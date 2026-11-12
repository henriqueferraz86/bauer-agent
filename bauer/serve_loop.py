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

import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable

logger = logging.getLogger("bauer.serve_loop")

#: mesmo texto do /loop da CLI (agent._LOOP_CONFIRM_NUDGE importa daqui).
#: Sprint 82: pede um veredito explícito. "O loop parou" não é "o objetivo foi
#: cumprido": sem a linha RESULTADO, um loop que encerrava dizendo "nenhuma
#: tarefa foi concluída" era registrado como concluído.
LOOP_CONFIRM_NUDGE = (
    "Você respondeu sem chamar nenhuma tool, o que normalmente indica que a "
    "tarefa terminou. Se ela está REALMENTE completa, responda apenas "
    "confirmando isso (sem chamar tools). Se ainda falta trabalho, continue "
    "chamando as tools necessárias. Se você não tem como continuar, explique "
    "o bloqueio. Termine a resposta com uma linha exata: "
    "`RESULTADO: CONCLUIDO` se o objetivo pedido foi cumprido e verificado, ou "
    "`RESULTADO: PENDENTE` se ele não foi cumprido por inteiro."
)

_VERDICT_RE = re.compile(
    r"RESULTADO\s*:\s*`?\s*(CONCLU[IÍ]D[OA]|PENDENTE)", re.IGNORECASE)


def loop_verdict(text: str) -> "str | None":
    """Veredito declarado pelo modelo: ``"done"``, ``"pending"`` ou ``None``.

    ``None`` = o modelo não escreveu a linha; quem chama mantém o
    comportamento antigo (conclusão natural). Vale a ÚLTIMA ocorrência.
    """
    found = _VERDICT_RE.findall(text or "")
    if not found:
        return None
    return "pending" if found[-1].upper() == "PENDENTE" else "done"


def strip_verdict(text: str) -> str:
    """Remove a linha RESULTADO do texto mostrado ao usuário."""
    lines = [ln for ln in (text or "").splitlines()
             if not _VERDICT_RE.fullmatch(ln.strip().strip("`*_ .")) ]
    return "\n".join(lines).strip()


#: teto duro de rodadas — backstop acima de qualquer orçamento (um loop
#: saudável termina por conclusão ou por budget muito antes disto).
MAX_ROUNDS_HARD_CAP = 100


