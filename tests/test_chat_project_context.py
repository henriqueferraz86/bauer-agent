"""Projeto público e ferramentas/prompt efetivos dos turnos do chat."""

import json

import pytest
from fastapi.testclient import TestClient

from bauer import projects_registry as pr
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.session_manager import SessionManager
from bauer.server import create_app
from bauer.tool_router import ToolRouter


@pytest.fixture
def chat_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "_DEFAULT_REGISTRY", tmp_path / "projects.json")
    monkeypatch.setattr("bauer.server._MEMORY_PREFETCH_ENABLED", False)
    monkeypatch.setattr("bauer.commands._runtime._build_router",
                        lambda cfg, path, **kwargs: ToolRouter(workspace=path))
    default = tmp_path / "default"
    default.mkdir()
    (default / "DEFAULT_MARKER.txt").write_text("default")
    projects = []
    for name in ("A", "B"):
        path = tmp_path / name
        path.mkdir()
        (path / f"{name}_MARKER.txt").write_text(name)
        (path / "PROJECT.md").write_text(f"brief-{name}: convenções deste projeto para validar o contexto do turno.")
        projects.append(pr.add_project(path))
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: controlled\nkernel:\n  enabled: false\n")

    def build(*, mode="bauer_native", api_key="", governed=False):
        runtime = tmp_path / "runtime"
        runtime.mkdir(exist_ok=True)
        (runtime / "serve-runtime-mode.json").write_text(json.dumps({"mode": mode}))
        if governed:
            config.write_text("model:\n  provider: ollama\n  name: controlled\nkernel:\n  enabled: true\n")
        return create_app(
            model_name="controlled", applied_context=4096, client=object(),
            router=ToolRouter(workspace=default), system_prompt="boot",
            sessions_dir=tmp_path / "sessions", config_path=config,
            api_key=api_key, web_auth_enabled=False, rate_limit_requests=0,
        )

    return build, projects, default


def stream_context(response):
    first_frame = response.text.split("\n\n")[0]
    assert first_frame.startswith("event: context\n")
    return json.loads(first_frame.split("data: ", 1)[1])


def test_context_lookup_is_authenticated_and_does_not_create_runs_or_sessions(chat_projects, tmp_path):
    build, projects, _ = chat_projects
    with TestClient(build(api_key="test-key")) as client:
        assert client.get("/chat/context").status_code == 401
        context = client.get("/chat/context", headers={"X-API-Key": "test-key"}).json()
    assert context == {
        "session_id": None, "project": {key: projects[0][key] for key in ("id", "name", "path")},
        "workspace": projects[0]["path"], "source": "active",
        "active_project": {key: projects[0][key] for key in ("id", "name", "path")}, "warning": None,
    }
    assert RunManager(root=tmp_path / "runtime").list_runs() == []
    assert SessionManager(root=tmp_path / "runtime").list_sessions() == []


@pytest.mark.parametrize("failure", ["unknown", "missing", "builder", "cached_missing"])
def test_context_reports_actual_default_on_fallback(chat_projects, monkeypatch, failure):
    build, projects, default = chat_projects
    with TestClient(build()) as client:
        pid = projects[0]["id"]
        if failure == "unknown":
            pid = "unknown"
        elif failure in {"missing", "cached_missing"}:
            if failure == "cached_missing":
                assert client.get("/chat/context").json()["project"]["id"] == pid
            from pathlib import Path
            import shutil
            shutil.rmtree(Path(projects[0]["path"]))
        else:
            def broken_builder(*args, **kwargs):
                raise ValueError("sensitive internal failure")
            monkeypatch.setattr("bauer.commands._runtime._build_router", broken_builder)
        context = client.get("/chat/context", params={"project_id": pid}).json()
    assert context["project"] is None
    assert context["workspace"] == str(default)
    assert context["source"] == "default"
    assert "workspace padrão" in context["warning"]
    assert "sensitive internal" not in context["warning"]


