"""The AgentOS shared key requires its middleware dependency and protects routes."""

import pytest

pytest.importorskip("agno")

from fastapi.testclient import TestClient

from bauer.agentos_app import build_agentos_app


def test_agentos_shared_key_protects_config_and_custom_routes(monkeypatch, tmp_path):
    monkeypatch.setenv("OS_SECURITY_KEY", "local-test-secret")
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: offline-echo\n", encoding="utf-8")
    agents = tmp_path / "agents.yaml"
    agents.write_text("agents: []\n", encoding="utf-8")
    app = build_agentos_app(
        config_path=config,
        agent_roots=[agents],
        team_roots=[tmp_path / "teams"],
        runtime_root=tmp_path / "runtime",
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/config").status_code in (401, 403)
        assert client.get("/config", headers={"Authorization": "Bearer wrong"}).status_code in (401, 403)
        assert client.post("/api/models/select", json={}).status_code in (401, 403)
        headers = {"Authorization": "Bearer local-test-secret"}
        assert client.get("/config", headers=headers).status_code == 200
        assert client.post("/api/models/select", headers=headers, json={}).status_code == 400
