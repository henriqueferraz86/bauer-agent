"""Agno AgentOS surface backed by the formal Bauer agent catalog.

The Bauer Cockpit remains the governed runtime on port 7770.  This module is
an optional AgentOS process for clients that speak the Agno API (port 7777 by
default); importing it does not start a server.
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("bauer.agentos")


class AgentOSBuildError(RuntimeError):
    """Raised when the formal Bauer catalog cannot be exposed through AgentOS."""


def build_agentos_app(
    *,
    config_path: str | Path = "config.yaml",
    workspace: str | Path = "workspace",
    runtime_root: str | Path = "memory/runtime",
    agent_roots: list[str | Path] | None = None,
    team_roots: list[str | Path] | None = None,
) -> Any:
    """Build and return a FastAPI app owned by :class:`agno.os.AgentOS`.

    Specs are materialized through ``AgnoRuntimeAdapter`` so the standalone
    surface and Bauer's in-process SDK path use the same model/tool mapping.
    """
    try:
        from agno.os import AgentOS
        from agno.db.sqlite import SqliteDb
    except (ImportError, ModuleNotFoundError) as exc:
        raise AgentOSBuildError(
            "AgentOS requer a dependência Agno com suporte a 'agno[os]'."
        ) from exc

    try:
        from .config_loader import load_config
        from .core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
        from .core.runtime.agent_spec import agno_agent_spec_from_bauer
        from .core.runtime.agent_registry import RuntimeAgentRegistry
        from .core.runtime.team_registry import TeamRegistry

        cfg = load_config(config_path)
        root = Path(runtime_root)
        adapter_cfg = dict(getattr(getattr(cfg, "runtime", None), "adapters", {}).get("agno", {}) or {})
        adapter_cfg.setdefault("workspace", str(Path(workspace)))
        adapter_cfg.setdefault("db_file", str(root / "agno" / "sessions.db"))
        adapter = AgnoRuntimeAdapter(config=cfg, adapter_config=adapter_cfg)
        agentos_db = SqliteDb(db_file=str(root / "agno" / "agentos.db"))

        # Every component in one AgentOS must share the same database identity.
        # The Bauer adapter normally creates an isolated handle per object, so
        # replace only this factory's handle with the AgentOS database.
        adapter._build_db = lambda: agentos_db  # type: ignore[method-assign]

        formal_agents = RuntimeAgentRegistry(roots=agent_roots).list() if agent_roots else RuntimeAgentRegistry().list()
        agent_by_id: dict[str, Any] = {}
        agents: list[Any] = []
        for spec in formal_agents:
            if spec.runtime_adapter != "agno":
                continue
            try:
                agno_spec = agno_agent_spec_from_bauer(spec)
                agent = adapter._build_agent(agno_spec)
            except Exception as exc:  # noqa: BLE001 - identify the broken spec
                raise AgentOSBuildError(f"não foi possível materializar o agente {spec.id}: {exc}") from exc
            agent_by_id[spec.id] = agent
            agents.append(agent)

        registry = RuntimeAgentRegistry(roots=agent_roots) if agent_roots else RuntimeAgentRegistry()
        teams_registry = TeamRegistry(
            roots=team_roots,
            agent_registry=registry,
        ) if team_roots else TeamRegistry(agent_registry=registry)
        teams: list[Any] = []
        for team_spec in teams_registry.list():
            members = [registry.get(agent_id) for agent_id in team_spec.agents]
            members = [item for item in members if item is not None and item.runtime_adapter == "agno"]
            if not members:
                logger.warning("ignorando time %s sem membros Agno", team_spec.id)
                continue
            supervisor_id = team_spec.coordinator or team_spec.agents[0]
            supervisor = registry.get(supervisor_id) or members[0]
            raw_team = {
                "id": team_spec.id,
                "name": team_spec.name,
                "mode": str(team_spec.coordination.get("mode") or "coordinate"),
                "members": [agno_agent_spec_from_bauer(item) for item in members],
                "supervisor": {
                    **agno_agent_spec_from_bauer(supervisor),
                    "instructions": team_spec.coordination.get("instructions", []),
                },
                "max_iterations": int(team_spec.limits.get("max_iterations") or 10),
            }
            try:
                teams.append(adapter._build_team(raw_team))
            except Exception as exc:  # noqa: BLE001 - identify the broken spec
                raise AgentOSBuildError(f"não foi possível materializar o time {team_spec.id}: {exc}") from exc

        agent_os = AgentOS(
            id="bauer-agentos",
            name="Bauer AgentOS",
            description="Agno AgentOS sobre o catálogo formal de agentes e times do Bauer.",
            agents=agents,
            teams=teams,
            db=agentos_db,
            tracing=False,
            telemetry=False,
        )
        return agent_os.get_app()
    except AgentOSBuildError:
        raise
    except Exception as exc:  # noqa: BLE001 - stable boundary for service startup
        raise AgentOSBuildError(f"falha ao construir o AgentOS: {exc}") from exc


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Serve o AgentOS do Bauer")
    parser.add_argument("--config", default=os.environ.get("BAUER_CONFIG", "config.yaml"))
    parser.add_argument("--workspace", default=os.environ.get("BAUER_WORKSPACE", "workspace"))
    parser.add_argument("--runtime-root", default=os.environ.get("BAUER_RUNTIME_ROOT", "memory/runtime"))
    parser.add_argument("--host", default=os.environ.get("AGENT_OS_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENT_OS_PORT", "7777")))
    args = parser.parse_args(argv)

    import uvicorn

    app = build_agentos_app(
        config_path=args.config,
        workspace=args.workspace,
        runtime_root=args.runtime_root,
    )
    uvicorn.run(app, host=args.host, port=args.port, lifespan="on")


if __name__ == "__main__":  # pragma: no cover - exercised by systemd smoke test
    main()
