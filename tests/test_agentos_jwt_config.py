"""Optional JWT isolation settings on the Bauer AgentOS app."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from bauer.agentos_app import AgentOSBuildError, build_agentos_app

jwt = pytest.importorskip("jwt")
pytest.importorskip("agno")


KEY = "test-only-key-with-32-characters-minimum"


@pytest.fixture
def jwt_env(monkeypatch):
    for name in ("OS_SECURITY_KEY", "JWT_VERIFICATION_KEY", "JWT_JWKS_FILE", "BAUER_AGENTOS_CHANNEL_BOUNDARY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BAUER_AGENTOS_JWT_ISOLATION", "true")
    monkeypatch.setenv("BAUER_AGENTOS_CHANNEL_BOUNDARY", "true")
    monkeypatch.setenv("BAUER_AGENTOS_JWT_KEY", KEY)
    monkeypatch.setenv("BAUER_AGENTOS_JWT_AUDIENCE", "bauer-test")
    monkeypatch.setenv("BAUER_AGENTOS_JWT_ISSUER", "trusted-test-issuer")


def test_jwt_misconfiguration_fails_closed(jwt_env, monkeypatch, tmp_path):
    for name in ("BAUER_AGENTOS_JWT_KEY", "BAUER_AGENTOS_JWT_AUDIENCE", "BAUER_AGENTOS_JWT_ISSUER"):
        with monkeypatch.context() as temporary:
            temporary.setenv(name, "")
            with pytest.raises(AgentOSBuildError):
                build_agentos_app(runtime_root=tmp_path)
    monkeypatch.setenv("OS_SECURITY_KEY", "other-key")
    with pytest.raises(AgentOSBuildError, match="alternativas"):
        build_agentos_app(runtime_root=tmp_path)


def test_jwt_requires_channel_boundary(jwt_env, monkeypatch, tmp_path):
    monkeypatch.delenv("BAUER_AGENTOS_CHANNEL_BOUNDARY")
    with pytest.raises(AgentOSBuildError, match="CHANNEL_BOUNDARY"):
        build_agentos_app(runtime_root=tmp_path)


def test_external_tools_require_distinct_secrets(jwt_env, monkeypatch, tmp_path):
    monkeypatch.setenv("BAUER_AGENTOS_EXTERNAL_READ_TOOLS", "true")
    with pytest.raises(AgentOSBuildError, match="chaves independentes"):
        build_agentos_app(runtime_root=tmp_path)
    monkeypatch.setenv("BAUER_TOOL_GATEWAY_REQUIREMENT_KEY", KEY)
    monkeypatch.setenv("BAUER_TOOL_GATEWAY_CONTINUATION_KEY", "continuation-test-key-more-than-32-bytes")
    with pytest.raises(AgentOSBuildError, match="chaves independentes"):
        build_agentos_app(runtime_root=tmp_path)


def test_external_tools_factory_exposes_scoped_controlled_routes(jwt_env, monkeypatch, tmp_path):
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
    from bauer.tool_gateway_protocol import call_digest, continuation_proof, verify_requirement_proof
    from tests.test_agentos_gateway_chain import _ReadModel

    requirement_key = "requirement-secret-at-least-32-bytes"
    continuation_key = "continuation-secret-at-least-32-bytes"
    monkeypatch.setenv("BAUER_AGENTOS_EXTERNAL_READ_TOOLS", "true")
    monkeypatch.setenv("BAUER_TOOL_GATEWAY_REQUIREMENT_KEY", requirement_key)
    monkeypatch.setenv("BAUER_TOOL_GATEWAY_CONTINUATION_KEY", continuation_key)
    monkeypatch.setattr(AgnoRuntimeAdapter, "_build_model", lambda self, spec: _ReadModel(id="stub"))
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: offline-echo\n", encoding="utf-8")
    app = build_agentos_app(config_path=config, runtime_root=tmp_path / "runtime")
    claims = {"sub": "alice", "aud": "bauer-test", "iss": "trusted-test-issuer",
              "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
              "scopes": ["agents:bauer.dev:run", "agents:bauer.dev:read"]}
    headers = {"Authorization": "Bearer " + jwt.encode(claims, KEY, algorithm="HS256")}
    with TestClient(app) as client:
        started = client.post("/agents/bauer.dev/runs", headers=headers,
                              data={"message": "read", "session_id": "s", "stream": "false"})
        assert started.status_code == 200, started.text
        paused = started.json()
        assert paused["status"].upper() == "PAUSED"
        payload = {"agent_id": "bauer.dev", "run_id": paused["run_id"], "session_id": "s", "challenge": "a" * 64}
        attested = client.post("/bauer/tool-requirement", headers=headers, json=payload)
        assert attested.status_code == 200, attested.text
        statement = attested.json()["statement"]
        assert verify_requirement_proof(requirement_key, payload["challenge"], statement, attested.json()["proof"])
        body = {"agent_id": "bauer.dev", "run_id": paused["run_id"], "session_id": "s",
                "tool_call_id": statement["tool_call_id"], "call_digest": call_digest(statement["action"], statement["args"]),
                "result": "from gateway"}
        body["proof"] = continuation_proof(continuation_key, subject="alice", **body)
        assert client.post("/agents/bauer.dev/runs/" + paused["run_id"] + "/continue", headers=headers,
                           data={"session_id": "s"}).status_code == 403
        completed = client.post("/bauer/tool-continue", headers=headers, json=body)
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"].upper() == "COMPLETED"
        assert client.post("/bauer/tool-continue", headers=headers, json=body).status_code == 409


def test_jwt_requires_subject_audience_issuer_expiry_and_scopes(jwt_env, monkeypatch, tmp_path):
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
    from agno.models.base import Model
    from agno.models.response import ModelResponse

    class LocalOnlyModel(Model):
        def invoke(self, *args, **kwargs):
            return ModelResponse(role="assistant", content="local")

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

    monkeypatch.setattr(AgnoRuntimeAdapter, "_build_model", lambda self, spec: LocalOnlyModel(id="local"))
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: offline-echo\n", encoding="utf-8")
    app = build_agentos_app(config_path=config, runtime_root=tmp_path / "runtime")
    claims = {"sub": "alice", "aud": "bauer-test", "iss": "trusted-test-issuer",
              "exp": datetime.now(timezone.utc) + timedelta(minutes=5), "scopes": ["agents:bauer.dev:run"]}

    def auth(payload, key=KEY):
        return {"Authorization": "Bearer " + jwt.encode(payload, key, algorithm="HS256")}

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        path = "/agents/bauer.dev/runs"
        data = {"message": "ola", "session_id": "jwt-test", "stream": "false"}
        assert client.post(path, data=data).status_code == 401
        assert client.post(path, data=data, headers=auth(claims)).status_code == 200
        assert client.post("/api/models/select", json={}, headers=auth(claims)).status_code == 403
        for field in ("sub", "aud", "iss", "exp", "scopes"):
            invalid = dict(claims)
            del invalid[field]
            assert client.post(path, data=data, headers=auth(invalid)).status_code == 401
        for change in ({"sub": ""}, {"aud": "wrong"}, {"iss": "wrong"},
                       {"exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                       {"scopes": ["agent_os:admin"]}):
            assert client.post(path, data=data, headers=auth({**claims, **change})).status_code == 401
        assert client.post(path, data=data, headers=auth(claims, "wrong-key-with-32-characters-minimum")).status_code == 401
        assert client.post(path, data=data, headers=auth({**claims, "scopes": ["agents:bauer.other:run"]})).status_code == 403
