"""Tests for the durable Kanban sidecar store."""

from __future__ import annotations

from pathlib import Path

from bauer.kanban_store import KanbanStore
from bauer.workspace_manager import WorkspaceManager


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    WorkspaceManager(workspace).init_project("Kanban Store Test")
    return workspace


def test_workspace_manager_records_task_events(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    store = KanbanStore(workspace)

    task = wm.add_task("Track me", priority="high")
    wm.update_task_status(task.id, "READY")
    wm.update_task_metadata(task.id, metadata={"dispatch": "true"})
    wm.add_task_comment(task.id, "Ready for dispatch", author="tester")

    events = store.list_events(task_id=task.id, limit=10)
    event_types = [event.event_type for event in events]

    assert "task.created" in event_types
    assert "task.status_changed" in event_types
    assert "task.metadata_updated" in event_types
    assert "task.commented" in event_types
    assert events[0].task_id == "001"


def test_run_lifecycle_round_trip(tmp_path: Path):
    workspace = _workspace(tmp_path)
    store = KanbanStore(workspace)

    run = store.start_run(
        run_id="run-1",
        task_id="T0001",
        claim_id="claim-1",
        runner="tester",
        attempt=2,
        log_path=".bauer_dispatch/runs/001-run-1.log",
    )
    updated = store.update_run("run-1", status="succeeded", summary="done", worker_pid=123)

    assert run.task_id == "001"
    assert updated is not None
    assert updated.status == "succeeded"
    assert updated.worker_pid == 123
    assert updated.finished_at
    assert store.latest_run_for_task("001").run_id == "run-1"  # type: ignore[union-attr]
