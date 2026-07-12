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
