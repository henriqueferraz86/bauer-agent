"""The optional boundary narrows the API; it does not authenticate users."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from bauer.agentos_channel_boundary import ChannelBoundaryMiddleware


@pytest.fixture
def client():
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "DELETE"])
    async def endpoint(request: Request):
        return {"path": request.url.path, "body": (await request.body()).decode("utf-8")}

    app.add_middleware(ChannelBoundaryMiddleware, agent_ids={"bauer.dev"}, team_ids={"bauer.team"})
    return TestClient(app)


def test_boundary_allows_only_declared_execution_paths(client):
    assert client.get("/health").status_code == 200
    assert client.post("/agents/bauer.dev/runs", files={"message": (None, "oi")}).status_code == 200
    response = client.post("/teams/bauer.team/runs", data={"message": "oi"})
    assert response.status_code == 200 and response.json()["body"] == "message=oi"
    assert client.get("/agents/bauer.dev/runs/run-1?session_id=s").status_code == 403
    assert client.post("/teams/bauer.team/runs/run-1/cancel?session_id=s").status_code == 403
    for path in ("/config", "/agents", "/components", "/sessions", "/api/models/select",
                 "/agents/persisted/runs", "/agents/bauer.dev/runs/run-1/continue"):
        assert client.post(path, data={"message": "oi"}).status_code == 403


@pytest.mark.parametrize("fields", [
    [("message", "oi"), ("version", "1")],
    [("message", "oi"), ("factory_input", "{}")],
    [("message", "oi"), ("message", "outra")],
    [("message", "oi"), ("background", "true")],
])
def test_boundary_rejects_extra_and_duplicate_form_fields(client, fields):
    assert client.post("/agents/bauer.dev/runs", files=[(k, (None, v)) for k, v in fields]).status_code == 400


def test_boundary_rejects_uploads_invalid_forms_queries_and_large_bodies(client):
    path = "/agents/bauer.dev/runs"
    assert client.post(path, files={"message": (None, "oi"), "files": ("a.txt", b"x")}).status_code == 400
    assert client.post(path, content=b"broken", headers={"Content-Type": "multipart/form-data"}).status_code == 400
    assert client.post(path, json={"message": "oi"}).status_code == 400
    assert client.post(path + "?version=1", data={"message": "oi"}).status_code == 403
    assert client.get(path + "/id?session_id=a&session_id=b").status_code == 403
    assert client.post(path, content=b"x" * (256 * 1024 + 1),
                       headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 413


def test_jwt_subject_and_session_are_bound_to_router_context():
    from bauer.core.runtime.adapters.agno_adapter import _ACTIVE_AGNO_SESSION_ID

    app = FastAPI()

    @app.post("/agents/bauer.dev/runs")
    async def fake_run():
        return {"context": _ACTIVE_AGNO_SESSION_ID.get()}

    app.add_middleware(ChannelBoundaryMiddleware, agent_ids={"bauer.dev"}, team_ids=set(), jwt_isolation=True)

    @app.middleware("http")
    async def verified_identity(request, call_next):
        # This fixture stands in for the separately tested JWT verifier.
        request.scope.setdefault("state", {})["bauer_jwt_identity"] = {"subject": request.headers.get("x-test-user")}
        return await call_next(request)

    with TestClient(app) as client:
        def run(subject, session):
            return client.post("/agents/bauer.dev/runs", headers={"x-test-user": subject},
                               data={"message": "oi", "session_id": session})

        a = run("alice", "same-session")
        b = run("bob", "same-session")
        assert a.status_code == b.status_code == 200
        assert a.json()["context"] != b.json()["context"]
        assert run("alice", "same-session").json()["context"] == a.json()["context"]
        assert run("alice", "other-session").json()["context"] != a.json()["context"]
        assert client.post("/agents/bauer.dev/runs", headers={"x-test-user": "alice"},
                           data={"message": "oi"}).status_code == 400
        assert _ACTIVE_AGNO_SESSION_ID.get() == ""


def test_real_agentos_boundary_requires_key_and_removes_tools(monkeypatch, tmp_path):
    pytest.importorskip("agno")
    from bauer.agentos_app import AgentOSBuildError, build_agentos_app
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    monkeypatch.setenv("BAUER_AGENTOS_CHANNEL_BOUNDARY", "true")
    monkeypatch.delenv("OS_SECURITY_KEY", raising=False)
    with pytest.raises(AgentOSBuildError, match="OS_SECURITY_KEY"):
        build_agentos_app(runtime_root=tmp_path)
    monkeypatch.setenv("OS_SECURITY_KEY", "local-test-secret")
    mapped = []
    original = AgnoRuntimeAdapter._map_tools

    def capture(self, tools):
        mapped.append(tools)
        return original(self, tools)

    monkeypatch.setattr(AgnoRuntimeAdapter, "_map_tools", capture)
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: offline-echo\n", encoding="utf-8")
    app = build_agentos_app(config_path=config, runtime_root=tmp_path / "runtime")
    assert mapped and all(not tools for tools in mapped)
    with TestClient(app) as actual:
        headers = {"Authorization": "Bearer local-test-secret"}
        assert actual.get("/health").status_code == 200
        assert actual.post("/components", headers=headers, json={}).status_code == 403
        assert actual.post("/agents/not-in-catalog/runs", headers=headers, data={"message": "a"}).status_code == 403
        assert actual.post("/agents/bauer.dev/runs", data={"message": "a"}).status_code in (401, 403)
