"""Formal team specs and governed delegation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from ..events import EventBus
from ..policy import PolicyEngine
from .agent_registry import RuntimeAgentRegistry
from .run_manager import Run, RunManager
from .state_store import JsonlStateStore


class TeamRegistryError(ValueError):
    pass


class DelegationDenied(RuntimeError):
    pass


@dataclass(slots=True)
class TeamSpec:
    id: str
    name: str
    agents: list[str]
    coordination: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    policies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "agents": list(self.agents),
            "coordination": dict(self.coordination),
            "limits": dict(self.limits),
            "policies": list(self.policies),
        }


@dataclass(slots=True)
class DelegationRecord:
    id: str
    team_id: str
    from_agent_id: str
    to_agent_id: str
    status: str
    reason: str | None = None
    run_id: str | None = None
    created_at: str = field(default_factory=lambda: _now_iso())
    input: dict[str, Any] = field(default_factory=dict)


class TeamRegistry:
    def __init__(self, roots: list[str | Path] | None = None, agent_registry: RuntimeAgentRegistry | None = None):
        formal_root = Path(__file__).resolve().parents[2] / "data" / "team_specs"
        self.roots = [Path(root) for root in (roots or [formal_root])]
        self.agent_registry = agent_registry or RuntimeAgentRegistry()

    def list(self) -> list[TeamSpec]:
        teams: dict[str, TeamSpec] = {}
        for path in self._spec_paths():
            team = team_spec_from_mapping(self._read(path))
            self._validate(team, path)
            teams[team.id] = team
        return sorted(teams.values(), key=lambda item: item.id)

    def get(self, team_id: str) -> TeamSpec | None:
        for team in self.list():
            if team.id == team_id or team.name == team_id:
                return team
        return None

    def _spec_paths(self) -> list[Path]:
        paths: list[Path] = []
        for root in self.roots:
            if root.is_file():
                paths.append(root)
                continue
            if not root.exists():
                continue
            paths.extend(sorted(root.rglob("team.yaml")))
            paths.extend(sorted(path for path in root.rglob("*.yaml") if path.name != "team.yaml"))
        return paths

    def _read(self, path: Path) -> dict[str, Any]:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise TeamRegistryError(f"{path}: team spec must be a mapping")
        return raw

    def _validate(self, team: TeamSpec, path: Path) -> None:
        if not team.id:
            raise TeamRegistryError(f"{path}: id is required")
        if not team.name:
            raise TeamRegistryError(f"{path}: name is required")
        if not team.agents:
            raise TeamRegistryError(f"{path}: agents must not be empty")
        missing = [agent_id for agent_id in team.agents if self.agent_registry.get(agent_id) is None]
        if missing:
            raise TeamRegistryError(f"{path}: unknown agents: {', '.join(missing)}")
        mode = str(team.coordination.get("mode") or "supervisor")
        supervisor = str(team.coordination.get("supervisor") or "")
        if mode == "supervisor" and supervisor and supervisor not in team.agents:
            raise TeamRegistryError(f"{path}: supervisor must be a team agent")


class DelegationManager:
    def __init__(
        self,
        *,
        root: str | Path = "memory/runtime",
        team_registry: TeamRegistry | None = None,
        agent_registry: RuntimeAgentRegistry | None = None,
        run_manager: RunManager | None = None,
        policy_engine: PolicyEngine | None = None,
        event_bus: EventBus | None = None,
    ):
        self.store = JsonlStateStore(root)
        self.event_bus = event_bus or EventBus(store=self.store)
        self.agent_registry = agent_registry or RuntimeAgentRegistry()
        self.team_registry = team_registry or TeamRegistry(agent_registry=self.agent_registry)
        self.run_manager = run_manager or RunManager(store=self.store, event_bus=self.event_bus, agent_registry=self.agent_registry)
        self.policy_engine = policy_engine or PolicyEngine(runtime_root=root)

    def delegate(
        self,
        *,
        team_id: str,
        from_agent_id: str,
        to_agent_id: str,
        input: dict[str, Any] | None = None,
        estimated_cost_usd: float = 0.0,
        session_id: str | None = None,
    ) -> DelegationRecord:
        payload = input or {}
        team = self.team_registry.get(team_id)
        if team is None:
            return self._deny(team_id, from_agent_id, to_agent_id, "team not found", payload)
        self.event_bus.publish(
            "delegation.requested",
            agent_id=from_agent_id,
            status="requested",
            message=f"{from_agent_id} -> {to_agent_id}",
            data={"team_id": team.id, "from_agent_id": from_agent_id, "to_agent_id": to_agent_id, "input": payload},
        )
        reason = self._validate_delegation(team, from_agent_id, to_agent_id, estimated_cost_usd)
        if reason:
            return self._deny(team.id, from_agent_id, to_agent_id, reason, payload)
        decision = self.policy_engine.evaluate(
            "agent.delegate",
            {
                "team_id": team.id,
                "from_agent_id": from_agent_id,
                "to_agent_id": to_agent_id,
                "estimated_cost_usd": estimated_cost_usd,
            },
        )
        self.event_bus.publish(
            "policy.evaluated",
            agent_id=from_agent_id,
            status=decision.action,
            message=decision.reason,
            data={"operation": "agent.delegate", "team_id": team.id, "risk_level": decision.risk_level},
        )
        if decision.action != "allow":
            return self._deny(team.id, from_agent_id, to_agent_id, decision.reason, payload)
        run = self.run_manager.create_run_for_agent(
            agent_id=to_agent_id,
            session_id=session_id or f"delegation-{uuid4()}",
            input={
                **payload,
                "delegated_by": from_agent_id,
                "team_id": team.id,
                "estimated_cost_usd": estimated_cost_usd,
            },
        )
        record = DelegationRecord(
            id=f"deleg-{uuid4()}",
            team_id=team.id,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            status="accepted",
            run_id=run.id,
            input=payload,
        )
        self.store.append("delegations", asdict(record))
        self.event_bus.publish(
            "delegation.accepted",
            run_id=run.id,
            agent_id=from_agent_id,
            status="accepted",
            message=f"delegated to {to_agent_id}",
            data={"delegation_id": record.id, "team_id": team.id, "to_agent_id": to_agent_id},
        )
        return record

    def record_team_cost(self, *, team_id: str, run_id: str, agent_id: str, cost_usd: float) -> dict[str, Any]:
        record = {
            "id": f"team-cost-{run_id}",
            "team_id": team_id,
            "run_id": run_id,
            "agent_id": agent_id,
            "cost_usd": max(0.0, float(cost_usd)),
            "timestamp": _now_iso(),
        }
        self.store.append("team_costs", record)
        return record

    def team_budget_status(self, team_id: str) -> dict[str, Any]:
        team = self.team_registry.get(team_id)
        limit = None if team is None else team.limits.get("max_daily_budget_usd")
        used = self._team_used_today(team_id)
        return {
            "team_id": team_id,
            "limit_usd": None if limit is None else float(limit),
            "used_usd": round(used, 6),
            "remaining_usd": None if limit is None else round(max(0.0, float(limit) - used), 6),
            "exceeded": False if limit is None else used >= float(limit),
        }

    def list_delegations(self) -> list[DelegationRecord]:
        return [DelegationRecord(**record) for record in self.store.list("delegations")]

    def _validate_delegation(
        self,
        team: TeamSpec,
        from_agent_id: str,
        to_agent_id: str,
        estimated_cost_usd: float,
    ) -> str | None:
        if from_agent_id not in team.agents:
            return "delegating agent is not part of team"
        if to_agent_id not in team.agents:
            return "target agent is not part of team"
        coordination = team.coordination or {}
        if coordination.get("mode", "supervisor") == "supervisor":
            supervisor = str(coordination.get("supervisor") or "")
            if supervisor and from_agent_id != supervisor:
                return f"only supervisor {supervisor} can delegate"
        limit = team.limits.get("max_daily_budget_usd")
        if limit is not None and self._team_used_today(team.id) + float(estimated_cost_usd) > float(limit):
            return f"team budget exceeded: used=${self._team_used_today(team.id):.4f} limit=${float(limit):.4f}"
        return None

    def _deny(
        self,
        team_id: str,
        from_agent_id: str,
        to_agent_id: str,
        reason: str,
        payload: dict[str, Any],
    ) -> DelegationRecord:
        record = DelegationRecord(
            id=f"deleg-{uuid4()}",
            team_id=team_id,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            status="denied",
            reason=reason,
            input=payload,
        )
        self.store.append("delegations", asdict(record))
        self.event_bus.publish(
            "delegation.denied",
            agent_id=from_agent_id,
            status="denied",
            message=reason,
            data={"delegation_id": record.id, "team_id": team_id, "to_agent_id": to_agent_id},
        )
        return record

    def _team_used_today(self, team_id: str) -> float:
        since = datetime.now(UTC) - timedelta(days=1)
        total = 0.0
        for record in self.store.list("team_costs"):
            if record.get("team_id") != team_id:
                continue
            try:
                timestamp = datetime.fromisoformat(str(record.get("timestamp", "")).replace("Z", "+00:00"))
            except ValueError:
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            if timestamp >= since:
                total += float(record.get("cost_usd", 0) or 0)
        return total


def team_spec_from_mapping(raw: dict[str, Any]) -> TeamSpec:
    return TeamSpec(
        id=str(raw.get("id") or "").strip(),
        name=str(raw.get("name") or "").strip(),
        agents=_str_list(raw.get("agents")),
        coordination=dict(raw.get("coordination") or {}) if isinstance(raw.get("coordination"), dict) else {},
        limits=dict(raw.get("limits") or {}) if isinstance(raw.get("limits"), dict) else {},
        policies=_str_list(raw.get("policies")),
    )


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
