"""A governed Telegram turn across real local HTTP sockets and real Agno."""
from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI

pytest.importorskip("agno")

from bauer import projects_registry
from bauer.agentos_app import build_agentos_app
from bauer.channel_projects import ChannelProjectStore
from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.execution_control import ExecutionControl
from bauer.provider_retry import ProviderRetryInterrupted
from bauer.remote_run_store import RemoteRunStore
from bauer.telegram_agentos_http import execute_http_turn
from bauer.tool_gateway_authorization import ToolGatewayAuthorizer
from bauer.tool_gateway_endpoint import ToolCallLedger, build_tool_gateway_router
from tests.test_agentos_gateway_chain import _ReadModel
from tests.test_tool_gateway_authorization import env  # noqa: F401


@contextmanager
def _server(app):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()


@pytest.mark.parametrize("allow_tool", [True, False])
def test_real_http_stream_through_tool_gateway_and_back(env, tmp_path, monkeypatch, allow_tool):
    original_auth, token, claims, runs, run, _, _, workspace, _ = env
    (workspace / "example.txt").write_text("project data", encoding="utf-8")
    state = tmp_path / "sessions"
    state.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("BAUER_HOME", str(home))
    registry = home / "projects.json"
    project = projects_registry.add_project(workspace, registry_path=registry)
    assert project["id"] == claims["project_id"]
    bindings = ChannelProjectStore(state / "channel_projects.sqlite3")
    bindings.bind("telegram:123:123", project["id"], workspace)
    remote = RemoteRunStore(state / "telegram-agentos.sqlite3", require_managed_session=True)
    identity_key = "identity-test-secret-with-over-32-bytes"
    requirement_key = "requirement-test-secret-over-32-bytes"
    continuation_key = "continuation-test-secret-over-32-bytes"
    service_key = "service-test-secret-over-32-bytes"
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    private.write_text(token.__kwdefaults__["signing_key"], encoding="utf-8")
    public.write_text(original_auth._public_key, encoding="utf-8")
    values = {"BAUER_AGENTOS_JWT_ISOLATION": "true", "BAUER_AGENTOS_CHANNEL_BOUNDARY": "true",
              "BAUER_AGENTOS_EXTERNAL_READ_TOOLS": "true", "BAUER_AGENTOS_JWT_KEY": identity_key,
              "BAUER_AGENTOS_JWT_ISSUER": "identity-issuer", "BAUER_AGENTOS_JWT_AUDIENCE": "identity-audience",
              "BAUER_TOOL_GATEWAY_REQUIREMENT_KEY": requirement_key,
              "BAUER_TOOL_GATEWAY_CONTINUATION_KEY": continuation_key, "BAUER_TOOL_GATEWAY_SERVICE_KEY": service_key,
              "BAUER_TOOL_GATEWAY_PRIVATE_KEY_FILE": str(private), "BAUER_TOOL_GATEWAY_PUBLIC_KEY_FILE": str(public),
              "BAUER_TOOL_GATEWAY_ISSUER": "gateway", "BAUER_TOOL_GATEWAY_AUDIENCE": "tool-gateway"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    for name in ("OS_SECURITY_KEY", "JWT_VERIFICATION_KEY", "JWT_JWKS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(AgnoRuntimeAdapter, "_build_model", lambda self, spec: _ReadModel(id="stub"))
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: offline-echo\n", encoding="utf-8")
    agentos = build_agentos_app(config_path=config, runtime_root=tmp_path / "agentos")
    auth = ToolGatewayAuthorizer(public_key=original_auth._public_key, identity_key=identity_key,
                                 issuer="gateway", audience="tool-gateway", runs=runs,
                                 remote_runs=remote, bindings=bindings, registry_path=registry)
    gateway = FastAPI()
    gateway.include_router(build_tool_gateway_router(authorizer=auth, ledger=ToolCallLedger(runs.store.path),
                           service_key=service_key, policy_root=runs.store.root, require_secure_transport=True))
    control = ExecutionControl(run_id=run.id)
    if not allow_tool:
        control.max_tool_calls = 0
    execution = SimpleNamespace(channel="telegram", private_chat=True, update_id="42", inbox_persisted=True,
                                owner="telegram:123:123", chat_id="123", project_id=project["id"], control=control)
    kernel = SimpleNamespace(runs=runs)
    with _server(agentos) as agentos_url, _server(gateway) as gateway_url:
        monkeypatch.setenv("BAUER_TOOL_GATEWAY_URL", gateway_url)
        settings = {"enabled": True, "experimental_read_only": True, "external_read_tools": True,
                    "agent_id": "bauer.dev", "base_url": agentos_url,
                    "jwt_issuer": "identity-issuer", "jwt_audience": "identity-audience"}
        if allow_tool:
            result = execute_http_turn(settings=settings, execution=execution, gateway_run_id=run.id,
                                       text="read the project file", state_dir=state, kernel=kernel)
            assert "Received tool result:" in result
            assert "project data" in result
        else:
            with pytest.raises(ProviderRetryInterrupted) as interrupted:
                execute_http_turn(settings=settings, execution=execution, gateway_run_id=run.id,
                                  text="read the project file", state_dir=state, kernel=kernel)
            assert interrupted.value.reason == "budget_exhausted"
    assert control.tool_calls == (1 if allow_tool else 0)
    assert (workspace / "example.txt").read_text(encoding="utf-8") == "project data"
