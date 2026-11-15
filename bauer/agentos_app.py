"""Agno AgentOS surface backed by the formal Bauer agent catalog.

The Bauer Cockpit remains the governed runtime on port 7770.  This module is
an optional AgentOS process for clients that speak the Agno API (port 7777 by
default); importing it does not start a server.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from starlette.requests import Request

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
    raw_jwt = os.environ.get("BAUER_AGENTOS_JWT_ISOLATION", "").strip().lower()
    if raw_jwt not in {"", "0", "false", "no", "1", "true", "yes"}:
        raise AgentOSBuildError("BAUER_AGENTOS_JWT_ISOLATION deve ser true ou false.")
    jwt_isolation = raw_jwt in {"1", "true", "yes"}
    jwt_key = os.environ.get("BAUER_AGENTOS_JWT_KEY", "")
    jwt_audience = os.environ.get("BAUER_AGENTOS_JWT_AUDIENCE", "").strip()
    jwt_issuer = os.environ.get("BAUER_AGENTOS_JWT_ISSUER", "").strip()
    if jwt_isolation:
        if len(jwt_key.encode("utf-8")) < 32 or not jwt_key.strip() or not jwt_audience or not jwt_issuer:
            raise AgentOSBuildError("isolamento JWT requer chave de 32 bytes, audience e issuer.")
        if any(os.environ.get(name, "").strip() for name in ("OS_SECURITY_KEY", "JWT_VERIFICATION_KEY", "JWT_JWKS_FILE")):
            raise AgentOSBuildError("isolamento JWT não permite chaves alternativas de autenticação.")
    raw_boundary = os.environ.get("BAUER_AGENTOS_CHANNEL_BOUNDARY", "").strip().lower()
    if raw_boundary not in {"", "0", "false", "no", "1", "true", "yes"}:
        raise AgentOSBuildError("BAUER_AGENTOS_CHANNEL_BOUNDARY deve ser true ou false.")
    channel_boundary = raw_boundary in {"1", "true", "yes"}
    raw_external = os.environ.get("BAUER_AGENTOS_EXTERNAL_READ_TOOLS", "").strip().lower()
    if raw_external not in {"", "0", "false", "no", "1", "true", "yes"}:
        raise AgentOSBuildError("BAUER_AGENTOS_EXTERNAL_READ_TOOLS deve ser true ou false.")
    external_tools = raw_external in {"1", "true", "yes"}
    requirement_key = os.environ.get("BAUER_TOOL_GATEWAY_REQUIREMENT_KEY", "")
    continuation_key = os.environ.get("BAUER_TOOL_GATEWAY_CONTINUATION_KEY", "")
    if external_tools and (not jwt_isolation or not channel_boundary
            or len(requirement_key.encode("utf-8")) < 32 or len(continuation_key.encode("utf-8")) < 32
            or len({requirement_key, continuation_key, jwt_key}) != 3):
        raise AgentOSBuildError("tools externas requerem isolamento JWT e chaves independentes de 32 bytes.")
    if jwt_isolation and not channel_boundary:
        raise AgentOSBuildError("isolamento JWT experimental requer BAUER_AGENTOS_CHANNEL_BOUNDARY=true.")
    if channel_boundary and not jwt_isolation and not os.environ.get("OS_SECURITY_KEY", "").strip():
        raise AgentOSBuildError("BAUER_AGENTOS_CHANNEL_BOUNDARY requer OS_SECURITY_KEY ou isolamento JWT.")
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
        from .core.runtime.agent_spec import AgentSpec
        from .core.runtime.agent_spec import agno_agent_spec_from_bauer
        from .core.runtime.agent_registry import RuntimeAgentRegistry
        from .core.runtime.team_registry import TeamRegistry

        cfg = load_config(config_path)
        root = Path(runtime_root)
        selected_model: dict[str, str] = {}
        selected_model_file = root / "serve-model-state.json"
        try:
            raw_selected = json.loads(selected_model_file.read_text(encoding="utf-8"))
            if raw_selected.get("provider") and raw_selected.get("model"):
                selected_model = {
                    "provider": str(raw_selected["provider"]),
                    "model": str(raw_selected["model"]),
                }
        except (OSError, ValueError, AttributeError):
            logger.debug("saved AgentOS model selection is unavailable")
        adapter_cfg = dict(getattr(getattr(cfg, "runtime", None), "adapters", {}).get("agno", {}) or {})
        adapter_cfg.setdefault("workspace", str(Path(workspace)))
        adapter_cfg.setdefault("db_file", str(root / "agno" / "sessions.db"))
        # AgentOS is the browser chat surface, not a durable worker.  Reuse
        # the chat policy context so read-only network tools such as
        # ``web_search`` are available when the catalog agent exposes them.
        adapter_cfg.setdefault("tool_context", "chat")
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
                if channel_boundary:
                    agno_spec["tools"] = []
                if selected_model:
                    agno_spec.update(selected_model)
                agent = adapter._build_agent(agno_spec)
                if external_tools:
                    from .agentos_external_tools import build_external_read_tools

                    agent.tools = build_external_read_tools()
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
            members: list[AgentSpec] = []
            for agent_id in team_spec.agents:
                item = registry.get(agent_id)
                if item is not None and item.runtime_adapter == "agno":
                    members.append(item)
            if not members:
                logger.warning("ignorando time %s sem membros Agno", team_spec.id)
                continue
            supervisor_id = team_spec.coordinator or team_spec.agents[0]
            supervisor: AgentSpec = registry.get(supervisor_id) or members[0]
            raw_team: dict[str, Any] = {
                "id": team_spec.id,
                "name": team_spec.name,
                "mode": str(team_spec.coordination.get("mode") or "coordinate"),
                "members": [
                    {**agno_agent_spec_from_bauer(item), **selected_model}
                    if selected_model else agno_agent_spec_from_bauer(item)
                    for item in members
                ],
                "supervisor": {
                    **agno_agent_spec_from_bauer(supervisor),
                    "instructions": team_spec.coordination.get("instructions", []),
                },
                "max_iterations": int(team_spec.limits.get("max_iterations") or 10),
            }
            if selected_model:
                raw_team["supervisor"].update(selected_model)
            if channel_boundary:
                for member in raw_team["members"]:
                    member["tools"] = []
                raw_team["supervisor"]["tools"] = []
            try:
                teams.append(adapter._build_team(raw_team))
            except Exception as exc:  # noqa: BLE001 - identify the broken spec
                raise AgentOSBuildError(f"não foi possível materializar o time {team_spec.id}: {exc}") from exc

        auth_options: dict[str, Any] = {}
        if jwt_isolation:
            from agno.os.config import AuthorizationConfig

            auth_options = {
                "authorization": True,
                "authorization_config": AuthorizationConfig(
                    verification_keys=[jwt_key], algorithm="HS256", verify_audience=True,
                    audience=jwt_audience, user_isolation=True,
                ),
            }
        agent_os = AgentOS(
            id="bauer-agentos",
            name="Bauer AgentOS",
            description="Agno AgentOS sobre o catálogo formal de agentes e times do Bauer.",
            agents=agents,
            teams=teams,
            db=agentos_db,
            tracing=False,
            telemetry=False,
            **auth_options,
        )
        if channel_boundary and not jwt_isolation and not getattr(agent_os.settings, "os_security_key", None):
            raise AgentOSBuildError("AgentOS não reconheceu OS_SECURITY_KEY; recusando inicialização.")
        app = agent_os.get_app()
        if channel_boundary and jwt_isolation:
            from fastapi import HTTPException
            from .agentos_capabilities import capability_proof
            from .tool_gateway_protocol import TOOL_CONTINUATION_VERSION

            @app.get("/bauer/channel-capabilities")
            def channel_capabilities(request: Request) -> dict[str, Any]:
                """Challenge-bound statement of this process' restricted profile."""
                challenges = request.headers.getlist("X-Bauer-Challenge")
                if len(challenges) != 1:
                    raise HTTPException(status_code=400, detail="invalid capability challenge")
                capabilities = {
                    "contract_version": 1,
                    "mode": "read_only",
                    "user_isolation": True,
                    "catalog_tools": False,
                    "continuation": external_tools,
                    "cancel_requires_terminal": True,
                    "identity": request.state.bauer_jwt_identity,
                }
                if external_tools:
                    capabilities["external_read_tools"] = True
                    capabilities["tool_continuation_version"] = TOOL_CONTINUATION_VERSION
                try:
                    proof = capability_proof(jwt_key, challenges[0], capabilities)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="invalid capability challenge") from exc
                return {"capabilities": capabilities, "proof": proof}
        # Expose the same model catalog used by the Bauer cockpit.  The
        # upstream Agent UI does not query this route yet, but keeping it on
        # the AgentOS origin lets a patched/custom UI use one canonical source
        # instead of seeing only Ollama/configured models.
        from fastapi import Query

        @app.get("/api/models/catalog")
        def models_catalog(
            provider: str = Query(default=""),
            q: str = Query(default=""),
            free: bool | None = Query(default=None),
            limit: int = Query(default=200, ge=1, le=10000),
            offset: int = Query(default=0, ge=0),
        ) -> dict[str, Any]:
            from .models_dev import catalog_models

            models = catalog_models(provider=provider or None, chat_only=True)
            if q:
                needle = q.casefold()
                models = [item for item in models if needle in str(item.get("id", "")).casefold()]
            if free is not None:
                models = [item for item in models if bool(item.get("is_free")) is free]
            return {
                "total": len(models),
                "free_count": sum(1 for item in models if item.get("is_free")),
                "selected": dict(selected_model) if selected_model else None,
                "models": models[offset : offset + limit],
            }

        @app.post("/api/models/select")
        def select_model(body: dict[str, Any]) -> dict[str, str]:
            """Select one Bauer catalog model for all AgentOS agents/teams."""
            provider = str(body.get("provider") or "").strip().lower()
            model_id = str(body.get("model") or body.get("id") or "").strip()
            if not provider or not model_id:
                from fastapi import HTTPException

                raise HTTPException(status_code=400, detail="provider e model são obrigatórios")
            selected = {"provider": provider, "model": model_id}
            try:
                model = adapter._build_model(selected)
                for item in agents:
                    item.model = model
                for item in teams:
                    item.model = model
                    for member in getattr(item, "members", []) or []:
                        member.model = model
                selected_model_file.parent.mkdir(parents=True, exist_ok=True)
                selected_model_file.write_text(json.dumps(selected, ensure_ascii=False), encoding="utf-8")
                selected_model.clear()
                selected_model.update(selected)
            except Exception as exc:  # noqa: BLE001 - stable API boundary
                from fastapi import HTTPException

                raise HTTPException(status_code=400, detail=f"modelo indisponível: {exc}") from exc
            return selected
        # Agent UI is served from a different origin in the Docker stack.
        # Keep the allow-list explicit; operators can add a LAN/reverse-proxy
        # origin through AGENT_OS_CORS_ORIGINS without opening the API broadly.
        from fastapi.middleware.cors import CORSMiddleware

        raw_origins = os.environ.get(
            "AGENT_OS_CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000",
        )
        origins = [origin.strip().rstrip("/") for origin in raw_origins.split(",") if origin.strip()]
        if origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
        if channel_boundary:
            from .agentos_channel_boundary import ChannelBoundaryMiddleware
            from .agentos_approval_store import RemoteApprovalStore
            from .agentos_active_runs import ActiveRunRegistry

            approvals = RemoteApprovalStore(root / "agno" / "channel-approvals.db") if jwt_isolation else None
            if external_tools:
                from .agentos_external_continuation import ExternalContinuationCoordinator
                from .agentos_external_requirement import ExternalRequirementReader
                from .agentos_requirement_api import build_requirement_router
                from .agentos_requirement_attestation import RequirementAttestor

                if approvals is None:
                    raise AgentOSBuildError("tools externas requerem o store de aprovação autenticado.")
                agent_map = {str(item.id): item for item in agents}
                allowed = {agent_id: {"read_file"} for agent_id in agent_map}
                reader = ExternalRequirementReader(agents=agent_map, allowed_tools=allowed)
                app.include_router(build_requirement_router(
                    RequirementAttestor(coordinator=reader, key=requirement_key),
                    continuation=ExternalContinuationCoordinator(reader=reader, store=approvals),
                    continuation_key=continuation_key,
                ))

            app.add_middleware(
                ChannelBoundaryMiddleware,
                agent_ids={str(item.id) for item in agents},
                team_ids={str(item.id) for item in teams},
                jwt_isolation=jwt_isolation,
                entities={"agents": {str(item.id): item for item in agents},
                          "teams": {str(item.id): item for item in teams}},
                approval_store=approvals,
                active_runs=ActiveRunRegistry(root / "agno" / "channel-active-runs.db") if jwt_isolation else None,
                external_tools=external_tools,
            )
        if jwt_isolation:
            from .agentos_jwt_claims import AgentOSJWTClaimsMiddleware

            app.add_middleware(AgentOSJWTClaimsMiddleware, key=jwt_key, audience=jwt_audience, issuer=jwt_issuer)
        return app
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
