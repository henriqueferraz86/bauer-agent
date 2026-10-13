"""Read-only chat activity projection from real runtime stores/events."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from bauer.core.events import EventBus
from bauer.core.runtime.run_manager import RunManager
from bauer.server_activity import MAX_AGENTS, MAX_EVENTS_PER_RUN, MAX_RUNS, MAX_TASK_TEXT, build_chat_activity
from bauer.server_observability import register_observability_routes


@pytest.fixture
def runtime(tmp_path):
    bus = EventBus(root=tmp_path / "runtime")
    runs = RunManager(store=bus.store, event_bus=bus)
    return runs, bus


def new_run(runs, *, sid="chat", agent="serve.chat", task="Implemente o pedido", status="running"):
    return runs.create_run(session_id=sid, agent_id=agent, status=status, input={"message": task})


def project(runtime, sid="chat", **kwargs):
    return build_chat_activity(*runtime, sid, **kwargs)


def test_activity_endpoint_auth_empty_session_readonly_and_no_provider(runtime, monkeypatch):
    runs, bus = runtime
    new_run(runs)
    def unexpected(*args, **kwargs):
        pytest.fail("activity snapshot called provider")
    monkeypatch.setattr("bauer.agent.run_one_turn", unexpected)
    def verify_key(x_api_key: str | None = Header(None)):
        if x_api_key != "test-key":
            raise HTTPException(status_code=401)
    app = FastAPI()
    register_observability_routes(app, SimpleNamespace(run_manager=runs, event_bus=bus), verify_key)
    before = {name: bus.store.list(name) for name in ("runs", "events", "sessions")}
    with TestClient(app) as client:
        assert client.get("/chat/activity", params={"session_id": "chat"}).status_code == 401
        headers = {"X-API-Key": "test-key"}
        assert client.get("/chat/activity", headers=headers).json() == {"session_id": None, "runs": []}
        response = client.get("/chat/activity", headers=headers, params={"session_id": "chat"})
        assert response.status_code == 200
        assert response.json()["runs"][0]["agent_name"] == "Bauer"
    assert before == {name: bus.store.list(name) for name in before}


def test_simple_run_isolation_and_specific_run_check(runtime):
    runs, bus = runtime
    current = new_run(runs)
    other = new_run(runs, sid="other", task="OTHER SESSION TASK")
    bus.publish("task.delegated", run_id=current.id, session_id="other", agent_id="wrong-session",
                message="private other-session task")
    result = project(runtime)
    assert result["session_id"] == "chat"
    assert len(result["runs"]) == 1
    activity = result["runs"][0]
    assert activity["run_id"] == current.id
    assert activity["task"] == "Implemente o pedido"
    assert activity["status"] == "running"
    assert [member["agent_id"] for member in activity["agents"]] == ["serve.chat"]
    assert project(runtime, run_id=other.id)["runs"] == []
    assert project(runtime, run_id=current.id)["runs"][0]["run_id"] == current.id
    assert project(runtime, "   ") == {"session_id": None, "runs": []}


def test_real_team_events_preserve_tasks_names_and_member_state(runtime):
    runs, bus = runtime
    run = new_run(runs, agent="coordinator")
    bus.publish("task.delegated", run_id=run.id, agent_id="worker-a", status="queued", message="Escrever os testes")
    bus.publish("agent.started", run_id=run.id, session_id="chat", agent_id="worker-a", data={"agent_name": "Testes"})
    bus.publish("agent.paused", run_id=run.id, agent_id="worker-a")
    bus.publish("task.delegated", run_id=run.id, data={"assignee": "worker-b", "task": "Revisar mudanças", "agent_name": "Revisão"})
    agents = project(runtime)["runs"][0]["agents"]
    assert agents[1]["agent_id"] == "worker-a" and agents[1]["name"] == "Testes"
    assert agents[1]["task"] == "Escrever os testes" and agents[1]["status"] == "paused"
    assert agents[2]["name"] == "Revisão" and agents[2]["task"] == "Revisar mudanças"
    assert agents[2]["status"] == "delegated"
    bus.publish("agent.resumed", run_id=run.id, agent_id="worker-a")
    assert project(runtime)["runs"][0]["agents"][1]["status"] == "running"
    bus.publish("agent.finished", run_id=run.id, agent_id="worker-a", status="completed")
    assert project(runtime)["runs"][0]["agents"][1]["status"] == "completed"


def test_agno_task_without_description_and_unidentified_delegation_remain_unknown(runtime):
    runs, bus = runtime
    run = new_run(runs)
    bus.publish("task.delegated", run_id=run.id, data={"task_id": "ignored", "assignee": "worker"}, status="working")
    bus.publish("task.delegated", run_id=run.id, data={"task_id": "no-assignee"})
    bus.publish("agent.message.sent", run_id=run.id, agent_id="worker", message="not a task description")
    members = project(runtime)["runs"][0]["agents"]
    assert len(members) == 2
    assert members[1]["task"] == "Tarefa não informada"
    assert members[1]["status"] == "delegated"
    assert "not a task description" not in json.dumps(members)


def test_tools_do_not_conclude_agent_and_missing_agent_id_belongs_to_parent(runtime):
    runs, bus = runtime
    run = new_run(runs)
    bus.publish("tool.call.requested", run_id=run.id, session_id="chat", tool_name="run_command",
                data={"args": {"command": "DO_NOT_EXPOSE"}})
    member = project(runtime)["runs"][0]["agents"][0]
    assert member["tool_name"] == "run_command"
    assert member["activity"] == "Executando ferramenta"
    bus.publish("tool.call.completed", run_id=run.id, tool_name="run_command", status="completed",
                message="HIDDEN OUTPUT", data={"result_preview": "HIDDEN OUTPUT"})
    members = project(runtime)["runs"][0]["agents"]
    assert len(members) == 1 and members[0]["status"] == "running"
    assert members[0]["tool_name"] is None and members[0]["activity"] == "Ferramenta concluída"
    assert "HIDDEN OUTPUT" not in json.dumps(members)
    bus.publish("tool.call.failed", run_id=run.id, tool_name="run_command", status="failed")
    assert project(runtime)["runs"][0]["agents"][0]["status"] == "running"
    bus.publish("tool.denied", run_id=run.id, tool_name="read_file", status="denied")
    assert project(runtime)["runs"][0]["agents"][0]["activity"] == "Ferramenta bloqueada"


@pytest.mark.parametrize("parent_status", ["completed", "failed", "cancelled"])
def test_parent_terminal_closes_stale_members_without_inventing_completion(runtime, parent_status):
    runs, bus = runtime
    run = new_run(runs)
    for name in ("ongoing", "finished"):
        bus.publish("agent.started", run_id=run.id, agent_id=name)
    bus.publish("agent.completed", run_id=run.id, agent_id="finished")
    bus.publish("tool.call.requested", run_id=run.id, agent_id="ongoing", tool_name="process")
    runs.update_run(run.id, status=parent_status)
    activity = project(runtime)["runs"][0]
    assert activity["status"] == parent_status and activity["agents"][0]["status"] == parent_status
    assert activity["agents"][1]["status"] == "unknown"
    assert activity["agents"][1]["tool_name"] is None
    assert activity["agents"][2]["status"] == "completed"


@pytest.mark.parametrize("status", ["waiting_approval", "paused", "evaluating", "retrying", "policy_check"])
def test_run_manager_state_dominates_early_adapter_completion(runtime, status):
    runs, bus = runtime
    run = new_run(runs)
    bus.publish("agent.completed", run_id=run.id, agent_id=run.agent_id)
    runs.update_run(run.id, status=status)
    result = project(runtime)["runs"][0]
    assert result["status"] == status and result["agents"][0]["status"] == status


def test_activity_redacts_text_before_truncating_and_ignores_internal_fields(runtime):
    runs, bus = runtime
    token = "sk-proj-" + "a" * 40
    run = new_run(runs, task=f"Fazer integração token={token}")
    runs.update_run(run.id, input={"message": f"Fazer integração token={token}",
                                 "agent_spec": {"id": "serve.chat", "name": "Authorization: Bearer name-secret", "instructions": "PRIVATE SYSTEM"},
                                 "api_key": "PRIVATE CONFIG", "resolved": {"memory": "PRIVATE MEMORY"}},
                    output={"response": "PRIVATE TOOL OUTPUT"})
    bus.publish("task.delegated", run_id=run.id, agent_id="worker", message="token=member-secret",
                data={"agent_name": "password=name-secret", "args": {"secret": "PRIVATE ARGS"}})
    payload = json.dumps(project(runtime), ensure_ascii=False)
    for secret in (token, "name-secret", "member-secret", "PRIVATE SYSTEM", "PRIVATE CONFIG", "PRIVATE MEMORY", "PRIVATE TOOL OUTPUT", "PRIVATE ARGS"):
        assert secret not in payload
    assert "[REDACTED]" in payload
    runs.update_run(run.id, input={"message": "x" * (MAX_TASK_TEXT - 5) + " " + token})
    task = project(runtime)["runs"][0]["task"]
    assert len(task) <= MAX_TASK_TEXT and "sk-" not in task


def test_run_agent_event_and_text_limits_are_bounded(runtime, monkeypatch):
    runs, bus = runtime
    finished = new_run(runs, task="old completed")
    runs.update_run(finished.id, status="completed")
    for index in range(MAX_RUNS + 2):
        new_run(runs, task=f"active {index}")
    newest = new_run(runs, task="newest completed")
    runs.update_run(newest.id, status="completed")
    result = project(runtime)
    assert len(result["runs"]) == MAX_RUNS
    assert result["runs"][0]["run_id"] == newest.id
    assert finished.id not in [run["run_id"] for run in result["runs"]]
    for index in range(MAX_AGENTS + 3):
        bus.publish("task.delegated", run_id=newest.id, agent_id=f"worker-{index}", message="t" * 1000)
    original = bus.list_events
    limits = []
    def limited_events(**kwargs):
        limits.append(kwargs.get("limit"))
        return original(**kwargs)
    monkeypatch.setattr(bus, "list_events", limited_events)
    cards = project(runtime, run_id=newest.id)["runs"][0]["agents"]
    assert len(cards) == MAX_AGENTS
    assert all(len(card["task"]) <= MAX_TASK_TEXT for card in cards)
    assert limits == [MAX_EVENTS_PER_RUN]


def test_same_agent_multiple_delegations_have_separate_task_instances(runtime):
    runs, bus = runtime
    run = new_run(runs)
    for number in (1, 2):
        data = {"delegation_id": f"delegation-{number}", "agent_name": "Revisor", "task": f"Revisar tarefa {number}"}
        bus.publish("task.delegated", run_id=run.id, session_id="chat", agent_id="reviewer", data=data, status="delegated")
        bus.publish("agent.started", run_id=run.id, session_id="chat", agent_id="reviewer", data=data)
    bus.publish("agent.completed", run_id=run.id, session_id="chat", agent_id="reviewer", data={"delegation_id": "delegation-1"})
    members = project(runtime)["runs"][0]["agents"]
    assert [member["agent_id"] for member in members[1:]] == ["delegation-1", "delegation-2"]
    assert [member["name"] for member in members[1:]] == ["Revisor", "Revisor"]
    assert members[1]["status"] == "completed" and members[2]["status"] == "running"
    assert members[1]["task"] != members[2]["task"]
