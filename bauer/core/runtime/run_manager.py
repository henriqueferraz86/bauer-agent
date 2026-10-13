"""Formal Run model and manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from ..events.bus import EventBus
from .state_store import RuntimeStateStore, SqliteStateStore

# Estados base (caminho legado: queued → running → completed) + estados do
# Kernel (created/planning/policy_check/evaluating/retrying/paused — só usados
# quando a execução passa pelo BauerKernel; ver core/kernel/states.py).
# EXTENSÃO ADITIVA: nenhum consumidor legado quebra — os terminais e o caminho
# queued→running→completed permanecem intocados.
RunStatus = Literal[
    "created", "planning", "policy_check", "queued", "running",
    "waiting_approval", "evaluating", "retrying", "paused",
    "completed", "failed", "cancelled",
]
RUN_STATUSES: tuple[str, ...] = (
    "created",
    "planning",
    "policy_check",
    "queued",
    "running",
    "waiting_approval",
    "evaluating",
    "retrying",
    "paused",
    "completed",
    "failed",
    "cancelled",
)
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
# Estados de espera INTENCIONAL — um run parado nesses estados não está
# "travado", está aguardando um humano (aprovação) ou foi pausado de propósito.
# O runtime recovery NÃO deve matá-los por idade (senão descarta aprovações
# pendentes e quebra o approve()/resume() posterior).
WAITING_RUN_STATUSES = {"waiting_approval", "paused"}
# Estados que o recovery PODE marcar como failed quando ficam velhos demais:
# tudo que não é terminal nem espera intencional.
RECOVERABLE_RUN_STATUSES = set(RUN_STATUSES) - TERMINAL_RUN_STATUSES - WAITING_RUN_STATUSES


@dataclass
class Run:
    id: str
    session_id: str
    agent_id: str
    runtime_adapter: str
    status: RunStatus
    input: dict[str, Any]
    output: dict[str, Any] | None = None
    error: str | None = None
    started_at: str = field(default_factory=lambda: _now_iso())
    finished_at: str | None = None
    cost_estimate: float | None = None
    tool_calls_count: int = 0
    usage_known: bool | None = None
    llm_calls_count: int = 0
    elapsed_seconds: float | None = None
    executor_elapsed_seconds: float | None = None
    validation_passed: bool | None = None
    validation_results: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = field(default_factory=lambda: _now_iso())


class RunManager:
    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        root: str | Path = "memory/runtime",
        event_bus: EventBus | None = None,
        agent_registry: Any | None = None,
    ):
        self.store = store or SqliteStateStore(root)
        self.event_bus = event_bus or EventBus(store=self.store)
        self.agent_registry = agent_registry

    def create_run(
        self,
        *,
        session_id: str,
        agent_id: str = "default",
        runtime_adapter: str = "bauer_native",
        input: dict[str, Any] | None = None,
        status: RunStatus = "queued",
    ) -> Run:
        run = Run(
            id=f"run-{uuid4()}",
            session_id=session_id,
            agent_id=agent_id,
            runtime_adapter=runtime_adapter,
            status=status,
            input=input or {},
        )
        self.store.upsert("runs", run)
        self.event_bus.publish(
            "run.created",
            run_id=run.id,
            session_id=run.session_id,
            agent_id=run.agent_id,
            status=run.status,
            data={"runtime_adapter": run.runtime_adapter, "input": run.input},
        )
        if run.status == "running":
            self._publish_status_event(run)
        return run

    def create_run_for_agent(
        self,
        *,
        agent_id: str,
        session_id: str,
        input: dict[str, Any] | None = None,
        version: str | None = None,
        status: RunStatus = "queued",
    ) -> Run:
        from .agent_registry import RuntimeAgentRegistry

        registry = self.agent_registry or RuntimeAgentRegistry()
        spec = registry.get(agent_id, version=version)
        if spec is None:
            raise KeyError(f"Agent not found: {agent_id}")
        payload = {
            **(input or {}),
            "agent_spec": spec.to_dict(),
            "agent_version": spec.version,
            "permissions": list(spec.permissions),
            "skills": list(spec.skills),
            "autonomy": dict(spec.autonomy),
            "limits": dict(spec.limits),
        }
        return self.create_run(
            session_id=session_id,
            agent_id=spec.id,
            runtime_adapter=spec.runtime_adapter,
            input=payload,
            status=status,
        )

    def get_run(self, run_id: str) -> Run | None:
        data = self.store.latest("runs", run_id)
        return Run(**data) if data else None

    def list_runs(self) -> list[Run]:
        return [Run(**item) for item in self.store.list_latest("runs")]

    def update_run(self, run_id: str, *, expected_status: str | None = None,
                   **changes: Any) -> Run:
        status = changes.get("status")
        if status is not None and status not in RUN_STATUSES:
            raise ValueError(f"Invalid run status: {status}")
        changes["updated_at"] = _now_iso()
        if status in TERMINAL_RUN_STATUSES:
            changes.setdefault("finished_at", _now_iso())
        maxima = {}
        if changes.get("elapsed_seconds") is not None:
            maxima["elapsed_seconds"] = max(0.0, float(changes.pop("elapsed_seconds")))
        data, previous_status, changed = self.store.patch_record(
            "runs", run_id, changes,
            forbidden_statuses=TERMINAL_RUN_STATUSES if status is not None else set(),
            expected_status=expected_status, maxima=maxima,
        )
        updated = Run(**data)
        if changed and updated.status != previous_status:
            self._publish_status_event(updated)
        return updated

    def start_run(self, run_id: str) -> Run:
        return self.update_run(run_id, status="running")

    def accumulate_metrics(self, run_id: str, *, increments: dict[str, float | int],
                           usage_known: bool | None = None) -> Run:
        """Add one executor attempt's usage without making a status decision."""
        data, _, _ = self.store.patch_record(
            "runs", run_id, {"updated_at": _now_iso()}, increments=increments,
            conjunctions={"usage_known": usage_known} if usage_known is not None else {},
        )
        run = Run(**data)
        if run.executor_elapsed_seconds is not None:
            run = self.update_run(run_id, elapsed_seconds=run.executor_elapsed_seconds)
        return run

    def complete_run(
        self,
        run_id: str,
        *,
        output: dict[str, Any] | None = None,
        tool_calls_count: int | None = None,
        cost_estimate: float | None = None,
        usage_known: bool | None = None,
        llm_calls_count: int | None = None,
        elapsed_seconds: float | None = None,
    ) -> Run:
        changes: dict[str, Any] = {"status": "completed", "output": output or {}}
        if tool_calls_count is not None:
            changes["tool_calls_count"] = tool_calls_count
        if cost_estimate is not None:
            changes["cost_estimate"] = cost_estimate
        if usage_known is not None:
            changes["usage_known"] = bool(usage_known)
        if llm_calls_count is not None:
            changes["llm_calls_count"] = max(0, int(llm_calls_count))
        if elapsed_seconds is not None:
            changes["elapsed_seconds"] = max(0.0, float(elapsed_seconds))
        return self.update_run(run_id, **changes)

    def fail_run(self, run_id: str, error: str) -> Run:
        return self.update_run(run_id, status="failed", error=error)

    def fail_run_se_nao_terminal(self, run_id: str, error: str) -> Run:
        """Compatibility alias for failure preserving the first terminal.

        All status changes now use the same transactional guard, including
        ``fail_run``. This name remains available to existing SSE/recovery callers.
        """
        return self.update_run(run_id, status="failed", error=error)

    def cancel_run(self, run_id: str) -> Run:
        return self.update_run(run_id, status="cancelled")

    def _publish_status_event(self, run: Run) -> None:
        event_type = {
            "running": "run.started",
            "completed": "run.completed",
            "failed": "run.failed",
            "cancelled": "run.cancelled",
        }.get(run.status)
        if event_type is None:
            return
        self.event_bus.publish(
            event_type,  # type: ignore[arg-type]
            run_id=run.id,
            session_id=run.session_id,
            agent_id=run.agent_id,
            status=run.status,
            message=run.error,
            data={
                "runtime_adapter": run.runtime_adapter,
                "tool_calls_count": run.tool_calls_count,
                "cost_estimate": run.cost_estimate,
            },
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
