"""Tests for the hybrid durable Kanban dispatcher."""

from __future__ import annotations

import time
from pathlib import Path

import bauer.task_dispatcher as task_dispatcher_module
from bauer.kanban_store import KanbanStore
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
    run = KanbanStore(workspace).latest_run_for_task(task.id)
    events = KanbanStore(workspace).list_events(task_id=task.id, limit=20)
    assert run is not None
    assert run.status == "succeeded"
    assert run.summary == "ok 001"
    assert "dispatcher.claimed" in {event.event_type for event in events}
    assert "dispatcher.completed" in {event.event_type for event in events}


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
    first_run = KanbanStore(workspace).latest_run_for_task(task.id)
    assert first_run is not None
    assert first_run.status == "retrying"

    second = dispatcher.dispatch_once(
        worker_fn=lambda _task: WorkerResult(False, error="boom again"),
        spawn_background=False,
    )
    failed = wm.get_task(task.id)
    assert second.failed == ["T0001"]
    assert failed.status == "FAILED"
    assert failed.metadata["attempts"] == "2"
    assert failed.metadata["last_error"] == "boom again"
    latest = KanbanStore(workspace).latest_run_for_task(task.id)
    assert latest is not None
    assert latest.status == "failed"
    assert latest.error == "boom again"


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
    run = KanbanStore(workspace).latest_run_for_task(task.id)
    assert run is not None
    assert run.status == "stale"


def test_detect_crashed_worker_returns_task_to_ready(tmp_path: Path, monkeypatch):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    task = wm.add_task("Crashed worker")
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id)

    with dispatcher._lock():
        claimed = dispatcher._claim_locked(wm.get_task(task.id))
        wm.update_task_metadata(claimed.id, metadata={"worker_pid": 424242})

    monkeypatch.setattr(task_dispatcher_module, "_pid_alive", lambda _pid: False)
    crashed = dispatcher.detect_crashed_workers()
    ready = wm.get_task(task.id)
    events = KanbanStore(workspace).list_events(task_id=task.id, limit=20)

    assert crashed == ["T0001"]
    assert ready.status == "READY"
    assert "claim_id" not in ready.metadata
    assert "worker.crashed" in {event.event_type for event in events}


def test_cancel_and_retry_task(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    task = wm.add_task("Cancelable")
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id)

    with dispatcher._lock():
        claimed = dispatcher._claim_locked(wm.get_task(task.id))

    cancelled = dispatcher.cancel_task(claimed.id, reason="operator stop")
    run = KanbanStore(workspace).get_run(claimed.metadata["run_id"])
    assert cancelled.status == "BLOCKED"
    assert run is not None
    assert run.status == "cancelled"

    retried = dispatcher.retry_failed(task.id, reason="operator retry")
    assert retried.status == "READY"
    assert retried.metadata["dispatch"] == "true"
    assert "run_id" not in retried.metadata


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


def test_dispatch_once_can_scope_claims_to_explicit_task_ids(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    first = wm.add_task("Unrelated")
    second = wm.add_task("Scoped")
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(first.id)
    dispatcher.mark_ready(second.id)

    result = dispatcher.dispatch_once(
        worker_fn=lambda claimed: WorkerResult(True, summary=f"ok {claimed.id}"),
        spawn_background=False,
        max_spawn=2,
        only_task_ids=[second.id],
    )

    assert result.claimed == ["T0002"]
    assert wm.get_task(first.id).status == "READY"
    assert wm.get_task(second.id).status == "DONE"


def test_claim_records_agent_lane_metadata(tmp_path: Path):
    workspace = _workspace(tmp_path)
    (workspace / "agents.yaml").write_text(
        """
agents:
  - name: coder
    description: Code agent
    system: Writes code
    capabilities: [python]
    lane: dev
    max_concurrent: 2
    priority_weight: 3
""".strip(),
        encoding="utf-8",
    )
    wm = WorkspaceManager(workspace)
    task = wm.add_task("Needs Python", metadata={"capability": "python"})
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id)

    result = dispatcher.dispatch_once(
        worker_fn=lambda claimed: WorkerResult(True, summary=f"ok {claimed.id}"),
        spawn_background=False,
    )

    run = KanbanStore(workspace).latest_run_for_task(task.id)
    finished = wm.get_task(task.id)
    assert result.claimed == ["T0001"]
    assert run is not None
    assert run.metadata["lane"] == "dev"
    assert run.metadata["agent"] == "coder"
    assert run.metadata["capability"] == "python"
    assert run.metadata["priority_weight"] == 3
    assert finished.metadata["lane"] == "dev"
    assert finished.metadata["agent"] == "coder"


