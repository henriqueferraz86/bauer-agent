"""Offline probes for the Docker Tool Gateway canary, using synthetic state."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import httpx
import uvicorn

CONFIG = Path("/app/staging-config.yaml")
WORKSPACE = Path("/app/workspace/gateway-canary")
SESSIONS = Path("/app/memory/sessions")
RUNTIME = Path("/app/memory/runtime")
GATEWAY = "http://127.0.0.1:8000"
AGENTOS = "http://127.0.0.1:7787"
OPERATOR = "telegram:123:123"
MARKER = "bauer-canary: synthetic workspace file\n"


def setup_project() -> str:
    from bauer import projects_registry
    from bauer.channel_projects import ChannelProjectStore
    from bauer.paths import get_bauer_home

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / "probe.txt").write_text(MARKER, encoding="utf-8")
    registry = get_bauer_home() / "projects.json"
    project = projects_registry.add_project(WORKSPACE, registry_path=registry)
    ChannelProjectStore(SESSIONS / "channel_projects.sqlite3").bind(OPERATOR, project["id"], WORKSPACE)
    return project["id"]


def gateway_keys() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private = Path(os.environ["BAUER_TOOL_GATEWAY_PRIVATE_KEY_FILE"])
    public = Path(os.environ["BAUER_TOOL_GATEWAY_PUBLIC_KEY_FILE"])
    private.parent.mkdir(parents=True, exist_ok=True)
    if private.is_file() and public.is_file():
        return
    if private.exists() or public.exists():
        raise RuntimeError("incomplete canary key pair")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                     serialization.PublicFormat.SubjectPublicKeyInfo))


def serve_bauer() -> None:
    from bauer.ollama_client import OllamaClient
    from bauer.server import create_app
    from bauer.tool_router import ToolRouter

    gateway_keys()
    setup_project()
    app = create_app(model_name="offline-echo", applied_context=4096, router=ToolRouter(workspace=WORKSPACE),
                     client=OllamaClient("http://127.0.0.1:1"), system_prompt="Offline canary", tool_mode="bridge",
                     sessions_dir=SESSIONS, config_path=CONFIG, workspace=WORKSPACE)
    uvicorn.run(app, host="127.0.0.1", port=8000, proxy_headers=False, access_log=False)


def serve_agentos() -> None:
    from agno.models.base import Model
    from agno.models.response import ModelResponse
    from bauer.agentos_app import build_agentos_app
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    class CanaryModel(Model):
        def invoke(self, *args, **kwargs):
            for message in reversed(kwargs.get("messages", [])):
                if message.role == "tool":
                    return ModelResponse(role="assistant", content=f"Read result: {message.content}")
                if message.role == "user":
                    break
            return ModelResponse(role="assistant", tool_calls=[{
                "id": secrets.token_hex(16), "type": "function",
                "function": {"name": "read_file", "arguments": json.dumps({"path": "probe.txt"})},
            }])

        async def ainvoke(self, *args, **kwargs):
            return self.invoke(*args, **kwargs)

        def invoke_stream(self, *args, **kwargs):
            yield self.invoke(*args, **kwargs)

        async def ainvoke_stream(self, *args, **kwargs):
            yield self.invoke(*args, **kwargs)

        def _parse_provider_response(self, response, **kwargs):
            return response

        def _parse_provider_response_delta(self, response, **kwargs):
            return response

    AgnoRuntimeAdapter._build_model = lambda self, spec: CanaryModel(id="gateway-canary")
    app = build_agentos_app(config_path=CONFIG, workspace="/app/workspace", runtime_root=RUNTIME)
    uvicorn.run(app, host="127.0.0.1", port=7787, proxy_headers=False, access_log=False)


def check_gateway() -> None:
    with httpx.Client(base_url=GATEWAY, trust_env=False, timeout=3) as client:
        client.get("/health").raise_for_status()
        denied = client.post("/bauer/tool-gateway/call", json={"action": "read_file", "args": {"path": "probe.txt"}})
        if denied.status_code != 401:
            raise RuntimeError(f"expected gateway to reject unauthenticated call, got {denied.status_code}")


def probe() -> None:
    from bauer.core.kernel.entry import run_governed
    from bauer.core.kernel.kernel import BauerKernel
    from bauer.core.runtime.run_manager import RunManager
    from bauer.execution_control import ExecutionControl
    from bauer.telegram_agentos_http import execute_http_turn

    check_gateway()
    with httpx.Client(base_url=AGENTOS, trust_env=False, timeout=5) as client:
        info = client.get("/info").raise_for_status().json()
        assert info.get("user_isolation") is True
        assert info.get("auth_mode") not in {None, "none"}
        assert client.get("/bauer/channel-capabilities").status_code == 401

    project_id = setup_project()
    kernel = BauerKernel(runs=RunManager(root=RUNTIME))
    session_id = "canary-" + secrets.token_hex(16)
    settings = {"enabled": True, "experimental_read_only": True, "external_read_tools": True,
                "agent_id": "bauer.dev", "base_url": AGENTOS,
                "jwt_issuer": os.environ["BAUER_AGENTOS_JWT_ISSUER"],
                "jwt_audience": os.environ["BAUER_AGENTOS_JWT_AUDIENCE"]}

    def execute(payload):
        control = ExecutionControl(run_id=payload["run_id"])
        execution = SimpleNamespace(channel="telegram", private_chat=True, update_id=secrets.token_hex(16),
                                    inbox_persisted=True, owner=OPERATOR, chat_id="123", project_id=project_id,
                                    control=control)
        answer = execute_http_turn(settings=settings, execution=execution, gateway_run_id=payload["run_id"],
                                   text="Read the synthetic canary file", state_dir=SESSIONS, kernel=kernel)
        return {"output": answer, "tool_calls_count": control.tool_calls}

    result = run_governed(kernel, execute, agent_id="channel.telegram", session_id=session_id,
                          runtime_adapter="agentos_http", input={"endpoint": "canal:telegram",
                          "operator": OPERATOR, "project_id": project_id, "workspace": str(WORKSPACE)})
    if not result.ok:
        raise RuntimeError(f"canary execution did not finish: {result.status} ({result.error})")
    assert MARKER.strip() in result.result["output"]
    assert result.result["tool_calls_count"] == 1
    assert (WORKSPACE / "probe.txt").read_text(encoding="utf-8") == MARKER
    with closing(sqlite3.connect(kernel.runs.store.path)) as database:
        states = database.execute("SELECT state FROM tool_gateway_calls WHERE run_id=?", (result.run_id,)).fetchall()
    assert states == [("COMPLETED",)]
    print("OK: JWT, leitura autenticada, retomada do AgentOS e conclusão no Kernel")


if __name__ == "__main__":
    os.umask(0o077)
    commands = {"bauer": serve_bauer, "agentos": serve_agentos, "health": check_gateway, "probe": probe}
    commands[sys.argv[1]]()
