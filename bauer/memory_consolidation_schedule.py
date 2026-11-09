"""Consolidação agendada, opt-in e governada (plano 065, sprint 67).

O agendamento reusa o cron durável (``AutomationStore``/``AutomationScheduler``
— ``bauer cron``): um job com ``metadata.kind = "memory_consolidation"`` não
vira task de Kanban com prompt para um LLM agente; o scheduler executa a
consolidação **dentro de** ``core.kernel.entry.run_governed`` (trabalho
autônomo: kill-switch central, policy e budget do Kernel no preflight).

Camadas de proteção, na ordem:

1. ``BAUER_MEMORY_CONSOLIDATION_DISABLED`` — kill-switch específico da
   feature (processo inteiro);
2. opt-in do workspace (``bauer memory consolidate enable``);
3. budget diário de tokens do workspace (ledger ``runs`` do dia);
4. Kernel: kill-switch do runtime, policy, budget/modo ``locked``;
5. no scheduler: backoff exponencial entre execuções agendadas que falharam.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .memory_consolidation import (
    ConsolidationLedger,
    ConsolidationSettings,
    LLMCallable,
    MemoryConsolidator,
)
from .memory_scope import resolve_workspace_scope

_log = logging.getLogger("bauer.memory_consolidation_schedule")

JOB_KIND = "memory_consolidation"
DEFAULT_JOB_NAME = "memory-consolidation"
KILL_SWITCH_ENV = "BAUER_MEMORY_CONSOLIDATION_DISABLED"
AGENT_ID = "memory.consolidation"
#: Backoff entre execuções agendadas com falha: 15min, 30min, 1h… até 6h.
BACKOFF_BASE_S = 15 * 60
BACKOFF_MAX_S = 6 * 3600


def kill_switch_engaged() -> bool:
    value = os.environ.get(KILL_SWITCH_ENV, "").strip().lower()
    return value not in {"", "0", "false", "no", "off"}


def backoff_seconds(consecutive_failures: int) -> int:
    if consecutive_failures <= 0:
        return 0
    return min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** (consecutive_failures - 1)))


def runtime_root_for(sessions_dir: str | Path) -> Path:
    """Mesmo runtime root dos canais (``channel_governance.runtime_root``)."""
    return Path(sessions_dir).resolve().parent / "runtime"


def schedule_consolidation(
    workspace: str | Path,
    *,
    sessions_dir: str | Path,
    schedule: str = "daily 03:00",
    name: str = DEFAULT_JOB_NAME,
) -> Any:
    """Cria o job durável no cron do workspace."""
    from .automation_store import AutomationStore

    scope = resolve_workspace_scope(workspace)
    if scope is None:
        raise ValueError("workspace inválido")
    return AutomationStore(scope.path).create_job(
        name=name,
        prompt="[interno] consolidação de memória do workspace (bauer memory consolidate)",
        schedule=schedule,
        metadata={
            "kind": JOB_KIND,
            "sessions_dir": str(Path(sessions_dir).resolve()),
            "consecutive_failures": 0,
        },
    )


def run_scheduled_consolidation(
    workspace: str | Path,
    *,
    sessions_dir: str | Path,
    llm: LLMCallable | None = None,
    kernel: Any = None,
    kernel_factory: Callable[[Path, Path], Any] | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Uma execução agendada. Nunca levanta; devolve status e métricas.

    ``status``: ``completed`` | ``partial`` | ``nothing_to_do`` | ``skipped``
    (``reason``: kill_switch_env, disabled, daily_budget, governance) |
    ``failed``.
    """
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    scope = resolve_workspace_scope(workspace)
    if scope is None:
        return {"status": "failed", "error": "workspace inválido"}
    if kill_switch_engaged():
        return {"status": "skipped", "reason": "kill_switch_env"}
    try:
        settings = ConsolidationSettings.load(scope)
    except Exception as exc:  # noqa: BLE001 — settings corrompido/confinamento
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    if not settings.enabled:
        return {"status": "skipped", "reason": "disabled"}
    ledger = ConsolidationLedger(scope)
    used_today = ledger.tokens_used_on(now.strftime("%Y-%m-%d"))
    remaining = settings.daily_token_budget - used_today
    if remaining <= 0:
        return {"status": "skipped", "reason": "daily_budget", "tokens_used_today": used_today}

    holder: dict[str, Any] = {}

    def _executor(payload: dict[str, Any]) -> dict[str, Any]:
        consolidator = MemoryConsolidator(
            scope.path, sessions_dir, llm=llm,
            **({"clock": clock} if clock is not None else {}),
        )
        report = consolidator.run(max_tokens=remaining, trigger="scheduled")
        holder["report"] = report
        summary = (
            f"memory consolidation {report.status}: sessions_ok={len(report.consolidated)} "
            f"failed={len(report.failed)} records={len(report.records)} "
            f"tokens={report.tokens_used}"
        )
        out: dict[str, Any] = {"output": summary, "tool_calls_count": 0, "cost_estimate": 0.0}
        if report.status == "failed":
            out.update(status="failed", error="; ".join(report.failed.values())[:500] or summary)
        return out

    from .core.kernel.entry import run_governed

    if kernel is None:
        root = runtime_root_for(sessions_dir)
        if kernel_factory is not None:
            kernel = kernel_factory(root, scope.root)
        else:
            from .core.kernel.kernel import build_kernel

            kernel = build_kernel(None, root=str(root), workspace=str(scope.root))
    try:
        gov = run_governed(
            kernel, _executor,
            agent_id=AGENT_ID,
            task="consolidação de memória do workspace",
            input={"workspace_id": scope.workspace_id, "kind": JOB_KIND},
            metadata={"kind": JOB_KIND, "workspace_id": scope.workspace_id},
            # Agendado: dispara sem humano e roda até acabar (governança total).
            autonomous=True,
        )
    except Exception as exc:  # noqa: BLE001 — scheduler nunca cai por um job
        _log.debug("scheduled consolidation crashed: %s", exc)
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    if gov.blocked_before_start:
        return {"status": "skipped", "reason": "governance",
                "detail": gov.error or gov.policy_reason or "", "kernel_run_id": gov.run_id}
    report = holder.get("report")
    result: dict[str, Any] = {
        "status": "completed" if gov.ok else "failed",
        "kernel_run_id": gov.run_id,
        "governed": gov.governed,
    }
    if report is not None:
        result.update(
            status=report.status if gov.ok else "failed",
            consolidation_run_id=report.run_id,
            sessions_ok=len(report.consolidated),
            sessions_failed=len(report.failed),
            records_written=len(report.records),
            tokens_used=report.tokens_used,
            budget_exhausted=report.budget_exhausted,
        )
    if not gov.ok:
        result["error"] = gov.error or "consolidação falhou"
    return result


def next_run_after_result(job: Any, due_at: str, result: dict[str, Any]) -> tuple[str, int]:
    """Próximo horário e contagem de falhas consecutivas (backoff)."""
    from .automation_store import next_after_run

    regular = next_after_run(job.schedule, due_at=due_at)
    failures = int((job.metadata or {}).get("consecutive_failures") or 0)
    if result.get("status") != "failed":
        return regular, 0
    failures += 1
    try:
        due = datetime.fromisoformat(due_at)
    except ValueError:
        due = datetime.now(timezone.utc)
    retry = due + timedelta(seconds=backoff_seconds(failures))
    try:
        regular_dt = datetime.fromisoformat(regular) if regular else None
    except ValueError:
        regular_dt = None  # horário regular ilegível: vale o retry com backoff
    if regular_dt is not None and regular_dt <= retry:
        return regular, failures
    return retry.isoformat(), failures
