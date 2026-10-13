"""Public lifecycle evidence from actual delegated execution, without outputs."""

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from bauer.core.events import EventBus
from bauer.runtime_ids import reset_runtime_ids, set_runtime_ids
from bauer.tool_router import ToolError, ToolRouter


@pytest.fixture
def delegated_router(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    client = SimpleNamespace(default_model="controlled", chat_stream=lambda *args: iter(["private result"]))
    router = ToolRouter(workspace=workspace, llm_client=client)
    router._event_bus = EventBus(root=tmp_path / "runtime")
    agent = SimpleNamespace(name="research-specialist", system="private system", model="", url="", api_key="")
    monkeypatch.setattr(router, "_resolve_delegate_agent", lambda *args: (agent, False, "unused-agents.yaml"))
    monkeypatch.setattr("bauer.tools.execution._find_bauer_python", lambda *args: "controlled-python")
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: pytest.fail("unexpected subprocess"))
    return router, agent


def delegate(router, task="investigue os testes", *, session="session-a", run="run-a", **kwargs):
    token = set_runtime_ids(session, run)
    try:
        return router._delegate_task({"task": task, **kwargs})
    finally:
        reset_runtime_ids(token)


def test_resolved_agent_publishes_started_before_execution_and_completed_after(delegated_router):
    router, _ = delegated_router
    during = []
    def stream(*args):
        during.extend(router._event_bus.list_events())
        yield "private result"
    router._llm_client.chat_stream = stream
    result = delegate(router, context="private context", agent_name="caller alias")
    events = router._event_bus.list_events()
    assert [event.event_type for event in during] == ["task.delegated", "agent.started"]
    assert [event.event_type for event in events] == ["task.delegated", "agent.started", "agent.completed"]
    assert [event.status for event in events] == ["delegated", "running", "completed"]
    assert {event.agent_id for event in events} == {"research-specialist"}
    assert {event.session_id for event in events} == {"session-a"}
    assert {event.run_id for event in events} == {"run-a"}
    assert len({event.data["delegation_id"] for event in events}) == 1
    assert all(event.data["task"] == "investigue os testes" for event in events)
    assert "private result" in result
    public = json.dumps([EventBus.to_dict(event) for event in events])
    for value in ("private result", "private context", "private system", "caller alias", "tool_args", "result_preview"):
        assert value not in public


def test_each_delegation_has_own_identity_even_for_same_resolved_agent(delegated_router):
    router, _ = delegated_router
    delegate(router, "primeira tarefa")
    delegate(router, "segunda tarefa")
    delegated = [event for event in router._event_bus.list_events() if event.event_type == "task.delegated"]
    assert len({event.agent_id for event in delegated}) == 1
    assert len({event.data["delegation_id"] for event in delegated}) == 2
    assert [event.data["task"] for event in delegated] == ["primeira tarefa", "segunda tarefa"]


def test_generic_delegate_is_identified_without_inventing_a_specialist(delegated_router, monkeypatch):
    router, _ = delegated_router
    monkeypatch.setattr(router, "_resolve_delegate_agent", lambda *args: (None, False, "unused"))
    delegate(router)
    events = router._event_bus.list_events()
    assert all(event.agent_id == event.data["delegation_id"] for event in events)
    assert all(event.data["agent_name"] == "Subagente" for event in events)


def test_failed_subprocess_marks_failed_without_error_or_output_payload(delegated_router, monkeypatch):
    router, _ = delegated_router
    router._llm_client = None
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("private command", 10, output="private output", stderr="private error")
    monkeypatch.setattr("subprocess.run", timeout)
    with pytest.raises(ToolError, match="timeout"):
        delegate(router)
    events = router._event_bus.list_events()
    assert [event.event_type for event in events] == ["task.delegated", "agent.started", "agent.failed"]
    assert events[-1].status == "failed"
    assert all(event.message is None for event in events)
    public = json.dumps([EventBus.to_dict(event) for event in events])
    assert "private output" not in public and "private error" not in public


def test_failed_local_provider_can_fallback_and_report_final_completed(delegated_router, monkeypatch):
    router, _ = delegated_router
    def unavailable(*args):
        raise OSError("private provider failure")
    router._llm_client.chat_stream = unavailable
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="fallback result", stderr=""))
    assert "fallback result" in delegate(router)
    events = router._event_bus.list_events()
    assert [event.event_type for event in events] == ["task.delegated", "agent.started", "agent.completed"]


def test_public_task_and_name_are_redacted_before_truncation(delegated_router):
    router, agent = delegated_router
    secret = "sk-" + "a" * 32
    agent.name = f"research-{secret}"
    delegate(router, "x" * 490 + " " + secret + " remainder")
    events = router._event_bus.list_events()
    public = json.dumps([EventBus.to_dict(event) for event in events])
    assert secret not in public
    assert "[REDACTED]" in events[0].agent_id
    assert all(len(event.data["task"]) <= 500 for event in events)
    assert all(len(event.data["agent_name"]) <= 160 for event in events)


def test_observability_failure_does_not_change_delegated_result(delegated_router):
    router, _ = delegated_router
    def unavailable(*args, **kwargs):
        raise OSError("event store unavailable")
    router._event_bus = SimpleNamespace(publish=unavailable)
    assert "private result" in delegate(router)


def test_parallel_delegations_keep_parent_session_and_run_separate(delegated_router):
    router, _ = delegated_router
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(delegate, router, "task-a", session="session-a", run="run-a")
        b = pool.submit(delegate, router, "task-b", session="session-b", run="run-b")
        assert "private result" in a.result(timeout=5)
        assert "private result" in b.result(timeout=5)
    events = router._event_bus.list_events()
    for event in events:
        assert (event.session_id, event.run_id, event.data["task"]) in {
            ("session-a", "run-a", "task-a"), ("session-b", "run-b", "task-b"),
        }
    assert len({event.data["delegation_id"] for event in events}) == 2