@pytest.mark.parametrize("mode", ["bauer_native", "agno"])
@pytest.mark.parametrize("transport", ["chat", "stream"])
def test_turn_project_prompt_tools_and_sticky_session_agree(chat_projects, tmp_path, monkeypatch, mode, transport):
    build, projects, default = chat_projects
    seen = []

    def native_turn(ctx, router, *args, **kwargs):
        seen.append((router, "\n".join(str(item["content"]) for item in ctx.get_payload())))
        output = router.execute_native_call("list_dir", {"path": "."})
        ctx.add_assistant(output)
        return output, []

    def agno_turn(adapter, request):
        router = adapter._build_tool_router()
        assert str(adapter.workspace) == str(router.workspace)
        seen.append((router, request["agent_spec"]["instructions"]))
        output = adapter._bauer_tool_callable("list_dir")(".")
        return {"status": "completed", "output": output, "metadata": {"tools": []}}

    monkeypatch.setattr("bauer.agent.run_one_turn", native_turn)
    monkeypatch.setattr("bauer.core.runtime.adapters.agno_adapter.AgnoRuntimeAdapter.run_agent", agno_turn)

    def send(client, sid=None):
        payload = {"message": "resuma projeto"}
        if sid:
            payload["session_id"] = sid
        if transport == "stream":
            response = client.get("/stream", params=payload)
            context = stream_context(response)
            assert context["session_id"] == response.headers["X-Session-ID"]
            return response, context["session_id"]
        response = client.post("/chat", json=payload)
        assert response.status_code == 200, response.text
        return response, response.json()["session_id"]

    with TestClient(build(mode=mode)) as client:
        response, sid = send(client)
        assert "A_MARKER" in response.text and "DEFAULT_MARKER" not in response.text
        assert str(seen[-1][0].workspace) == projects[0]["path"]
        assert projects[0]["path"] in seen[-1][1] and "brief-A" in seen[-1][1]
        # Both engines must publish real tools into the Server's event store.
        from bauer.core.events import EventBus
        bus = EventBus(root=tmp_path / "runtime")
        assert any(event.tool_name == "list_dir" for event in bus.list_events())
        pr.set_active(projects[1]["id"])
        context = client.get("/chat/context", params={"session_id": sid}).json()
        assert context["project"]["id"] == projects[0]["id"]
        assert context["active_project"]["id"] == projects[1]["id"]
        assert context["source"] == "session" and context["warning"]
        assert "A_MARKER" in send(client, sid)[0].text
        assert "B_MARKER" in send(client)[0].text
        explicit = client.get("/chat/context", params={"session_id": sid, "project_id": projects[1]["id"]}).json()
        assert explicit["project"]["id"] == projects[1]["id"] and explicit["source"] == "explicit"


def test_nested_active_project_does_not_redirect_sticky_parent_session(chat_projects, monkeypatch, tmp_path):
    from pathlib import Path

    build, projects, _ = chat_projects
    seen = []
    def turn(ctx, router, *args, **kwargs):
        seen.append("\n".join(str(item["content"]) for item in ctx.get_payload()))
        ctx.add_assistant("ok")
        return "ok", []
    monkeypatch.setattr("bauer.agent.run_one_turn", turn)
    with TestClient(build()) as client:
        sid = client.post("/chat", json={"message": "hello"}).json()["session_id"]
        nested = Path(projects[0]["path"]) / "child"
        nested.mkdir()
        child = pr.add_project(nested)
        pr.set_active(child["id"])
        response = client.post("/chat", json={"message": "hello", "session_id": sid})
        assert response.status_code == 200
    assert "<projeto-ativo>" not in seen[-1]
    assert f"Workspace efetivo deste turno: {projects[0]['path']}." in seen[-1]


@pytest.mark.parametrize("helper", ["bauer.agent._build_system_prompt", "bauer.app_factory.system_prompt_section"])
def test_accessory_prompt_failure_keeps_effective_workspace(chat_projects, monkeypatch, helper):
    build, projects, _ = chat_projects
    seen = []
    def turn(ctx, *args, **kwargs):
        seen.append(ctx.system_prompt)
        ctx.add_assistant("ok")
        return "ok", []
    def broken(*args, **kwargs):
        raise ValueError("accessory unavailable")
    monkeypatch.setattr("bauer.agent.run_one_turn", turn)
    monkeypatch.setattr(helper, broken)
    with TestClient(build()) as client:
        assert client.post("/chat", json={"message": "hello"}).status_code == 200
    assert f"Workspace efetivo deste turno: {projects[0]['path']}." in seen[0]


def test_blocked_stream_still_emits_project_context_first(chat_projects, tmp_path, monkeypatch):
    from bauer.core.runtime.resilience import RuntimeControl
    from bauer.core.runtime.state_store import JsonlStateStore

    build, projects, _ = chat_projects
    app = build(governed=True)
    RuntimeControl(store=JsonlStateStore(tmp_path / "runtime")).set_kill_switch(True)
    def unexpected_turn(*args, **kwargs):
        pytest.fail("blocked turn called provider")
    monkeypatch.setattr("bauer.agent.run_one_turn", unexpected_turn)
    with TestClient(app) as client:
        response = client.get("/stream", params={"message": "oi"})
    context = stream_context(response)
    assert context["project"]["id"] == projects[0]["id"]
    assert context["workspace"] == projects[0]["path"]
    assert context["session_id"] == response.headers["X-Session-ID"]
    assert "kill switch" in response.text
