"""Local Agno JWT isolation proof; not evidence about a deployed AgentOS."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
from typing import Any

import pytest

pytest.importorskip("agno")
pytest.importorskip("sqlalchemy")
jwt = pytest.importorskip("jwt")

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.base import Model
from agno.models.response import ModelResponse
from agno.os import AgentOS
from agno.os.config import AuthorizationConfig
from fastapi.testclient import TestClient


class LocalOnlyModel(Model):
    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return ModelResponse(role="assistant", content="local response")

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self.invoke(*args, **kwargs)

    def invoke_stream(self, *args: Any, **kwargs: Any):
        yield self.invoke(*args, **kwargs)

    async def ainvoke_stream(self, *args: Any, **kwargs: Any):
        yield self.invoke(*args, **kwargs)

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response


@pytest.fixture
def isolated_os(tmp_path, monkeypatch):
    monkeypatch.delenv("OS_SECURITY_KEY", raising=False)
    secret = "local-tests-only-32-byte-jwt-secret"
    db = SqliteDb(db_file=str(tmp_path / "agentos.db"))
    agent = Agent(id="demo", name="Demo", model=LocalOnlyModel(id="local"), db=db)
    app = AgentOS(
        id="local-test", agents=[agent], db=db, telemetry=False, tracing=False,
        authorization=True,
        authorization_config=AuthorizationConfig(
            verification_keys=[secret], algorithm="HS256", verify_audience=True,
            audience="local-test", user_isolation=True,
        ),
    ).get_app()

    def token(user: str, *, audience: str = "local-test", expired: bool = False) -> str:
        return jwt.encode({"sub": user, "aud": audience,
                           "exp": datetime.now(timezone.utc) + timedelta(minutes=-5 if expired else 5),
                           "scopes": ["agents:demo:run", "agents:demo:read"]}, secret, algorithm="HS256")

    with TestClient(app) as client:
        yield client, token


def test_cross_user_cannot_reuse_session_or_cancel_run(isolated_os):
    client, token = isolated_os
    a = {"Authorization": f"Bearer {token('alice')}"}
    b = {"Authorization": f"Bearer {token('bob')}"}
    create = client.post("/agents/demo/runs", headers=a,
                         data={"message": "hello", "session_id": "session-a",
                               "user_id": "bob", "stream": "false"})
    assert create.status_code == 200, create.text
    result = create.json()
    run_id = result["run_id"]
    # The form identity cannot override the caller authenticated by the JWT.
    assert result["user_id"] == "alice"
    own_get = client.get(f"/agents/demo/runs/{run_id}", headers=a, params={"session_id": "session-a"})
    assert own_get.status_code == 200
    for headers in (b,):
        foreign_create = client.post("/agents/demo/runs", headers=headers,
                                     data={"message": "injected", "session_id": "session-a", "stream": "false"})
        foreign_cancel = client.post(f"/agents/demo/runs/{run_id}/cancel", headers=headers,
                                     params={"session_id": "session-a"})
        foreign_get = client.get(f"/agents/demo/runs/{run_id}", headers=headers,
                                 params={"session_id": "session-a"})
        foreign_continue = client.post(f"/agents/demo/runs/{run_id}/continue", headers=headers,
                                      data={"session_id": "session-a", "stream": "false"})
        assert foreign_create.status_code in {403, 404}
        assert foreign_cancel.status_code in {403, 404}
        assert foreign_get.status_code in {403, 404}
        assert foreign_continue.status_code in {403, 404}


def test_jwt_auth_and_audience_are_enforced(isolated_os):
    client, token = isolated_os
    path = "/agents/demo/runs"
    data = {"message": "hello", "session_id": "s", "stream": "false"}
    for credential in (None, token("alice", audience="wrong"), token("alice", expired=True)):
        headers = {"Authorization": f"Bearer {credential}"} if credential else {}
        assert client.post(path, headers=headers, data=data).status_code == 401


def test_bauer_factory_jwt_isolation_and_channel_boundary(tmp_path, monkeypatch):
    """Exercise Bauer's factory and enforce ownership on run lifecycle routes."""
    from bauer.agentos_app import build_agentos_app
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    key = "local-factory-test-key-over-32-characters"
    for name in ("OS_SECURITY_KEY", "JWT_VERIFICATION_KEY", "JWT_JWKS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BAUER_AGENTOS_JWT_ISOLATION", "true")
    monkeypatch.setenv("BAUER_AGENTOS_CHANNEL_BOUNDARY", "true")
    monkeypatch.setenv("BAUER_AGENTOS_JWT_KEY", key)
    monkeypatch.setenv("BAUER_AGENTOS_JWT_AUDIENCE", "local-factory")
    monkeypatch.setenv("BAUER_AGENTOS_JWT_ISSUER", "local-issuer")
    monkeypatch.setattr(AgnoRuntimeAdapter, "_build_model", lambda self, spec: LocalOnlyModel(id="local"))
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: offline-echo\n", encoding="utf-8")
    app = build_agentos_app(config_path=config, runtime_root=tmp_path / "runtime")

    def auth(user):
        claims = {"sub": user, "aud": "local-factory", "iss": "local-issuer",
                  "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
                  "scopes": ["agents:bauer.dev:run", "agents:bauer.dev:read",
                             "agents:bauer.qa:run", "agents:bauer.qa:read",
                             "teams:bauer.software_team:run", "teams:bauer.software_team:read"]}
        return {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="HS256")}

    path = "/agents/bauer.dev/runs"
    with TestClient(app) as client:
        from bauer.agentos_capabilities import verify_capability_proof

        capabilities = "/bauer/channel-capabilities"
        assert client.get(capabilities).status_code == 401
        assert client.get(capabilities, headers=auth("alice")).status_code == 400
        challenge = "a" * 64
        response = client.get(capabilities, headers={**auth("alice"), "X-Bauer-Challenge": challenge})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["capabilities"] == {
            "contract_version": 1, "mode": "read_only", "user_isolation": True,
            "catalog_tools": False, "continuation": False, "cancel_requires_terminal": True,
            "identity": {"subject": "alice", "scopes": sorted([
                "agents:bauer.dev:run", "agents:bauer.dev:read", "agents:bauer.qa:run",
                "agents:bauer.qa:read", "teams:bauer.software_team:run", "teams:bauer.software_team:read",
            ])},
        }
        assert verify_capability_proof(key, challenge, body["capabilities"], body["proof"])
        assert not verify_capability_proof(key, "b" * 64, body["capabilities"], body["proof"])
        assert not verify_capability_proof("another-secret-with-more-than-32-bytes", challenge,
                                           body["capabilities"], body["proof"])
        assert client.get(capabilities + "?x=1", headers=auth("alice")).status_code == 403
        a = client.post(path, headers=auth("alice"),
                        data={"message": "hello", "session_id": "s-a", "user_id": "bob", "stream": "false"})
        assert a.status_code == 200, a.text
        assert a.json()["user_id"] == "alice"
        run_id = a.json()["run_id"]
        a_again = client.post(path, headers=auth("alice"),
                              data={"message": "again", "session_id": "s-a", "stream": "false"})
        assert a_again.status_code == 200, a_again.text
        b_forge = client.post(path, headers=auth("bob"),
                              data={"message": "injected", "session_id": "s-a",
                                    "user_id": "alice", "stream": "false"})
        assert b_forge.status_code in {403, 404}, b_forge.text
        b = client.post(path, headers=auth("bob"),
                        data={"message": "hello", "session_id": "s-b", "stream": "false"})
        assert b.status_code == 200 and b.json()["user_id"] == "bob"
        assert client.get(f"{path}/{run_id}", headers=auth("alice"),
                          params={"session_id": "s-a"}).status_code == 200
        assert client.get(f"{path}/{run_id}", headers=auth("bob"),
                          params={"session_id": "s-a"}).status_code in {403, 404}
        assert client.post(f"/agents/bauer.qa/runs/{run_id}/cancel", headers=auth("alice"),
                           params={"session_id": "s-a"}).status_code in {403, 404}
        assert client.post(f"{path}/{run_id}/cancel", headers=auth("bob"),
                           params={"session_id": "s-a"}).status_code in {403, 404}
        from bauer.agentos_approval_store import RemoteApprovalStore

        approval_store = RemoteApprovalStore(tmp_path / "runtime" / "agno" / "channel-approvals.db")
        record = approval_store.issue(owner_id="alice", entity_type="agents", entity_id="bauer.dev",
                                      run_id=run_id, session_id="s-a", tool_call_id="call-1",
                                      payload_digest="a" * 64, expires_at=time.time() + 60)
        approval_store.decide(record.approval_id, owner_id="alice", approve=True)
        approval_store.claim(record.approval_id, owner_id="alice", payload_digest="a" * 64)
        assert client.post(f"{path}/{run_id}/cancel", headers=auth("alice"),
                           params={"session_id": "s-a"}).status_code == 409
        approval_store.settle(record.approval_id, owner_id="alice", completed=True)
        assert client.get(f"{path}/{run_id}", headers=auth("alice")).status_code == 400
        assert client.post(f"{path}/{run_id}/cancel", headers=auth("alice"),
                           params=[("session_id", "s-a"), ("session_id", "s-b")]).status_code == 400
        assert client.post(f"{path}/{run_id}/cancel", headers=auth("alice"),
                           params={"session_id": "s-a"}).status_code == 409

        team_path = "/teams/bauer.software_team/runs"
        team = client.post(team_path, headers=auth("alice"),
                           data={"message": "hello", "session_id": "team-a", "stream": "false"})
        assert team.status_code == 200, team.text
        team_run = team.json()["run_id"]
        assert client.get(f"{team_path}/{team_run}", headers=auth("alice"),
                          params={"session_id": "team-a"}).status_code == 200
        assert client.get(f"{team_path}/{team_run}", headers=auth("bob"),
                          params={"session_id": "team-a"}).status_code in {403, 404}
        assert client.post(f"{team_path}/{team_run}/cancel", headers=auth("bob"),
                           params={"session_id": "team-a"}).status_code in {403, 404}
        assert client.post(f"{team_path}/{team_run}/cancel", headers=auth("alice"),
                           params={"session_id": "team-a"}).status_code == 409
