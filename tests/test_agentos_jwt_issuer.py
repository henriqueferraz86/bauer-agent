import time

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bauer.agentos_jwt_claims import AgentOSJWTClaimsMiddleware
from bauer.agentos_jwt_issuer import AgentOSJWTIssuer

KEY = "0123456789abcdefghijklmnopqrstuvwxyz0123456789"
SCOPES = ["agents:bauer.dev:run", "agents:bauer.dev:read"]


def issuer(**kwargs):
    return AgentOSJWTIssuer(key=KEY, audience="agentos", issuer="gateway",
                            allowed_scopes=SCOPES, **kwargs)


def test_token_has_stable_opaque_subject_and_narrow_expiry():
    signer = issuer(lifetime_s=60)
    token = signer.issue(authenticated_user_id="123456", scopes=[SCOPES[0]])
    claims = jwt.decode(token, KEY, algorithms=["HS256"], audience="agentos", issuer="gateway")
    assert claims["sub"].startswith("tg-") and "123456" not in claims["sub"]
    assert claims["scopes"] == [SCOPES[0]]
    assert claims["exp"] - claims["iat"] == 60 and abs(time.time() - claims["iat"]) < 5
    another = signer.issue(authenticated_user_id="123457", scopes=[SCOPES[0]])
    assert jwt.decode(another, KEY, algorithms=["HS256"], audience="agentos")["sub"] != claims["sub"]
    assert "0123456789" not in repr(signer)


@pytest.mark.parametrize("scope", ["agent_os:admin", "agents:*:run", "agents:bauer.other:run", "teams:x:write", "*"])
def test_scopes_cannot_exceed_allowlist(scope):
    with pytest.raises(ValueError):
        issuer().issue(authenticated_user_id="123", scopes=[scope])


@pytest.mark.parametrize("scope", ["agent_os:admin", "agents:*:run", "agents:x:write"])
def test_invalid_scope_cannot_enter_allowlist(scope):
    with pytest.raises(ValueError):
        AgentOSJWTIssuer(key=KEY, audience="agentos", issuer="gateway", allowed_scopes=[scope])


@pytest.mark.parametrize("user", ["", "0", "01", "-1", " 123", "١٢٣", "1" * 33])
def test_requires_canonical_authenticated_id(user):
    with pytest.raises(ValueError):
        issuer().issue(authenticated_user_id=user, scopes=[SCOPES[0]])


@pytest.mark.parametrize("lifetime", [0, -1, 301, True])
def test_lifetime_bounded(lifetime):
    with pytest.raises(ValueError):
        issuer(lifetime_s=lifetime)


def test_issued_token_passes_claims_boundary_without_admin():
    app = FastAPI()
    @app.get("/agents/a/runs")
    def route():
        return {"ok": True}
    app.add_middleware(AgentOSJWTClaimsMiddleware, key=KEY, audience="agentos", issuer="gateway")
    token = issuer().issue(authenticated_user_id="42", scopes=[SCOPES[0]])
    with TestClient(app) as client:
        assert client.get("/agents/a/runs", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert client.get("/agents/a/runs").status_code == 401


def test_issued_token_executes_real_bauer_factory_with_resource_scope(tmp_path, monkeypatch):
    pytest.importorskip("agno")
    from agno.models.base import Model
    from agno.models.response import ModelResponse
    from bauer.agentos_app import build_agentos_app
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    class LocalModel(Model):
        def invoke(self, *args, **kwargs):
            return ModelResponse(role="assistant", content="local response")
        async def ainvoke(self, *args, **kwargs):
            return self.invoke(*args, **kwargs)
        def invoke_stream(self, *args, **kwargs):
            yield self.invoke(*args, **kwargs)
        async def ainvoke_stream(self, *args, **kwargs):
            yield self.invoke(*args, **kwargs)
        def _parse_provider_response(self, response, **kwargs):
            return response
        def _parse_provider_response_delta(self, response):
            return response

    for name in ("OS_SECURITY_KEY", "JWT_VERIFICATION_KEY", "JWT_JWKS_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BAUER_AGENTOS_JWT_ISOLATION", "true")
    monkeypatch.setenv("BAUER_AGENTOS_CHANNEL_BOUNDARY", "true")
    monkeypatch.setenv("BAUER_AGENTOS_JWT_KEY", KEY)
    monkeypatch.setenv("BAUER_AGENTOS_JWT_AUDIENCE", "agentos")
    monkeypatch.setenv("BAUER_AGENTOS_JWT_ISSUER", "gateway")
    monkeypatch.setattr(AgnoRuntimeAdapter, "_build_model", lambda self, spec: LocalModel(id="local"))
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: offline\n", encoding="utf-8")
    app = build_agentos_app(config_path=config, runtime_root=tmp_path / "runtime")
    token = issuer().issue(authenticated_user_id="123", scopes=[SCOPES[0]])
    body = {"message": "hello", "session_id": "session-1", "stream": "false"}
    with TestClient(app) as client:
        result = client.post("/agents/bauer.dev/runs", headers={"Authorization": f"Bearer {token}"}, data=body)
        assert result.status_code == 200, result.text
        assert result.json()["content"] == "local response"
        claims = jwt.decode(token, KEY, algorithms=["HS256"], audience="agentos")
        assert result.json()["user_id"] == claims["sub"]
        refused = client.post("/agents/bauer.qa/runs", headers={"Authorization": f"Bearer {token}"}, data=body)
        assert refused.status_code == 403
