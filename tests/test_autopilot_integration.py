"""Controlled end-to-end autopilot flow with a temporary Git workspace."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bauer.autopilot import AutopilotController, AutopilotState
from bauer.goal_tracker import GoalStatus, GoalTracker
from bauer.workspace_manager import WorkspaceManager


def _cfg(**overrides):
    values = {
        "enabled": True,
        "workspace": "",
        "poll_interval_s": 1.0,
        "max_active_goals": 1,
        "max_replans_per_goal": 0,
        "mission": "",
        "allow_model_proposals": False,
        "approval_mode": "threshold",
        "max_minutes": 30,
        "max_tool_calls": 10,
        "max_cost_usd": 2.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required for the isolated workspace scenario")
def test_goal_tasks_reconcile_completion_after_restart(tmp_path: Path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    subprocess.run(["git", "init", str(workspace)], check=True, capture_output=True)
    manager = WorkspaceManager(workspace)
    manager.init_project("Controlled autopilot")
    tracker = GoalTracker(workspace / ".bauer_runtime" / "goals.db")
    goal_id = tracker.create("deterministic integration goal")

    controller = AutopilotController(
        workspace,
        config=_cfg(),
        tracker=tracker,
        task_manager=manager,
        session_id="integration-controller",
        plan_fn=lambda _goal: [{"title": "fake governed task", "description": "no provider"}],
    )
    first = controller.tick()
    assert first.state == AutopilotState.DISPATCHING
    task = manager.list_tasks()[0]
    assert task.status == "READY"
    assert task.metadata["goal_id"] == goal_id

    # Simulate the supervisor dying after task creation and before the next
    # reconciliation cycle. The durable task remains discoverable.
    lease = tracker.get(goal_id).lease_expires_at
    tracker.release_or_requeue_stale(now=lease + 1)
    restarted = AutopilotController(
        workspace,
        config=_cfg(),
        tracker=GoalTracker(workspace / ".bauer_runtime" / "goals.db"),
        task_manager=manager,
        session_id="restarted-controller",
        plan_fn=lambda _goal: [{"title": "fake governed task", "description": "no provider"}],
    )
    resumed = restarted.tick()
    assert resumed.state == AutopilotState.DISPATCHING
    assert len(manager.list_tasks()) == 1

    manager.update_task_metadata(task.id, metadata={"gate_receipt": json.dumps({
        "schema": "bauer.dispatch-gate-receipt.v1",
        "status": "passed",
        "verifier": "task_dispatcher",
        "verification_id": "dispatch-1:verify-1",
        "kernel_run_id": "kernel-1",
        "kanban_task_id": task.id,
        "kanban_claim_id": "claim-1",
        "dispatcher_run_id": "dispatch-1",
        "gate_names": ["non_empty_output", "no_traceback"],
        "cost_known": True,
        "cost_usd": 0.0,
        "tool_calls": 1,
        "elapsed_seconds": 1.0,
    })})
    manager.update_task_status(task.id, "DONE")
    completed = restarted.tick()
    assert completed.state == AutopilotState.IDLE
    assert restarted.tracker.get(goal_id).status == GoalStatus.DONE


def test_controlled_failure_and_kill_switch_do_not_execute_provider(tmp_path: Path):
    manager = WorkspaceManager(tmp_path / "project")
    manager.init_project("Controlled failure")
    tracker = GoalTracker(tmp_path / "goals.db")
    tracker.create("must block")
    controller = AutopilotController(
        tmp_path / "project",
        config=_cfg(),
        tracker=tracker,
        task_manager=manager,
        kill_switch=lambda: False,
        plan_fn=lambda _goal: [{"title": "fake task"}],
    )
    controller.tick()
    manager.update_task_status("001", "FAILED")
    blocked = controller.tick()
    assert blocked.state == AutopilotState.BLOCKED
    assert tracker.list_by_status(GoalStatus.BLOCKED)

    blocked_controller = AutopilotController(
        tmp_path / "blocked",
        config=_cfg(mission="must not start"),
        task_manager=WorkspaceManager(tmp_path / "blocked"),
        kill_switch=lambda: True,
    )
    assert blocked_controller.tick().reason == "kill_switch"
