"""Formal Run model and manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from ..events.bus import EventBus
from .state_store import JsonlStateStore

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
    updated_at: str = field(default_factory=lambda: _now_iso())


class RunManager:
    def __init__(
        self,
        store: JsonlStateStore | None = None,
        root: str | Path = "memory/runtime",
        event_bus: EventBus | None = None,
        agent_registry: Any | None = None,
    ):
        self.store = store or JsonlStateStore(root)
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

    def update_run(self, run_id: str, **changes: Any) -> Run:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"Run not found: {run_id}")
        data = run.__dict__.copy()
        data.update(changes)
        data["updated_at"] = _now_iso()
        status = data.get("status")
        if status not in RUN_STATUSES:
            raise ValueError(f"Invalid run status: {status}")
        if status in TERMINAL_RUN_STATUSES and not data.get("finished_at"):
            data["finished_at"] = _now_iso()
        updated = Run(**data)
        self.store.upsert("runs", updated)
        self._publish_status_event(updated)
        return updated

    def start_run(self, run_id: str) -> Run:
        return self.update_run(run_id, status="running")

    def complete_run(
        self,
        run_id: str,
        *,
        output: dict[str, Any] | None = None,
        tool_calls_count: int | None = None,
        cost_estimate: float | None = None,
    ) -> Run:
        changes: dict[str, Any] = {"status": "completed", "output": output or {}}
        if tool_calls_count is not None:
            changes["tool_calls_count"] = tool_calls_count
        if cost_estimate is not None:
            changes["cost_estimate"] = cost_estimate
        return self.update_run(run_id, **changes)

    def fail_run(self, run_id: str, error: str) -> Run:
        return self.update_run(run_id, status="failed", error=error)

    def cancel_run(self, run_id: str) -> Run:
        run = self.get_run(run_id)
        if run is None:
            raise KeyError(f"Run not found: {run_id}")
        if run.status in TERMINAL_RUN_STATUSES:
            return run
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