@dataclass(frozen=True)
class ResolvedLoopLimits:
    """Limites efetivos de uma execução autônoma, já reconciliados entre o
    config (``loop:``) e os overrides do caller. Imutável de propósito — é o
    contrato que o motor e o banner consomem, um lugar só para a verdade."""

    # None = sem limite (sprint 79; `0` no config).
    max_minutes: int | None
    max_tool_calls: int | None
    max_cost_usd: float | None
    approval_mode: str = "threshold"
    max_rounds: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"max_minutes": self.max_minutes, "max_tool_calls": self.max_tool_calls,
                "max_cost_usd": self.max_cost_usd, "approval_mode": self.approval_mode,
                "max_rounds": self.max_rounds}

    def budget(self) -> Any:
        """AutonomousBudget deste loop (sem teto escondido de chamadas/tokens)."""
        from .autonomous_budget import loop_budget
        return loop_budget(max_minutes=self.max_minutes, max_tool_calls=self.max_tool_calls,
                           max_cost_usd=self.max_cost_usd)

    def banner_pt(self) -> str:
        """Frase única em PT com os guardrails — usada pela CLI e pela UI. O
        custo é ESTIMADO (depende de usage + tabela de preços); tempo e nº de
        tools são os guardrails primários."""
        if all(v is None for v in (self.max_minutes, self.max_tool_calls,
                                   self.max_cost_usd, self.max_rounds)):
            return ("sem limite de tempo, de ferramentas, de rodadas nem de custo — "
                    "roda até concluir ou até /cancelar")
        cost = "sem limite" if self.max_cost_usd is None else f"~US$ {self.max_cost_usd:.2f}"
        minutes = "sem limite" if self.max_minutes is None else f"{self.max_minutes} min"
        tools = "sem limite" if self.max_tool_calls is None else str(self.max_tool_calls)
        rounds = "" if self.max_rounds is None else f" OU {self.max_rounds} rodadas"
        return (
            f"tempo: {minutes} · ferramentas: {tools} · custo ESTIMADO: {cost}"
            f"{rounds} — o que vier primeiro"
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
    def _base(name: str, default: Any, kind: type) -> Any:
        raw = getattr(loop_section, name, default)
        if raw is None or kind(raw) == 0:
            return None  # 0 no config = sem limite (sprint 79)
        return kind(raw)

    base_minutes = _base("max_minutes", 30, int)
    base_tools = _base("max_tool_calls", 120, int)
    base_cost = _base("max_cost_usd", 2.0, float)
    base_rounds = _base("max_rounds", 0, int)
    approval = str(getattr(loop_section, "approval_mode", "threshold") or "threshold")

    ov = overrides or {}

    def _pick(key: str, base, floor, kind: type):
        raw = ov.get(key)
        if raw is None:
            return base
        val = kind(raw)
        if val <= floor and not (floor == 0.0 and key == "max_cost_usd"):
            raise ValueError(f"{key} deve ser > {floor}")
        if key == "max_cost_usd" and val < 0:
            raise ValueError("max_cost_usd não pode ser negativo")
        if not clamp_to_config or base is None:
            return val
        return min(val, base)

    minutes = _pick("max_minutes", base_minutes, 0, int)
    tools = _pick("max_tool_calls", base_tools, 0, int)
    cost = _pick("max_cost_usd", base_cost, -0.0000001, float)
    ov_approval = ov.get("approval_mode")
    if ov_approval is not None:
        if ov_approval not in ("threshold", "deny_all", "yolo"):
            raise ValueError(f"approval inválido: {ov_approval!r} (use threshold|deny_all|yolo)")
        approval = ov_approval

    return ResolvedLoopLimits(max_minutes=minutes, max_tool_calls=tools,
                              max_cost_usd=cost, approval_mode=approval,
                              max_rounds=base_rounds)


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
    activity: str = ""                # o que está fazendo AGORA (feedback ao vivo)
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


#: Esperas antes de repetir uma rodada que falhou por erro TRANSITÓRIO do
#: provider (sprint 78). Na Beelink, em 2026-11-11, um /loop pedido pelo
#: agente morreu em 2 s na 1ª rodada com `provider_error`; o mesmo loop,
#: reproduzido logo depois, rodou 40 rodadas sem erro.
ROUND_RETRY_DELAYS: tuple[float, ...] = (5.0, 15.0, 45.0)
_sleep = time.sleep


def _transient_provider_failure(exc: Exception) -> str | None:
    """Motivo, se a falha é passageira (limite de taxa, 5xx, timeout, queda)."""
    try:
        from .error_classifier import FailReason, classify_api_error
        reason = classify_api_error(exc).reason
    except Exception:  # noqa: BLE001 — sem classificação, não repete
        return None
    transient = {FailReason.RATE_LIMIT, FailReason.SERVER_ERROR,
                 FailReason.PROVIDER_DOWN, FailReason.TIMEOUT}
    return reason.value if reason in transient else None


def _wait_unless_stopped(delay: float, should_stop: "Callable[[], str | None] | None") -> str | None:
    """Espera ``delay`` segundos em passos curtos; devolve o motivo se pararam o loop."""
    deadline = time.monotonic() + delay
    while True:
        if should_stop is not None:
            external = should_stop()
            if external:
                return external
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        _sleep(min(1.0, remaining))


def run_loop_rounds(
    *,
    goal: str,
    ctx: Any,
    turn_fn: Callable[[], "tuple[str, list[dict]]"],
    budget: Any,
    should_stop: "Callable[[], str | None] | None" = None,
    on_round: "Callable[[int, str, list[dict]], None] | None" = None,
    confirm_nudge: str = LOOP_CONFIRM_NUDGE,
    max_rounds: int | None = MAX_ROUNDS_HARD_CAP,
) -> "tuple[str, int, str, list[dict]]":
    """Roda o laço de rodadas até concluir/orçamento/parada externa.

    Retorna ``(stop_reason, rounds, last_text, all_tool_log)``. Não toca em
    persistência nem telemetria — isso é papel do ``on_round``/caller (o
    motor fica puro e testável sem threads).
    """
    ctx.add_user(goal)

    round_num = 0
    retries = 0
    confirm_pending = False
    stop_reason = "completed"
    last_text = ""
    all_tool_log: list[dict] = []

    def preserve_tools(exc: BaseException) -> None:
        try:
            exc.partial_tool_log = [dict(entry) for entry in all_tool_log]  # type: ignore[attr-defined]
        except Exception as accounting_exc:
            from .logging_config import log_suppressed
            log_suppressed("serve_loop.preserve_tools", accounting_exc)

    def record_tools(tool_log: list[dict]) -> None:
        all_tool_log.extend(tool_log)
        try:
            for entry in tool_log:
                # Shared budgets reserve before dispatch; legacy turns need
                # accounting here, including tools completed before failure.
                if not entry.get("budget_accounted"):
                    budget.consume_tool_call()
        except Exception as exc:  # noqa: BLE001 — exhausted; the next round stops
            from .logging_config import log_suppressed
            log_suppressed("serve_loop.record_tools", exc)

    while True:
        if should_stop is not None:
            external = should_stop()
            if external:
                stop_reason = external
                break
        if getattr(budget, "is_exhausted", False):
            stop_reason = "budget_exhausted"
            break
        if max_rounds is not None and round_num >= max_rounds:
            stop_reason = "max_rounds"
            break

        round_num += 1
        try:
            text, tool_log = turn_fn()
        except BaseException as exc:
            record_tools(getattr(exc, "partial_tool_log", []))
            if not isinstance(exc, Exception):
                preserve_tools(exc)
                raise
            from .provider_retry import ProviderRetryInterrupted
            if isinstance(exc, ProviderRetryInterrupted):
                stop_reason = exc.reason
                if not last_text:
                    last_text = {
                        "budget_exhausted": "Execução interrompida pelo limite de segurança.",
                        "cancelled": "Execução cancelada pelo usuário.",
                        "kill_switch": "Execução interrompida pelo kill-switch.",
                    }[exc.reason]
                break
            from .privacy import redact_text
            detail = redact_text(f"{type(exc).__name__}: {exc}")[:500]
            transient = _transient_provider_failure(exc)
            if transient is not None and retries < len(ROUND_RETRY_DELAYS):
                delay = ROUND_RETRY_DELAYS[retries]
                retries += 1
                round_num -= 1  # a rodada que falhou não conta
                logger.warning(
                    "loop: rodada falhou por erro transitório do provider (%s); "
                    "tentativa %d/%d em %.0fs — %s",
                    transient, retries, len(ROUND_RETRY_DELAYS), delay, detail,
                )
                external = _wait_unless_stopped(delay, should_stop)
                if external:
                    stop_reason = external
                    break
                continue
            logger.warning("loop: parou por erro do provider — %s", detail)
            stop_reason = "provider_error"
            last_text = detail
            break
        retries = 0

        text = text or ""
        tool_log = tool_log or []
        record_tools(tool_log)
        if text.strip():
            last_text = text
        if on_round is not None:
            try:
                on_round(round_num, text, tool_log)
            except BaseException as exc:
                preserve_tools(exc)
                raise

        if not tool_log:
            if not text.strip():
                stop_reason = "empty_response"
                break
            # Sinal natural genuíno (mesma regra da CLI): rodada só-texto.
            if confirm_pending:
                # O modelo confirmou que parou; o veredito diz se cumpriu.
                stop_reason = ("objetivo_pendente"
                               if loop_verdict(text) == "pending" else "completed")
                last_text = strip_verdict(last_text) or last_text
                break
            confirm_pending = True
            ctx.add_user(confirm_nudge)
        else:
            confirm_pending = False

    return stop_reason, round_num, last_text, all_tool_log
