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
