"""Tests for the hybrid durable Kanban dispatcher."""

from __future__ import annotations

import time
from pathlib import Path

from bauer.task_dispatcher import TaskDispatcher, WorkerResult
from bauer.workspace_manager import WorkspaceManager


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    WorkspaceManager(workspace).init_project("Dispatcher Test")
    return workspace


def test_mark_ready_sets_dispatch_metadata(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    task = wm.add_task("Run me")

    ready = TaskDispatcher(workspace).mark_ready(task.id, assignee="coder", max_retries=3)

    assert ready.status == "READY"
    assert ready.assignee == "coder"
    assert ready.metadata["dispatch"] == "true"
    assert ready.metadata["max_retries"] == "3"
    assert "claim_id" not in ready.metadata


def test_dispatch_success_completes_task(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    task = wm.add_task("Successful task")
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id)

    result = dispatcher.dispatch_once(
        worker_fn=lambda claimed: WorkerResult(True, summary=f"ok {claimed.id}"),
        spawn_background=False,
    )

    finished = wm.get_task(task.id)
    assert result.claimed == ["T0001"]
    assert result.completed == ["T0001"]
    assert finished.status == "DONE"
    assert finished.metadata["attempts"] == "1"
    assert "claim_id" not in finished.metadata
    assert any("Resultado: ok 001" in comment["text"] for comment in finished.comments)


def test_dispatch_failure_retries_until_failed(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    task = wm.add_task("Flaky task")
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id, max_retries=2)

    first = dispatcher.dispatch_once(
        worker_fn=lambda _task: WorkerResult(False, error="boom"),
        spawn_background=False,
    )
    retry = wm.get_task(task.id)
    assert first.failed == ["T0001"]
    assert retry.status == "READY"
    assert retry.metadata["attempts"] == "1"
    assert retry.metadata["last_error"] == "boom"

    second = dispatcher.dispatch_once(
        worker_fn=lambda _task: WorkerResult(False, error="boom again"),
        spawn_background=False,
    )
    failed = wm.get_task(task.id)
    assert second.failed == ["T0001"]
    assert failed.status == "FAILED"
    assert failed.metadata["attempts"] == "2"
    assert failed.metadata["last_error"] == "boom again"


def test_reclaim_stale_claim_returns_task_to_ready(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    task = wm.add_task("Stale task")
    dispatcher = TaskDispatcher(workspace, claim_ttl_seconds=30, stale_seconds=30)
    dispatcher.mark_ready(task.id)

    with dispatcher._lock():
        claimed = dispatcher._claim_locked(wm.get_task(task.id))
        wm.update_task_metadata(
            claimed.id,
            metadata={
                "claim_expires": int(time.time()) - 1,
                "heartbeat_at": "2000-01-01T00:00:00+00:00",
            },
        )

    reclaimed = dispatcher.reclaim_stale()
    ready = wm.get_task(task.id)

    assert reclaimed == ["T0001"]
    assert ready.status == "READY"
    assert "claim_id" not in ready.metadata
    assert any("Reclaimed:" in comment["text"] for comment in ready.comments)


def test_max_in_progress_blocks_new_claim(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    first = wm.add_task("Already running")
    second = wm.add_task("Waiting")
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(first.id)
    dispatcher.mark_ready(second.id)

    with dispatcher._lock():
        dispatcher._claim_locked(wm.get_task(first.id))

    result = dispatcher.dispatch_once(
        worker_fn=lambda _task: WorkerResult(True, summary="should not run"),
        max_in_progress=1,
        spawn_background=False,
    )

    assert result.claimed == []
    assert result.completed == []
    assert wm.get_task(first.id).status == "IN_PROGRESS"
    assert wm.get_task(second.id).status == "READY"


def test_dry_run_respects_max_spawn_without_claiming(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    first = wm.add_task("First")
    second = wm.add_task("Second")
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(first.id)
    dispatcher.mark_ready(second.id)

    result = dispatcher.dispatch_once(dry_run=True, max_spawn=1)

    assert result.dry_run == ["T0001"]
    assert wm.get_task(first.id).status == "READY"
    assert wm.get_task(second.id).status == "READY"