def test_agent_lane_capacity_skips_excess_ready_tasks(tmp_path: Path):
    workspace = _workspace(tmp_path)
    (workspace / "agents.yaml").write_text(
        """
agents:
  - name: coder
    description: Code agent
    system: Writes code
    capabilities: [python]
    lane: dev
    max_concurrent: 1
""".strip(),
        encoding="utf-8",
    )
    wm = WorkspaceManager(workspace)
    first = wm.add_task("First", assignee="coder")
    second = wm.add_task("Second", assignee="coder")
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(first.id)
    dispatcher.mark_ready(second.id)

    result = dispatcher.dispatch_once(
        worker_fn=lambda claimed: WorkerResult(True, summary=f"ok {claimed.id}"),
        max_spawn=2,
        spawn_background=False,
    )

    assert result.claimed == ["T0001"]
    assert result.completed == ["T0001"]
    assert any("lane dev capacity 1/1" in item for item in result.skipped)
    assert wm.get_task(second.id).status == "READY"


def test_watchdog_tick_records_daemon_event(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    task = wm.add_task("Daemon work")
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id)

    dispatcher.record_daemon_started(interval=1, max_spawn=1, max_in_progress=1)
    result = dispatcher.watchdog_tick(dry_run=True, max_spawn=1, max_in_progress=1)
    dispatcher.record_daemon_stopped(reason="test stop")

    events = KanbanStore(workspace).list_events(task_id="000", limit=10)
    event_types = {event.event_type for event in events}
    assert result.dry_run == ["T0001"]
    assert "dispatcher.daemon_started" in event_types
    assert "dispatcher.daemon_tick" in event_types
    assert "dispatcher.daemon_stopped" in event_types


def test_cancel_task_can_request_worker_termination(tmp_path: Path, monkeypatch):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    task = wm.add_task("Terminate me")
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id)
    with dispatcher._lock():
        claimed = dispatcher._claim_locked(wm.get_task(task.id))
        wm.update_task_metadata(claimed.id, metadata={"worker_pid": 12345})

    monkeypatch.setattr(
        task_dispatcher_module,
        "_terminate_pid",
        lambda pid: {"termination_requested": True, "termination_status": "terminated", "worker_pid": pid},
    )

    cancelled = dispatcher.cancel_task(claimed.id, reason="stop", terminate_worker=True)
    events = KanbanStore(workspace).list_events(task_id=task.id, limit=20)

    assert cancelled.status == "BLOCKED"
    assert "worker.cancel_requested" in {event.event_type for event in events}
    cancel_event = next(event for event in events if event.event_type == "worker.cancel_requested")
    assert cancel_event.metadata["termination_status"] == "terminated"


def test_orchestration_task_worker_uses_node_worker_subprocess(tmp_path: Path, monkeypatch):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    task = wm.add_task(
        "Orchestration node",
        metadata={
            "orchestration_run": "orch-abc",
            "orchestration_step": "2",
            "orchestration_backend": "dispatcher",
        },
    )
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id)
    with dispatcher._lock():
        claimed = dispatcher._claim_locked(wm.get_task(task.id))

    captured = {}

    class _Proc:
        returncode = 0
        stdout = "node ok"
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _Proc()

    monkeypatch.setattr(task_dispatcher_module.subprocess, "run", _fake_run)

    result = dispatcher._run_orchestrator_subprocess(
        claimed,
        config=workspace / "config.yaml",
        models=workspace / "models.yaml",
    )

    cmd = captured["cmd"]
    assert result.success is True
    assert "node-worker" in cmd
    assert "orch-abc" in cmd
    assert "2" in cmd
    assert "--task-id" in cmd
    assert claimed.id in cmd
    assert "--claim-id" in cmd
    assert claimed.metadata["claim_id"] in cmd


def test_ops_status_reports_lanes_and_active_claims(tmp_path: Path):
    from bauer.ops_status import build_ops_status

    workspace = _workspace(tmp_path)
    (workspace / "agents.yaml").write_text(
        """
agents:
  - name: coder
    description: Code agent
    system: Writes code
    capabilities: [python]
    lane: dev
    max_concurrent: 1
""".strip(),
        encoding="utf-8",
    )
    wm = WorkspaceManager(workspace)
    task = wm.add_task("Ops visible", metadata={"capability": "python"})
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id)
    with dispatcher._lock():
        dispatcher._claim_locked(wm.get_task(task.id))

    status = build_ops_status(workspace, limit=5)

    assert status["status_counts"]["IN_PROGRESS"] == 1
    assert status["lanes"][0]["lane"] == "dev"
    assert status["lanes"][0]["running"] == 1
    assert status["active_claims"][0]["public_id"] == "T0001"
