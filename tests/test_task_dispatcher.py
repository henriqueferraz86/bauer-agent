"""Tests for the hybrid durable Kanban dispatcher."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import bauer.task_dispatcher as task_dispatcher_module
from bauer import kanban_db
from bauer.kanban_store import KanbanStore
from bauer.task_dispatcher import TaskDispatcher, TaskDispatcherError, WorkerResult
from bauer.workspace_manager_factory import get_workspace_manager
from bauer.workspace_manager_sqlite import WorkspaceManagerSqlite


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    get_workspace_manager(workspace).init_project("Dispatcher Test")
    return workspace


def test_mark_ready_sets_dispatch_metadata(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = get_workspace_manager(workspace)
    task = wm.add_task("Run me")

    ready = TaskDispatcher(workspace).mark_ready(task.id, assignee="coder", max_retries=3)

    assert ready.status == "READY"
    assert ready.assignee == "coder"
    assert ready.metadata["dispatch"] == "true"
    assert ready.metadata["max_retries"] == "3"
    assert "claim_id" not in ready.metadata


def test_dispatch_success_completes_task(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = get_workspace_manager(workspace)
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


def test_autopilot_task_requires_correlated_gate_receipt_to_complete(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = get_workspace_manager(workspace)
    task = wm.add_task("Governed task", metadata={"goal_id": "goal-1"})
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id)
    with dispatcher._lock():
        claimed = dispatcher._claim_locked(wm.get_task(task.id))
    with pytest.raises(TaskDispatcherError, match="comprovante válido"):
        dispatcher._complete_task(task.id, "missing receipt")
    receipt = {
        "schema": "bauer.dispatch-gate-receipt.v1",
        "status": "passed",
        "verifier": "task_dispatcher",
        "verification_id": "dispatch-run:verify-1",
        "kernel_run_id": "kernel-run-1",
        "kanban_task_id": task.id,
        "kanban_claim_id": claimed.metadata["claim_id"],
        "dispatcher_run_id": claimed.metadata["run_id"],
        "gate_names": ["NonEmptyOutput"],
        "cost_usd": 0.03,
        "cost_known": True,
        "tool_calls": 2,
        "elapsed_seconds": 10.0,
    }
    completed = dispatcher._complete_task(task.id, "ok", gate_receipt=receipt)

    assert completed.status == "DONE"
    assert json.loads(completed.metadata["gate_receipt"]) == receipt


def test_unknown_cost_blocks_autopilot_task_without_automatic_retry(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = get_workspace_manager(workspace)
    task = wm.add_task("Unknown-cost task", metadata={"goal_id": "goal-1"})
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id)
    with dispatcher._lock():
        dispatcher._claim_locked(wm.get_task(task.id))

    blocked = dispatcher._fail_task(
        task.id, "unknown cost", force_blocked=True
    )

    assert blocked.status == "BLOCKED"
    assert "unknown cost" in blocked.metadata["last_error"]


def test_kernel_attestation_must_be_persisted_and_correlated(tmp_path: Path):
    from bauer.core.runtime.state_store import SqliteStateStore

    from bauer.task_dispatcher import _find_kernel_gate_attestation

    root = tmp_path / "kernel-runtime"
    store = SqliteStateStore(root)
    correlation = {
        "task_id": "001",
        "claim_id": "claim-1",
        "dispatcher_run_id": "dispatch-1",
        "gate_names": ["non_empty_output", "no_traceback"],
    }
    store.upsert("runs", {
        "id": "kernel-1",
        "session_id": "session-1",
        "agent_id": "cli.run",
        "runtime_adapter": "bauer_native",
        "status": "completed",
        "input": {"autopilot_dispatch": correlation},
        "cost_estimate": 0.12,
        "tool_calls_count": 4,
        "usage_known": True,
        "llm_calls_count": 2,
        "elapsed_seconds": 35.5,
        "validation_passed": True,
        "validation_results": [
            {"gate": "non_empty_output", "passed": True, "reason": ""},
            {"gate": "no_traceback", "passed": True, "reason": ""},
        ],
    })

    proof = _find_kernel_gate_attestation(
        root, task_id="001", claim_id="claim-1", dispatcher_run_id="dispatch-1"
    )
    assert proof == {
        "kernel_run_id": "kernel-1",
        "gate_names": ["non_empty_output", "no_traceback"],
        "cost_usd": 0.12,
        "cost_known": True,
        "tool_calls": 4,
        "llm_calls": 2,
        "elapsed_seconds": 35.5,
    }

    assert _find_kernel_gate_attestation(
        root, task_id="001", claim_id="wrong", dispatcher_run_id="dispatch-1"
    ) is None
    run = store.latest("runs", "kernel-1")
    run["validation_results"][1]["passed"] = False
    store.upsert("runs", run)
    assert _find_kernel_gate_attestation(
        root, task_id="001", claim_id="claim-1", dispatcher_run_id="dispatch-1"
    ) is None


def test_goal_budget_accumulates_persisted_receipts_after_dispatcher_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    workspace = tmp_path / "mission-budget"
    wm = WorkspaceManagerSqlite(workspace, board="mission-budget")
    wm.init_project("mission budget")
    prior = wm.add_task(
        "First step",
        status="DONE",
        metadata={
            "goal_id": "goal-1",
            "budget_max_cost_usd": 1,
            "budget_max_tool_calls": 10,
            "budget_max_minutes": 5,
            "gate_receipt": json.dumps({
                "schema": "bauer.dispatch-gate-receipt.v1",
                "status": "passed",
                "verifier": "task_dispatcher",
                "verification_id": "verify-1",
                "kernel_run_id": "kernel-1",
                "kanban_task_id": "001",
                "kanban_claim_id": "claim-1",
                "dispatcher_run_id": "dispatch-1",
                "gate_names": ["tests"],
                "cost_known": True,
                "cost_usd": 0.4,
                "tool_calls": 3,
                "elapsed_seconds": 60.0,
            }),
        },
    )
    next_task = wm.add_task(
        "Second step",
        status="READY",
        metadata={
            "goal_id": "goal-1",
            "budget_max_cost_usd": 1,
            "budget_max_tool_calls": 10,
            "budget_max_minutes": 5,
        },
    )

    # A fresh dispatcher reconstructs mission usage from durable task receipts.
    monkeypatch.setattr(task_dispatcher_module, "get_workspace_manager", lambda _workspace: wm)
    dispatcher = TaskDispatcher(workspace)
    result = dispatcher.dispatch_once(
        dry_run=True,
        max_spawn=1,
        only_task_ids=[next_task.id],
    )

    assert wm.get_task(prior.id).status == "DONE"
    assert result.dry_run == ["T0002"]
    state = dispatcher._autopilot_budget_state(wm.get_task(next_task.id))
    assert state["max_cost_usd"] == pytest.approx(0.6)
    assert state["max_tool_calls"] == 7
    assert state["max_runtime_seconds"] == 240


def test_goal_budget_blocks_when_prior_cost_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "unknown-mission-budget"
    wm = WorkspaceManagerSqlite(workspace, board="unknown-mission-budget")
    wm.init_project("unknown mission budget")
    wm.add_task(
        "First step",
        status="DONE",
        metadata={
            "goal_id": "goal-unknown",
            "budget_max_cost_usd": 1,
            "budget_max_tool_calls": 10,
            "budget_max_minutes": 5,
            "gate_receipt": json.dumps({
                "schema": "bauer.dispatch-gate-receipt.v1",
                "status": "passed",
                "verifier": "task_dispatcher",
                "verification_id": "verify-unknown",
                "kernel_run_id": "kernel-unknown",
                "kanban_task_id": "001",
                "kanban_claim_id": "claim-1",
                "dispatcher_run_id": "dispatch-1",
                "gate_names": ["tests"],
                "cost_known": False,
                "tool_calls": 2,
                "elapsed_seconds": 20.0,
            }),
        },
    )
    next_task = wm.add_task(
        "Second step",
        status="READY",
        metadata={
            "goal_id": "goal-unknown",
            "budget_max_cost_usd": 1,
            "budget_max_tool_calls": 10,
            "budget_max_minutes": 5,
        },
    )
    monkeypatch.setattr(task_dispatcher_module, "get_workspace_manager", lambda _workspace: wm)
    dispatcher = TaskDispatcher(workspace)

    result = dispatcher.dispatch_once(only_task_ids=[next_task.id], spawn_background=False)

    assert result.claimed == []
    assert wm.get_task(next_task.id).status == "BLOCKED"
    assert "cost is unknown" in wm.get_task(next_task.id).metadata["last_error"]


def test_dispatch_failure_retries_until_failed(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = get_workspace_manager(workspace)
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
    wm = get_workspace_manager(workspace)
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
    wm = get_workspace_manager(workspace)
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
    wm = get_workspace_manager(workspace)
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
    wm = get_workspace_manager(workspace)
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
    wm = get_workspace_manager(workspace)
    first = wm.add_task("First")
    second = wm.add_task("Second")
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(first.id)
    dispatcher.mark_ready(second.id)

    result = dispatcher.dispatch_once(dry_run=True, max_spawn=1)

    assert result.dry_run == ["T0001"]
    assert wm.get_task(first.id).status == "READY"
    assert wm.get_task(second.id).status == "READY"


@pytest.mark.parametrize("pending_status", ["TODO", "READY", "IN_PROGRESS", "BLOCKED", "FAILED"])
def test_dispatcher_requires_every_sqlite_predecessor_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pending_status: str
):
    workspace = tmp_path / "sqlite-workspace"
    wm = WorkspaceManagerSqlite(workspace, board=f"dispatcher-{pending_status.lower()}")
    wm.init_project("SQLite dispatcher dependencies")
    monkeypatch.setattr(task_dispatcher_module, "get_workspace_manager", lambda _workspace: wm)

    completed_parent = wm.add_task("Completed predecessor", status="DONE")
    pending_parent = wm.add_task("Pending predecessor", status=pending_status)
    task = wm.add_task("Dependent task", status="TODO")
    conn = wm._connect()
    try:
        kanban_db.link_tasks(conn, completed_parent.id, task.id)
        kanban_db.link_tasks(conn, pending_parent.id, task.id)
    finally:
        conn.close()

    assert set(wm.get_task_parent_ids(task.id)) == {completed_parent.id, pending_parent.id}
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id)

    preview = dispatcher.dispatch_once(dry_run=True, only_task_ids=[task.id])
    assert preview.dry_run == []
    assert preview.skipped == ["T0003: predecessors T0002 not done"]
    assert wm.get_task(task.id).status == "READY"

    actual = dispatcher.dispatch_once(
        worker_fn=lambda _claimed: WorkerResult(True, summary="all predecessors complete"),
        spawn_background=False,
        only_task_ids=[task.id],
    )
    assert actual.claimed == []
    assert actual.completed == []
    assert wm.get_task(task.id).status == "READY"
    with dispatcher._lock(), pytest.raises(TaskDispatcherError, match="predecessores nao concluidos"):
        dispatcher._claim_locked(wm.get_task(task.id))

    wm.update_task_status(pending_parent.id, "DONE")
    completed = dispatcher.dispatch_once(
        worker_fn=lambda _claimed: WorkerResult(True, summary="unblocked"),
        spawn_background=False,
        only_task_ids=[task.id],
    )
    assert completed.claimed == ["T0003"]
    assert completed.completed == ["T0003"]
    assert wm.get_task(task.id).status == "DONE"


def test_markdown_dispatcher_keeps_single_parent_dependency_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    workspace = _workspace(tmp_path)
    wm = get_workspace_manager(workspace, backend="markdown")
    monkeypatch.setattr(task_dispatcher_module, "get_workspace_manager", lambda _workspace: wm)
    parent = wm.add_task("Markdown predecessor", status="TODO")
    task = wm.add_task("Markdown dependent", status="TODO", parent_id=parent.id)
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(task.id)

    blocked = dispatcher.dispatch_once(dry_run=True)
    assert blocked.dry_run == []
    assert wm.get_task(task.id).status == "READY"

    wm.update_task_status(parent.id, "DONE")
    ready = dispatcher.dispatch_once(dry_run=True)
    assert ready.dry_run == ["T0002"]


def test_dispatch_once_can_scope_claims_to_explicit_task_ids(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = get_workspace_manager(workspace)
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
    wm = get_workspace_manager(workspace)
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
    wm = get_workspace_manager(workspace)
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
    wm = get_workspace_manager(workspace)
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
    wm = get_workspace_manager(workspace)
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
    wm = get_workspace_manager(workspace)
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
    wm = get_workspace_manager(workspace)
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
