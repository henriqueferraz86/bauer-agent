"""Opt-in registration cannot silently expose an unconfigured gateway."""
from __future__ import annotations

import shutil
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bauer.tool_gateway_wiring import ToolGatewayConfigurationError, install_tool_gateway
from tests.test_tool_gateway_authorization import env  # noqa: F401


def test_disabled_does_not_register_route(monkeypatch, tmp_path):
    monkeypatch.delenv("BAUER_TOOL_GATEWAY_ENABLED", raising=False)
    app = FastAPI()
    assert not install_tool_gateway(app, sessions_dir=tmp_path, run_manager=None, kernel=None)
    with TestClient(app) as client:
        assert client.post("/bauer/tool-gateway/call", json={}).status_code == 404


def test_enabled_misconfiguration_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("BAUER_TOOL_GATEWAY_ENABLED", "true")
    for name in ("BAUER_TOOL_GATEWAY_PUBLIC_KEY_FILE", "BAUER_TOOL_GATEWAY_SERVICE_KEY",
                 "BAUER_AGENTOS_JWT_KEY", "BAUER_TOOL_GATEWAY_ISSUER", "BAUER_TOOL_GATEWAY_AUDIENCE"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ToolGatewayConfigurationError):
        install_tool_gateway(FastAPI(), sessions_dir=tmp_path, run_manager=None, kernel=None)
    with pytest.raises(ToolGatewayConfigurationError):
        install_tool_gateway(FastAPI(), sessions_dir=tmp_path, run_manager=None, kernel=object())


def test_enabled_router_requires_secure_transport_and_authorization(env, tmp_path, monkeypatch):
    auth, token, _, runs, _, remote, bindings, workspace, registry = env
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    shutil.copyfile(bindings.path, sessions / "channel_projects.sqlite3")
    home = tmp_path / "bauer-home"
    home.mkdir()
    shutil.copyfile(registry, home / "projects.json")
    public_key = tmp_path / "public.pem"
    public_key.write_text(auth._public_key, encoding="utf-8")
    monkeypatch.setenv("BAUER_HOME", str(home))
    monkeypatch.setenv("BAUER_TOOL_GATEWAY_ENABLED", "true")
    monkeypatch.setenv("BAUER_TOOL_GATEWAY_PUBLIC_KEY_FILE", str(public_key))
    monkeypatch.setenv("BAUER_TOOL_GATEWAY_SERVICE_KEY", "isolated-service-key-at-least-32-bytes")
    monkeypatch.setenv("BAUER_AGENTOS_JWT_KEY", "identity-test-secret-with-over-32-bytes")
    monkeypatch.setenv("BAUER_TOOL_GATEWAY_ISSUER", "gateway")
    monkeypatch.setenv("BAUER_TOOL_GATEWAY_AUDIENCE", "tool-gateway")
    (workspace / "example.txt").write_text("safe contents", encoding="utf-8")
    with pytest.raises(ToolGatewayConfigurationError, match="Política"):
        install_tool_gateway(FastAPI(), sessions_dir=sessions, run_manager=runs, kernel=object(),
                             policy_rules_path=tmp_path / "missing-policy.yaml")
    remote_path = sessions / "telegram-agentos.sqlite3"
    assert not remote_path.exists()
    assert install_tool_gateway(FastAPI(), sessions_dir=sessions, run_manager=runs, kernel=object())
    from bauer.remote_run_store import RemoteRunStore

    assert RemoteRunStore(remote_path, require_managed_session=True).recoverable(owner_id="bootstrap") == []
    shutil.copyfile(remote.path, remote_path)
    from bauer.server import create_app
    from bauer.tool_router import ToolRouter

    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: offline-test\n", encoding="utf-8")
    client = MagicMock()
    client.host = "http://127.0.0.1:1"
    client._provider = "ollama"
    app = create_app(model_name="offline-test", applied_context=4096,
                     router=ToolRouter(workspace=workspace), client=client, system_prompt="test",
                     sessions_dir=sessions, config_path=config, workspace=workspace)
    headers = {"Authorization": token(), "X-Bauer-Service-Key": "isolated-service-key-at-least-32-bytes"}
    body = {"action": "read_file", "args": {"path": "example.txt"}}
    with TestClient(app, base_url="http://testserver") as client:
        assert client.post("/bauer/tool-gateway/call", headers=headers, json=body).status_code == 403
    with TestClient(app, base_url="https://testserver") as client:
        assert client.post("/bauer/tool-gateway/call", json=body).status_code == 401
        assert client.post("/bauer/tool-gateway/call", headers={**headers, "Authorization": token({"sub": "tg-other"})},
                           json=body).status_code == 403
        assert client.post("/bauer/tool-gateway/call", headers=headers, json={
            "action": "write_file", "args": {"path": "example.txt", "content": "unsafe"},
        }).status_code == 403
        result = client.post("/bauer/tool-gateway/call", headers=headers, json=body)
        assert result.status_code == 200, result.text
        assert "safe contents" in result.json()["result"]
        assert client.post("/bauer/tool-gateway/call", headers=headers, json=body).status_code == 409
    assert (workspace / "example.txt").read_text(encoding="utf-8") == "safe contents"
