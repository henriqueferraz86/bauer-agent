from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bauer.cli import app
from bauer.core.events import EventBus
from bauer.core.runtime.run_manager import RunManager
from bauer.tool_router import ToolError, ToolRouter


def test_runtime_event_bus_publishes_subscribes_and_persists(tmp_path: Path):
    bus = EventBus(root=tmp_path)
    seen = []
    bus.subscribe("run.created", seen.append)

    event = bus.publish("run.created", run_id="run-1", session_id="session-1")

    assert seen == [event]
    reloaded = EventBus(root=tmp_path).list_events()
    assert len(reloaded) == 1
    assert reloaded[0].event_type == "run.created"
    assert reloaded[0].run_id == "run-1"


def test_run_manager_emits_lifecycle_events(tmp_path: Path):
    manager = RunManager(root=tmp_path)

    run = manager.create_run(
        session_id="session-1",
        agent_id="agent-1",
        runtime_adapter="bauer_native",
        input={"message": "hi"},
        status="running",
    )
    manager.complete_run(run.id, output={"response": "ok"})

    event_types = [event.event_type for event in EventBus(root=tmp_path).list_events(run_id=run.id)]
    assert event_types == ["run.created", "run.started", "run.completed"]


def test_tool_router_emits_tool_events(tmp_path: Path):
    router = ToolRouter(workspace=tmp_path / "workspace", session_id="session-1", run_id="run-1")

    result = router.execute({"action": "calculate", "args": {"expression": "1+1"}})

    assert "2" in result
    events = EventBus(root=tmp_path / "runtime").list_events(run_id="run-1")
    assert [event.event_type for event in events] == [
        "tool.call.requested",
        "tool.call.completed",
    ]
    assert events[0].tool_name == "calculate"


def test_tool_router_parse_failure_does_not_emit_tool_event(tmp_path: Path):
    router = ToolRouter(workspace=tmp_path / "workspace", session_id="session-1", run_id="run-1")

    try:
        router.execute({"action": "missing_tool", "args": {}})
    except ToolError:
        pass

    events = EventBus(root=tmp_path / "runtime").list_events(run_id="run-1")
    assert events == []


def test_events_cli_tail_and_runs_events(tmp_path: Path):
    runner = CliRunner()
    manager = RunManager(root=tmp_path)
    run = manager.create_run(
        session_id="session-1",
        agent_id="agent-1",
        runtime_adapter="bauer_native",
        input={},
        status="running",
    )

    result = runner.invoke(app, ["events", "tail", "--state-dir", str(tmp_path), "--limit", "10"])
    assert result.exit_code == 0
    assert "run.created" in result.output

    result = runner.invoke(app, ["runs", "events", run.id, "--state-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "run.started" in result.output
