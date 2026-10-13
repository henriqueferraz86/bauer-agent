from __future__ import annotations

from types import SimpleNamespace

from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter


def test_agno_member_events_are_normalized_without_content_or_tool_payload():
    member_content = SimpleNamespace(
        event="RunContent",
        agent_id="bauer.research",
        agent_name="Bauer Research Agent",
        run_id="agno-member-run",
        parent_run_id="agno-team-run",
        session_id="session-1",
        content="private user content",
        reasoning_content="private reasoning",
    )

    normalized = AgnoRuntimeAdapter._normalize_agent_event(
        member_content,
        run_id="bauer-run",
        session_id="session-1",
        team_id="bauer.software_team",
        member_event=True,
    )

    assert normalized is not None
    assert normalized["event"] == "agent.message.sent"
    assert normalized["agent_id"] == "bauer.research"
    assert normalized["data"]["team_id"] == "bauer.software_team"
    assert "content" not in normalized and "reasoning_content" not in normalized

    tool_event = SimpleNamespace(
        event="ToolCallStarted",
        agent_id="bauer.research",
        run_id="agno-member-run",
        parent_run_id="agno-team-run",
        tool=SimpleNamespace(tool_name="web_search", tool_args={"query": "secret query"}),
    )
    normalized_tool = AgnoRuntimeAdapter._normalize_agent_event(
        tool_event,
        run_id="bauer-run",
        session_id="session-1",
        team_id="bauer.software_team",
        member_event=True,
    )
    assert normalized_tool is not None
    assert normalized_tool["event"] == "tool.call.requested"
    assert normalized_tool["tool_name"] == "web_search"
    assert "tool_args" not in normalized_tool["data"]


def test_agno_task_and_tool_event_enums_are_normalized():
    assert AgnoRuntimeAdapter._agno_event_name(SimpleNamespace(event="team_tool_call_started")) == "ToolCallStarted"
    assert AgnoRuntimeAdapter._agno_event_name(SimpleNamespace(event="TeamToolCallStarted")) == "ToolCallStarted"
    assert AgnoRuntimeAdapter._agno_event_name(SimpleNamespace(event="RunEvent.run_started")) == "RunStarted"


def test_installed_agno_task_events_preserve_assignment_and_task_without_result():
    from agno.run.team import TaskCreatedEvent, TaskUpdatedEvent

    created = TaskCreatedEvent(task_id="task-1", title="Revisar", description="Revisar testes",
                               assignee="reviewer", status="pending")
    updated = TaskUpdatedEvent(task_id="task-1", title="Revisar", assignee="reviewer",
                               status="completed", result="private result")
    events = [AgnoRuntimeAdapter._normalize_agent_event(
        event, run_id="run", session_id="session", team_id="team", member_event=False,
    ) for event in (created, updated)]
    assert events[0]["status"] == "queued"
    assert events[0]["data"]["task"] == "Revisar testes"
    assert events[1]["status"] == "completed"
    assert events[1]["agent_id"] == "reviewer"
    assert "result" not in events[1]["data"]


def test_run_agent_forwards_public_lifecycle_with_best_effort_sink(monkeypatch):
    seen = []
    adapter = AgnoRuntimeAdapter(activity_sink=seen.append)
    lifecycle = {"event": "agent.started", "agent_id": "reviewer"}
    monkeypatch.setattr(adapter, "stream_agent", lambda request: iter([
        lifecycle, {"event": "message.delta", "content": "answer"},
        {"event": "run.completed", "status": "completed"},
    ]))
    assert adapter.run_agent({"run_id": "run"})["output"] == "answer"
    assert seen == [lifecycle]

    def broken(event):
        raise OSError("store unavailable")

    adapter._activity_sink = broken
    assert adapter.run_agent({"run_id": "run"})["status"] == "completed"


def test_run_agent_preserves_member_tool_identity_without_duplicating_root_tools(monkeypatch):
    seen = []
    adapter = AgnoRuntimeAdapter(activity_sink=seen.append)
    member = {"event": "tool.call.requested", "agent_id": "reviewer", "tool_name": "read_file",
              "data": {"member_event": True}}
    root = {**member, "data": {"member_event": False}}
    monkeypatch.setattr(adapter, "stream_agent", lambda request: iter([
        member, root, {"event": "run.completed", "output": "answer"},
    ]))
    assert adapter.run_agent({"run_id": "run"})["output"] == "answer"
    assert seen == [member]
