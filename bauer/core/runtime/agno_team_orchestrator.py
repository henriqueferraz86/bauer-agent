"""Kernel governed orchestration for Bauer teams backed by Agno Team."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any

from ..kernel.entry import GovernedResult, run_governed
from ..policy import PolicyEngine
from ..events import EventBus
from .agent_registry import RuntimeAgentRegistry
from .team_registry import TeamRegistry, TeamSpec, DelegationManager


class TeamOrchestrationError(RuntimeError):
    """Raised when a formal team cannot be admitted or executed."""


class AgnoTeamOrchestrator:
    """Coordinates a formal Bauer team through the Kernel and Agno adapter.

    Bauer remains the source of truth for admission, policy, lifecycle, events,
    budgets and persisted runs.  Agno receives only the normalized team and
    member specs after the Kernel has admitted the run.
    """

    _semaphore_lock = Lock()
    _semaphores: dict[str, BoundedSemaphore] = {}

    def __init__(
        self,
        *,
        kernel: Any | None,
        config: Any | None = None,
        root: str | Path = "memory/runtime",
        team_registry: TeamRegistry | None = None,
        agent_registry: RuntimeAgentRegistry | None = None,
        adapter: Any | None = None,
        event_bus: EventBus | None = None,
        policy_engine: PolicyEngine | None = None,
    ) -> None:
        self.kernel = kernel
        self.config = config
        self.root = Path(root)
        self.agent_registry = agent_registry or RuntimeAgentRegistry()
        self.team_registry = team_registry or TeamRegistry(agent_registry=self.agent_registry)
        self.event_bus = event_bus or getattr(kernel, "bus", None) or EventBus(root=self.root)
        self.policy_engine = policy_engine or getattr(kernel, "policy", None)
        self.adapter = adapter
        self.delegations = DelegationManager(
            root=self.root,
            team_registry=self.team_registry,
            agent_registry=self.agent_registry,
            run_manager=getattr(kernel, "runs", None),
            policy_engine=self.policy_engine,
            event_bus=self.event_bus,
        )

    def run(
        self,
        team_id: str,
        task: str,
        *,
        session_id: str | None = None,
        user_id: str = "local-user",
        estimated_cost_usd: float = 0.0,
        autonomous: bool = False,
    ) -> GovernedResult:
        team = self._require_team(team_id)
        supervisor_id = self._supervisor(team)
        self._authorize(team, supervisor_id, estimated_cost_usd)
        semaphore = self._semaphore_for(team)
        if not semaphore.acquire(blocking=False):
            raise TeamOrchestrationError(
                f"team concurrency limit reached: {team.id} "
                f"max={self._max_parallel(team)}"
            )

        self.event_bus.publish(
            "team.run.requested",
            agent_id=supervisor_id,
            session_id=session_id,
            status="requested",
            message=f"team {team.id} requested",
            data={"team_id": team.id, "members": team.agents},
        )
        try:
            result = run_governed(
                self.kernel,
                lambda payload: self._execute(team, payload, user_id=user_id),
                agent_id=supervisor_id,
                task=task,
                input={
                    "team_id": team.id,
                    "session_id": session_id or "",
                    "user_id": user_id,
                    "estimated_cost_usd": float(max(0.0, estimated_cost_usd)),
                },
                session_id=session_id,
                runtime_adapter="agno",
                operation="runtime.execute",
                autonomous=autonomous,
                metadata={"team_id": team.id, "orchestrator": "agno"},
            )
        finally:
            semaphore.release()

        self.event_bus.publish(
            "team.run.completed" if result.ok else "team.run.failed",
            run_id=result.run_id,
            agent_id=supervisor_id,
            session_id=session_id,
            status=result.status,
            message=f"team {team.id} {result.status}",
            data={"team_id": team.id, "governed": result.governed},
        )
        return result

    def stream(
        self,
        team_id: str,
        task: str,
        *,
        session_id: str | None = None,
        user_id: str = "local-user",
    ) -> Iterator[dict[str, Any]]:
        """Stream a team through the Kernel's streaming path."""
        team = self._require_team(team_id)
        supervisor_id = self._supervisor(team)
        self._authorize(team, supervisor_id, 0.0)
        semaphore = self._semaphore_for(team)
        if not semaphore.acquire(blocking=False):
            raise TeamOrchestrationError(
                f"team concurrency limit reached: {team.id} max={self._max_parallel(team)}"
            )
        try:
            from ..kernel.schemas import KernelRequest

            request = KernelRequest(
                task=task,
                agent_id=supervisor_id,
                runtime_adapter="agno",
                session_id=session_id or "",
                input={"team_id": team.id, "user_id": user_id},
                metadata={"team_id": team.id, "orchestrator": "agno"},
            )
            for event in self.kernel.stream(request, executor=lambda payload: self._stream_execute(team, payload, user_id=user_id)):
                yield event
        finally:
            semaphore.release()

    def _execute(self, team: TeamSpec, payload: dict[str, Any], *, user_id: str) -> dict[str, Any]:
        adapter = self._adapter()
        request = self._adapter_request(team, payload, user_id=user_id)
        result = dict(adapter.run_team(request))
        if result.get("status") == "failed" or result.get("event") == "run.failed":
            return result
        cost = float(result.get("cost_usd") or result.get("cost_estimate") or payload.get("estimated_cost_usd") or 0.0)
        run_id = str(payload.get("run_id") or result.get("run_id") or "")
        if run_id:
            self.delegations.record_team_cost(
                team_id=team.id,
                run_id=run_id,
                agent_id=self._supervisor(team),
                cost_usd=cost,
            )
        return {**result, "cost_estimate": cost, "team_id": team.id}

    def _stream_execute(self, team: TeamSpec, payload: dict[str, Any], *, user_id: str) -> Iterator[dict[str, Any]]:
        adapter = self._adapter()
        yield from adapter.stream_team(self._adapter_request(team, payload, user_id=user_id))

    def _adapter_request(self, team: TeamSpec, payload: dict[str, Any], *, user_id: str) -> dict[str, Any]:
        members = []
        for agent_id in team.agents:
            spec = self.agent_registry.get(agent_id)
            if spec is None:  # registry validation should make this unreachable
                raise TeamOrchestrationError(f"team member not found: {agent_id}")
            members.append(spec.to_dict())
        supervisor_id = self._supervisor(team)
        supervisor = self.agent_registry.get(supervisor_id)
        if supervisor is None:
            raise TeamOrchestrationError(f"team supervisor not found: {supervisor_id}")
        coordination = team.coordination or {}
        return {
            "run_id": payload.get("run_id"),
            "team_id": team.id,
            "task": payload.get("task") or payload.get("input", ""),
            "session_id": payload.get("session_id") or f"team-session-{team.id}",
            "user_id": user_id,
            "team_spec": {
                **team.to_dict(),
                "mode": "coordinate" if str(coordination.get("mode") or "supervisor") == "supervisor" else coordination.get("mode"),
                "max_iterations": coordination.get("max_iterations") or (team.limits or {}).get("max_iterations", 10),
            },
            "agent_specs": members,
            "supervisor_spec": supervisor.to_dict(),
        }

    def _adapter(self) -> Any:
        if self.adapter is None:
            from .adapters import get_runtime_adapter

            self.adapter = get_runtime_adapter("agno", config=self.config)
        return self.adapter

    def _authorize(self, team: TeamSpec, supervisor_id: str, estimated_cost_usd: float) -> None:
        status = self.delegations.team_budget_status(team.id)
        limit = status.get("limit_usd")
        estimated = max(0.0, float(estimated_cost_usd))
        if limit is not None and float(status.get("used_usd") or 0.0) + estimated > float(limit):
            raise TeamOrchestrationError(
                f"team budget exceeded: used=${float(status.get('used_usd') or 0):.4f} "
                f"limit=${float(limit):.4f}"
            )
        if supervisor_id not in team.agents:
            raise TeamOrchestrationError("team supervisor is not a member")
        if self.policy_engine is not None:
            decision = self.policy_engine.evaluate(
                "agent.delegate",
                {"team_id": team.id, "from_agent_id": supervisor_id, "estimated_cost_usd": estimated},
            )
            if decision.action != "allow":
                raise TeamOrchestrationError(decision.reason)

    @staticmethod
    def _require_team_from(registry: TeamRegistry, team_id: str) -> TeamSpec:
        team = registry.get(team_id)
        if team is None:
            raise TeamOrchestrationError(f"team not found: {team_id}")
        return team

    def _require_team(self, team_id: str) -> TeamSpec:
        return self._require_team_from(self.team_registry, team_id)

    @staticmethod
    def _supervisor(team: TeamSpec) -> str:
        return str((team.coordination or {}).get("supervisor") or team.agents[0])

    @staticmethod
    def _max_parallel(team: TeamSpec) -> int:
        try:
            return max(1, int((team.limits or {}).get("max_parallel_runs") or 1))
        except (TypeError, ValueError):
            return 1

    @classmethod
    def _semaphore_for(cls, team: TeamSpec) -> BoundedSemaphore:
        with cls._semaphore_lock:
            semaphore = cls._semaphores.get(team.id)
            if semaphore is None:
                semaphore = BoundedSemaphore(cls._max_parallel(team))
                cls._semaphores[team.id] = semaphore
            return semaphore
