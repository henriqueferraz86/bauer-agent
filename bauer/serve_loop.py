"""Modo autônomo no serve — o /loop da CLI disponível para a UI web.

Porta a LÓGICA DE RODADAS de ``agent._run_loop_mode`` (sinal natural de
conclusão + nudge de confirmação + orçamento de segurança) para fora do
console: a execução de cada rodada é INJETADA (``turn_fn``) — o serve usa o
mesmo motor do /chat e do /stream (``run_one_turn_with_fallback``); os testes
usam stubs. Paradas externas (kill-switch, run cancelado) são checadas entre
rodadas via ``should_stop`` — é assim que o botão "parar" da UI e o
``bauer runtime kill-switch on`` interrompem um loop em andamento.

Semântica de conclusão (idêntica à CLI): uma rodada que respondeu SÓ texto,
sem nenhuma tool call, é candidata a "terminei" — o loop injeta o nudge de
confirmação; uma SEGUNDA rodada só-texto consecutiva confirma e encerra.
Rodada com tools zera o candidato (o modelo ainda está trabalhando).
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

#: mesmo texto do /loop da CLI (agent._LOOP_CONFIRM_NUDGE) — mantido aqui para
#: não importar agent.py (pesado) só pela string.
LOOP_CONFIRM_NUDGE = (
    "Você respondeu sem chamar nenhuma tool, o que normalmente indica que a "
    "tarefa terminou. Se ela está REALMENTE completa, responda apenas "
    "confirmando isso (sem chamar tools). Se ainda falta trabalho, continue "
    "chamando as tools necessárias."
)

#: teto duro de rodadas — backstop acima de qualquer orçamento (um loop
#: saudável termina por conclusão ou por budget muito antes disto).
MAX_ROUNDS_HARD_CAP = 100


@dataclass(frozen=True)
class ResolvedLoopLimits:
    """Limites efetivos de uma execução autônoma, já reconciliados entre o
    config (``loop:``) e os overrides do caller. Imutável de propósito — é o
    contrato que o motor e o banner consomem, um lugar só para a verdade."""

    max_minutes: int
    max_tool_calls: int
    max_cost_usd: float
    approval_mode: str = "threshold"

    def to_dict(self) -> dict[str, Any]:
        return {"max_minutes": self.max_minutes, "max_tool_calls": self.max_tool_calls,
                "max_cost_usd": self.max_cost_usd, "approval_mode": self.approval_mode}

    def banner_pt(self) -> str:
        """Frase única em PT com os guardrails — usada pela CLI e pela UI. O
        custo é ESTIMADO (depende de usage + tabela de preços); tempo e nº de
        tools são os guardrails primários."""
        return (
            f"trabalho até {self.max_minutes} min OU {self.max_tool_calls} "
            f"chamadas de ferramenta OU ~US$ {self.max_cost_usd:.2f} de custo "
            f"ESTIMADO — o que vier primeiro"
        )


def resolve_loop_limits(loop_section: Any, overrides: "dict[str, Any] | None" = None,
                        *, clamp_to_config: bool) -> ResolvedLoopLimits:
    """Reconcilia os limites do ``loop:`` do config com os ``overrides``.

    ``clamp_to_config``:
      * ``False`` (CLI / ``bauer run``): o override SUBSTITUI o config —
        você é o dono da máquina, pode pedir mais.
      * ``True`` (HTTP / UI web): o override só REDUZ o teto — um cliente
        remoto nunca amplia o blast radius definido no servidor.

    ``overrides`` aceita as chaves max_minutes / max_tool_calls / max_cost_usd /
    approval_mode; valores None/ausentes herdam o config. Valida positividade.
    """
    base_minutes = int(getattr(loop_section, "max_minutes", 30) or 30)
    base_tools = int(getattr(loop_section, "max_tool_calls", 120) or 120)
    base_cost = float(getattr(loop_section, "max_cost_usd", 2.0) or 2.0)
    approval = str(getattr(loop_section, "approval_mode", "threshold") or "threshold")

    ov = overrides or {}

    def _pick(key: str, base, floor):
        raw = ov.get(key)
        if raw is None:
            return base
        val = type(base)(raw)
        if val <= floor and not (floor == 0.0 and key == "max_cost_usd"):
            raise ValueError(f"{key} deve ser > {floor}")
        if key == "max_cost_usd" and val < 0:
            raise ValueError("max_cost_usd não pode ser negativo")
        return min(val, base) if clamp_to_config else val

    minutes = _pick("max_minutes", base_minutes, 0)
    tools = _pick("max_tool_calls", base_tools, 0)
    cost = _pick("max_cost_usd", base_cost, -0.0000001)
    ov_approval = ov.get("approval_mode")
    if ov_approval is not None:
        if ov_approval not in ("threshold", "deny_all", "yolo"):
            raise ValueError(f"approval inválido: {ov_approval!r} (use threshold|deny_all|yolo)")
        approval = ov_approval

    return ResolvedLoopLimits(max_minutes=minutes, max_tool_calls=tools,
                              max_cost_usd=cost, approval_mode=approval)


@dataclass
class LoopState:
    """Estado observável de um loop (o que a UI mostra no card)."""

    run_id: str
    session_id: str
    goal: str
    state: str = "running"            # running | completed | stopped | failed
    rounds: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    stop_reason: str | None = None
    last_text: str = ""
    limits: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LoopRegistry:
    """Loops do processo do serve (thread-safe). Perde-se em restart — o Run
    persistido no RunManager é a fonte de verdade durável; isto é o retrato
    vivo p/ a UI (rodada corrente, custo parcial, último texto)."""

    def __init__(self) -> None:
        self._items: dict[str, LoopState] = {}
        self._lock = threading.Lock()

    def put(self, state: LoopState) -> None:
        with self._lock:
            self._items[state.run_id] = state

    def get(self, run_id: str) -> LoopState | None:
        with self._lock:
            return self._items.get(run_id)

    def list(self) -> list[LoopState]:
        with self._lock:
            return list(self._items.values())


_REGISTRY = LoopRegistry()


def loop_registry() -> LoopRegistry:
    return _REGISTRY


def run_loop_rounds(
    *,
    goal: str,
    ctx: Any,
    turn_fn: Callable[[], "tuple[str, list[dict]]"],
    budget: Any,
    should_stop: "Callable[[], str | None] | None" = None,
    on_round: "Callable[[int, str, list[dict]], None] | None" = None,
    confirm_nudge: str = LOOP_CONFIRM_NUDGE,
    max_rounds: int = MAX_ROUNDS_HARD_CAP,
) -> "tuple[str, int, str, list[dict]]":
    """Roda o laço de rodadas até concluir/orçamento/parada externa.

    Retorna ``(stop_reason, rounds, last_text, all_tool_log)``. Não toca em
    persistência nem telemetria — isso é papel do ``on_round``/caller (o
    motor fica puro e testável sem threads).
    """
    ctx.add_user(goal)

    round_num = 0
    confirm_pending = False
    stop_reason = "completed"
    last_text = ""
    all_tool_log: list[dict] = []

    while True:
        if should_stop is not None:
            external = should_stop()
            if external:
                stop_reason = external
                break
        if getattr(budget, "is_exhausted", False):
            stop_reason = "budget_exhausted"
            break
        if round_num >= max_rounds:
            stop_reason = "max_rounds"
            break

        round_num += 1
        try:
            text, tool_log = turn_fn()
        except Exception as exc:  # noqa: BLE001 — o loop para; o run registra o porquê
            stop_reason = "provider_error"
            last_text = str(exc)
            break

        text = text or ""
        tool_log = tool_log or []
        all_tool_log.extend(tool_log)
        if text.strip():
            last_text = text
        try:
            for _ in tool_log:
                budget.consume_tool_call()
        except Exception:  # noqa: BLE001 — esgotou no meio: o topo do laço encerra
            pass

        if on_round is not None:
            on_round(round_num, text, tool_log)

        if not tool_log:
            if not text.strip():
                stop_reason = "empty_response"
                break
            # Sinal natural genuíno (mesma regra da CLI): rodada só-texto.
            if confirm_pending:
                stop_reason = "completed"
                break
            confirm_pending = True
            ctx.add_user(confirm_nudge)
        else:
            confirm_pending = False

    return stop_reason, round_num, last_text, all_tool_log
