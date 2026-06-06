"""Agent lane/capability resolution for the durable dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .agent_registry import AgentDef, AgentRegistry
from .workspace_manager import Task


UNLIMITED_LANE_CONCURRENCY = 1_000_000


@dataclass(frozen=True)
class AgentLaneSelection:
    lane: str = "default"
    agent: str = ""
    capability: str = ""
    max_concurrent: int = UNLIMITED_LANE_CONCURRENCY
    priority_weight: int = 1
    configured: bool = False


def resolve_agent_lane(
    task: Task,
    *,
    workspace: str | Path = "workspace",
    registry: AgentRegistry | None = None,
) -> AgentLaneSelection:
    """Resolve the operational lane for a task without changing the task."""
    explicit_lane = task.metadata.get("lane", "").strip()
    explicit_agent = task.metadata.get("agent", "").strip() or task.assignee.strip()
    capability = task.metadata.get("capability", "").strip()
    agents = _load_agents(workspace, registry)
    by_name = {agent.name: agent for agent in agents}

    if explicit_lane:
        matched = by_name.get(explicit_agent) or _match_capability(agents, capability)
        return AgentLaneSelection(
            lane=explicit_lane,
            agent=explicit_agent or (matched.name if matched else ""),
            capability=capability,
            max_concurrent=_agent_capacity(matched) if matched else UNLIMITED_LANE_CONCURRENCY,
            priority_weight=_agent_weight(matched),
            configured=matched is not None,
        )

    if explicit_agent and explicit_agent in by_name:
        agent = by_name[explicit_agent]
        return AgentLaneSelection(
            lane=agent.lane or agent.name,
            agent=agent.name,
            capability=capability,
            max_concurrent=_agent_capacity(agent),
            priority_weight=_agent_weight(agent),
            configured=True,
        )

    matched = _match_capability(agents, capability)
    if matched is not None:
        return AgentLaneSelection(
            lane=matched.lane or matched.name,
            agent=matched.name,
            capability=capability,
            max_concurrent=_agent_capacity(matched),
            priority_weight=_agent_weight(matched),
            configured=True,
        )

    if explicit_agent:
        return AgentLaneSelection(
            lane=explicit_agent,
            agent=explicit_agent,
            capability=capability,
        )

    return AgentLaneSelection(capability=capability)


def _load_agents(workspace: str | Path, registry: AgentRegistry | None) -> list[AgentDef]:
    if registry is not None:
        return registry.list_agents()

    workspace_path = Path(workspace)
    candidates = [workspace_path / "agents.yaml", Path("agents.yaml")]
    for candidate in candidates:
        if candidate.exists():
            return AgentRegistry(candidate).list_agents()
    return []


def _match_capability(agents: list[AgentDef], capability: str) -> AgentDef | None:
    if not capability:
        return None
    wanted = capability.strip().lower()
    for agent in agents:
        if wanted in {item.strip().lower() for item in agent.capabilities}:
            return agent
    return None


def _agent_capacity(agent: AgentDef | None) -> int:
    if agent is None:
        return UNLIMITED_LANE_CONCURRENCY
    return max(1, int(agent.max_concurrent or 1))


def _agent_weight(agent: AgentDef | None) -> int:
    if agent is None:
        return 1
    return max(1, int(agent.priority_weight or 1))
