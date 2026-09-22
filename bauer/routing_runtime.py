"""Primitivas compartilhadas do roteamento por turno."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .decision_router import RouteDecision, decide_with_fallback

_DECISION_TOOLS = {
    "read_file", "list_dir", "search_text", "glob_files", "diff_files",
    "write_file", "patch", "run_command", "web_search", "web_fetch",
    "browser_navigate", "browser_snapshot", "memory", "delegate_task",
    "kanban_list", "kanban_show", "lsp", "transcribe_audio",
}


def decide_route(
    message: str,
    profiles: dict[str, Any],
    config: Any,
    *,
    workspace: str | Path | None = None,
    session_id: str | None = None,
    decision_memory: Any | None = None,
) -> RouteDecision:
    """Calcula uma rota usando Jev quando ativo e fallback local quando necessário."""
    teams, agents, tools = decision_catalog(workspace)
    memory = decision_memory
    if memory is None and workspace is not None:
        try:
            from .decision_memory import DecisionMemory
            memory = DecisionMemory(db_path=Path(workspace) / "decisions.db")
        except Exception:
            memory = None
    return decide_with_fallback(
        message,
        profiles,
        config,
        teams=teams,
        agents=agents,
        available_tools=tools,
        decision_memory=memory,
        session_id=session_id,
    )


def decision_catalog(workspace: str | Path | None = None) -> tuple[list[str], list[str], list[str]]:
    """Retorna catálogo de times, agents e tools permitido ao decisor."""
    try:
        from .core.runtime.agent_registry import RuntimeAgentRegistry
        from .core.runtime.team_registry import TeamRegistry

        roots: list[str | Path] = []
        if workspace is not None:
            roots.append(Path(workspace) / "agents.yaml")
        agents_registry = RuntimeAgentRegistry(roots=roots or None)
        specs = agents_registry.list()
        team_roots: list[str | Path] = []
        if workspace is not None:
            team_roots.append(Path(workspace) / "teams")
        teams_registry = TeamRegistry(roots=team_roots or None, agent_registry=agents_registry)
        tools = sorted(_DECISION_TOOLS | {tool for spec in specs for tool in spec.tools})
        return (
            [team.id for team in teams_registry.list()],
            [spec.id for spec in specs],
            tools,
        )
    except Exception:
        return [], [], []


def route_event_data(decision: RouteDecision) -> dict[str, Any]:
    """Normaliza o payload público de ``model.route.selected``."""
    data: dict[str, Any] = {
        "task_type": decision.task_type,
        "complexity": decision.complexity,
        "tier": decision.profile,
        "provider": decision.provider,
        "model": decision.model,
        "source": decision.source,
        "confidence": decision.confidence,
        "orchestrate": decision.orchestrate,
    }
    if hasattr(decision, "probabilities"):
        data.update(
            {
                "probabilities": dict(getattr(decision, "probabilities", {}) or {}),
                "alternatives": [
                    {
                        "option": item.option,
                        "probability": item.probability,
                        "rationale": item.rationale,
                    }
                    if hasattr(item, "option")
                    else dict(item)
                    for item in (getattr(decision, "alternatives", []) or [])
                ],
                "runtime": getattr(decision, "runtime", "bauer_native"),
                "team_id": getattr(decision, "team_id", ""),
                "agent_id": getattr(decision, "agent_id", ""),
                "tools": list(getattr(decision, "tools", []) or []),
                "strategy": getattr(decision, "strategy", "direct"),
                "plan": list(getattr(decision, "plan", []) or []),
                "memory_hits": list(getattr(decision, "memory_hits", []) or []),
                "memory_decision_id": getattr(decision, "memory_decision_id", ""),
            }
        )
    return data
