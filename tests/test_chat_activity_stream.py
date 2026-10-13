"""The chat publishes runtime metadata before executing the turn."""

import json

import pytest
from fastapi.testclient import TestClient

from tests.test_chat_project_context import chat_projects  # noqa: F401


@pytest.mark.parametrize("governed", [False, True])
def test_stream_initial_activity_is_separate_and_matches_persisted_run(chat_projects, monkeypatch, governed):
    build, _, _ = chat_projects

    def turn(ctx, *args, **kwargs):
        ctx.add_assistant("Resposta")
        return "Resposta", []

    monkeypatch.setattr("bauer.agent.run_one_turn", turn)
    with TestClient(build(governed=governed)) as client:
        response = client.get("/stream", params={"message": "Revise os testes"})
        frames = response.text.split("\n\n")
        assert frames[0].startswith("event: context\n")
        assert frames[1].startswith("event: internal\n")
        metadata = json.loads(frames[1].split("data: ", 1)[1])
        assert metadata["kind"] == "chat.activity"
        snapshot = metadata["payload"]
        run = snapshot["runs"][0]
        sid = response.headers["X-Session-ID"]
        assert snapshot["session_id"] == sid
        assert run["run_id"] == response.headers["X-Bauer-Run-ID"]
        assert run["status"] == "running"
        assert run["task"] == "Revise os testes"
        assert client.get("/chat/activity", params={"session_id": sid}).json()["runs"][0]["status"] == "completed"
        assert "data: Resposta" in response.text


def test_blocked_admission_publishes_terminal_activity_without_starting_turn(chat_projects, tmp_path, monkeypatch):
    from bauer.core.runtime.resilience import RuntimeControl
    from bauer.core.runtime.state_store import JsonlStateStore

    build, _, _ = chat_projects
    app = build(governed=True)
    RuntimeControl(store=JsonlStateStore(tmp_path / "runtime")).set_kill_switch(True)
    monkeypatch.setattr("bauer.agent.run_one_turn", lambda *a, **k: pytest.fail("LLM invoked"))
    with TestClient(app) as client:
        response = client.get("/stream", params={"message": "Revise os testes"})
    frames = response.text.split("\n\n")
    assert frames[0].startswith("event: context\n")
    assert frames[1].startswith("event: internal\n")
    metadata = json.loads(frames[1].split("data: ", 1)[1])
    assert metadata["kind"] == "chat.activity"
    snapshot = metadata["payload"]
    assert snapshot["runs"][0]["status"] == "cancelled"
    assert snapshot["runs"][0]["run_id"] == response.headers["X-Bauer-Run-ID"]


def test_accessory_snapshot_failure_does_not_break_response_or_claim_idle(chat_projects, monkeypatch):
    build, _, _ = chat_projects

    def turn(ctx, *args, **kwargs):
        ctx.add_assistant("Resposta preservada")
        return "Resposta preservada", []

    def unavailable(*args, **kwargs):
        raise OSError("private store failure")

    monkeypatch.setattr("bauer.agent.run_one_turn", turn)
    monkeypatch.setattr("bauer.server_activity.build_chat_activity", unavailable)
    with TestClient(build()) as client:
        response = client.get("/stream", params={"message": "Revise os testes"})
    assert 'event: internal\ndata: {"kind": "chat.activity", "payload": null}' in response.text
    assert "data: Resposta preservada" in response.text
    assert "private store failure" not in response.text


def test_agno_chat_persists_member_lifecycle_through_real_run_agent(chat_projects, monkeypatch):
    build, _, _ = chat_projects

    def stream(adapter, request):
        common = {"run_id": request["run_id"], "session_id": request["session_id"],
                  "agent_id": "reviewer", "data": {"agent_name": "Revisor", "task": "Validar testes"}}
        yield {"event": "task.delegated", "status": "delegated", **common}
        yield {"event": "agent.started", "status": "running", **common}
        yield {"event": "message.delta", "content": "Revisão concluída"}
        yield {"event": "agent.completed", "status": "completed", **common}
        yield {"event": "run.completed", "status": "completed", "metadata": {"tools": []}}

    monkeypatch.setattr("bauer.core.runtime.adapters.agno_adapter.AgnoRuntimeAdapter.stream_agent", stream)
    with TestClient(build(mode="agno", governed=True)) as client:
        response = client.get("/stream", params={"message": "Revise com um time"})
        sid = response.headers["X-Session-ID"]
        snapshot = client.get("/chat/activity", params={"session_id": sid}).json()
    assert "Revisão concluída" in response.text
    member = next(agent for agent in snapshot["runs"][0]["agents"] if agent["agent_id"] == "reviewer")
    assert member["name"] == "Revisor"
    assert member["task"] == "Validar testes"
    assert member["status"] == "completed"
