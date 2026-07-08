from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bauer.cli import app
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.session_manager import SessionManager


def test_run_manager_lifecycle(tmp_path: Path):
    manager = RunManager(root=tmp_path)
    run = manager.create_run(
        session_id="session-1",
        agent_id="agent-1",
        runtime_adapter="bauer_native",
        input={"message": "hello"},
    )

    assert run.status == "queued"
    running = manager.start_run(run.id)
    assert running.status == "running"

    completed = manager.complete_run(run.id, output={"response": "ok"}, tool_calls_count=2)
    assert completed.status == "completed"
    assert completed.finished_at is not None
    assert completed.output == {"response": "ok"}
    assert completed.tool_calls_count == 2

    assert manager.get_run(run.id).status == "completed"  # type: ignore[union-attr]
    assert len(manager.list_runs()) == 1


def test_run_manager_cancel_non_terminal_run(tmp_path: Path):
    manager = RunManager(root=tmp_path)
    run = manager.create_run(
        session_id="session-1",
        agent_id="agent-1",
        runtime_adapter="bauer_native",
        input={},
        status="running",
    )

    cancelled = manager.cancel_run(run.id)

    assert cancelled.status == "cancelled"
    assert cancelled.finished_at is not None


def test_session_manager_get_or_create_and_touch(tmp_path: Path):
    manager = SessionManager(root=tmp_path)

    session = manager.get_or_create_session("session-1", user_id="user-1", agent_id="agent-1")
    touched = manager.touch_session(session.id, state={"last_run_id": "run-1"})

    assert touched.id == "session-1"
    assert touched.user_id == "user-1"
    assert touched.state["last_run_id"] == "run-1"
    assert manager.get_session("session-1").state["last_run_id"] == "run-1"  # type: ignore[union-attr]


def test_runtime_cli_lists_shows_and_cancels(tmp_path: Path):
    runner = CliRunner()
    sessions = SessionManager(root=tmp_path)
    runs = RunManager(root=tmp_path)
    sessions.create_session(session_id="session-1", user_id="user-1", agent_id="agent-1")
    run = runs.create_run(
        session_id="session-1",
        agent_id="agent-1",
        runtime_adapter="bauer_native",
        input={"message": "hello"},
        status="running",
    )

    result = runner.invoke(app, ["runs", "list", "--state-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "Runs" in result.output

    result = runner.invoke(app, ["runs", "show", run.id, "--state-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert '"status": "running"' in result.output

    result = runner.invoke(app, ["runs", "cancel", run.id, "--state-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "cancelled" in result.output

    result = runner.invoke(app, ["sessions", "list", "--state-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "user-1" in result.output

    result = runner.invoke(app, ["sessions", "show", "session-1", "--state-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert '"user_id": "user-1"' in result.output
