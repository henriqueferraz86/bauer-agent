"""Hermetic controller tests: no provider, subprocess or network access."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from bauer.autonomous_budget import AutonomousBudget
from bauer.autopilot import AutopilotController, AutopilotState
from bauer.goal_tracker import GoalStatus, GoalTracker
from bauer.workspace_manager_factory import get_workspace_manager


def _config(**changes):
    values = {
        "enabled": True,
        "workspace": "",
        "poll_interval_s": 1.0,
        "max_active_goals": 1,
        "max_replans_per_goal": 1,
        "mission": "",
        "allow_model_proposals": False,
        "approval_mode": "threshold",
        "max_minutes": 30,
        "max_tool_calls": 500,
        "max_cost_usd": 2.0,
    }
    values.update(changes)
    return SimpleNamespace(**values)


class FakeTask:
    def __init__(self, task_id: str, title: str, status: str, metadata: dict[str, str]):
        self.id = task_id
        self.title = title
        self.description = ""
        self.status = status
        self.metadata = dict(metadata)


class FakeTaskManager:
    def __init__(self):
        self.tasks: list[FakeTask] = []

    def add_task(self, title, description="", status="READY", metadata=None, **_kwargs):
        task = FakeTask(str(len(self.tasks) + 1).zfill(3), title, status, metadata or {})
        task.description = description
        self.tasks.append(task)
        return task

    def list_tasks(self):
        return list(self.tasks)

    def get_task(self, task_id):
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)


def _controller(tmp_path: Path, *, config=None, plan_fn=None, kill_switch=None, budget=None):
    tracker = GoalTracker(tmp_path / "goals.db")
    manager = FakeTaskManager()
    controller = AutopilotController(
        tmp_path,
        config=config or _config(),
        tracker=tracker,
        task_manager=manager,
        plan_fn=plan_fn,
        kill_switch=kill_switch,
        budget=budget,
    )
    return controller, tracker, manager


def test_tick_materializes_declared_mission_and_is_idempotent(tmp_path):
    controller, tracker, manager = _controller(tmp_path, config=_config(mission="ship MVP"))

    first = controller.tick()
    assert first.state == AutopilotState.DISPATCHING
    assert len(manager.tasks) == 1
    goal = tracker.list_by_status(GoalStatus.RUNNING)[0]
    assert manager.tasks[0].metadata["goal_id"] == goal.id
    assert manager.tasks[0].metadata["dispatch"] == "true"

    second = controller.tick()
    assert second.state == AutopilotState.DISPATCHING
    assert second.reason == "tasks_in_flight"
    assert len(manager.tasks) == 1

    manager.tasks[0].status = "DONE"
    manager.tasks[0].metadata["gate_receipt"] = {
        "schema": "bauer.dispatch-gate-receipt.v1",
        "status": "passed",
        "verifier": "task_dispatcher",
        "verification_id": "dispatch-run-1:verify-1",
        "kernel_run_id": "kernel-run-1",
        "kanban_task_id": manager.tasks[0].id,
        "kanban_claim_id": "claim-1",
        "dispatcher_run_id": "dispatch-run-1",
        "gate_names": ["NonEmptyOutput"],
        "cost_usd": 0.03,
        "cost_known": True,
        "tool_calls": 2,
        "elapsed_seconds": 10.0,
    }
    done = controller.tick()
    assert done.state == AutopilotState.IDLE
    assert tracker.get(goal.id).status == GoalStatus.DONE


def test_done_autopilot_task_without_gate_receipt_blocks_goal(tmp_path):
    controller, tracker, manager = _controller(tmp_path, config=_config(mission="ship MVP"))
    controller.tick()
    goal = tracker.list_by_status(GoalStatus.RUNNING)[0]
    manager.tasks[0].status = "DONE"

    result = controller.tick()

    assert result.state == AutopilotState.BLOCKED
    assert result.reason == "task_gate_receipt_missing"
    assert tracker.get(goal.id).status == GoalStatus.BLOCKED


def test_restart_between_task_creation_and_ledger_write_relinks_without_duplicate(tmp_path, monkeypatch):
    controller, tracker, manager = _controller(tmp_path)
    goal_id = tracker.create("recover task")
    original = tracker.record_materialized_task
    failed_once = True

    def crash_once(*args, **kwargs):
        nonlocal failed_once
        if failed_once:
            failed_once = False
            raise OSError("simulated restart")
        return original(*args, **kwargs)

    monkeypatch.setattr(tracker, "record_materialized_task", crash_once)
    first = controller.tick()
    assert first.state == AutopilotState.ERROR
    assert len(manager.tasks) == 1

    monkeypatch.setattr(tracker, "record_materialized_task", original)
    controller._status.goal_id = goal_id
    recovered = controller.tick()
    assert recovered.state == AutopilotState.DISPATCHING
    assert "relinked:001" in recovered.actions
    assert len(manager.tasks) == 1


def test_failed_task_replans_once_then_blocks(tmp_path):
    controller, tracker, manager = _controller(tmp_path)
    tracker.create("retryable goal")
    first = controller.tick()
    goal_id = first.goal_id
    manager.tasks[0].status = "FAILED"

    replanning = controller.tick()
    assert replanning.state == AutopilotState.PLANNING
    assert tracker.get(goal_id).replans == 1

    materialized = controller.tick()
    assert materialized.state == AutopilotState.DISPATCHING
    assert len(manager.tasks) == 2
    manager.tasks[1].status = "FAILED"

    blocked = controller.tick()
    assert blocked.state == AutopilotState.BLOCKED
    assert tracker.get(goal_id).status == GoalStatus.BLOCKED


def test_kill_switch_and_budget_block_before_claim_or_materialization(tmp_path):
    controller, tracker, manager = _controller(tmp_path, kill_switch=lambda: True)
    blocked = controller.tick()
    assert blocked.state == AutopilotState.BLOCKED
    assert blocked.reason == "kill_switch"
    assert tracker.count() == 0
    assert manager.tasks == []

    exhausted = AutonomousBudget(max_cost_usd=0.0, max_wall_seconds=60, max_tool_calls=10)
    controller, tracker, manager = _controller(tmp_path / "budget", budget=exhausted)
    blocked = controller.tick()
    assert blocked.state == AutopilotState.BLOCKED
    assert blocked.reason == "budget_exhausted"
    assert tracker.count() == 0
    assert manager.tasks == []


def test_deny_all_is_observable_and_does_not_create_tasks(tmp_path):
    controller, tracker, manager = _controller(
        tmp_path,
        config=_config(approval_mode="deny_all", mission="needs approval"),
    )
    blocked = controller.tick()
    assert blocked.state == AutopilotState.BLOCKED
    assert blocked.reason == "approval_required"
    assert tracker.count() == 0
    assert manager.tasks == []


def test_enabled_without_mission_adopts_existing_kanban_task(tmp_path, monkeypatch):
    controller, tracker, manager = _controller(
        tmp_path,
        config=_config(mission=""),
    )
    manager.tasks.append(FakeTask("001", "Task existente", "TODO", {}))

    class FakeDispatcher:
        def __init__(self, _workspace):
            pass

        def mark_ready(self, task_id):
            task = manager.get_task(task_id)
            task.status = "READY"
            return task

    monkeypatch.setattr("bauer.task_dispatcher.TaskDispatcher", FakeDispatcher)

    result = controller.tick()
    assert result.state == AutopilotState.DISPATCHING
    assert result.reason == "kanban_task_adopted"
    assert tracker.count() == 0
    assert manager.tasks[0].status == "READY"


def test_mission_restarts_after_terminal_historical_goal(tmp_path):
    controller, tracker, manager = _controller(tmp_path, config=_config(mission="inspecionar"))
    old_goal = tracker.create("missão anterior")
    tracker.mark_complete(old_goal)

    result = controller.tick()

    assert result.state == AutopilotState.DISPATCHING
    assert tracker.count(GoalStatus.RUNNING) == 1
    assert len(manager.tasks) == 1


def test_configured_mission_does_not_duplicate_unfinished_legacy_task(tmp_path):
    mission = "Revisar bugs e melhorias"
    controller, tracker, manager = _controller(
        tmp_path,
        config=_config(mission=mission),
    )
    manager.tasks.append(
        FakeTask(
            "057",
            f"Execute: {mission}",
            "FAILED",
            {"dispatch": "true", "goal_id": "old-goal"},
        )
    )

    result = controller.tick()

    assert result.state == AutopilotState.BLOCKED
    assert result.reason == "mission_task_failed"
    assert tracker.count() == 0
    assert len(manager.tasks) == 1


def test_configured_mission_waits_for_existing_task_instead_of_seeding_goal(tmp_path):
    mission = "Revisar bugs e melhorias"
    controller, tracker, manager = _controller(
        tmp_path,
        config=_config(mission=mission),
    )
    manager.tasks.append(
        FakeTask(
            "057",
            f"Execute: {mission}",
            "READY",
            {"dispatch": "true", "goal_id": "old-goal"},
        )
    )

    result = controller.tick()

    assert result.state == AutopilotState.DISPATCHING
    assert result.reason == "mission_task_in_flight"
    assert tracker.count() == 0
    assert len(manager.tasks) == 1


def test_completed_latest_mission_supersedes_older_failed_duplicates(tmp_path):
    mission = "Revisar bugs e melhorias"
    controller, tracker, manager = _controller(
        tmp_path,
        config=_config(mission=mission),
    )
    manager.tasks.extend(
        [
            FakeTask(
                "057",
                f"Execute: {mission}",
                "FAILED",
                {"dispatch": "true", "goal_id": "old-goal"},
            ),
            FakeTask(
                "058",
                f"Execute: {mission}",
                "DONE",
                {"dispatch": "true", "goal_id": "latest-goal"},
            ),
        ]
    )

    result = controller.tick()

    assert result.state == AutopilotState.IDLE
    assert result.reason == "mission_completed"
    assert tracker.count() == 0


def test_completed_mission_falls_back_to_existing_kanban_task(tmp_path, monkeypatch):
    mission = "Revisar bugs e melhorias"
    controller, tracker, manager = _controller(
        tmp_path,
        config=_config(mission=mission),
    )
    manager.tasks.extend(
        [
            FakeTask(
                "057",
                f"Execute: {mission}",
                "DONE",
                {"dispatch": "true", "goal_id": "completed-goal"},
            ),
            FakeTask("003", "Criar PROJECT_CONTEXT.md", "TODO", {}),
        ]
    )

    class FakeDispatcher:
        def __init__(self, _workspace):
            pass

        def mark_ready(self, task_id):
            task = manager.get_task(task_id)
            task.status = "READY"
            return task

    monkeypatch.setattr("bauer.task_dispatcher.TaskDispatcher", FakeDispatcher)

    result = controller.tick()

    assert result.state == AutopilotState.DISPATCHING
    assert result.reason == "kanban_task_adopted"
    assert tracker.count() == 0
    assert manager.get_task("003").status == "READY"


def test_completed_mission_does_not_reseed_without_backlog(tmp_path):
    mission = "Revisar bugs e melhorias"
    controller, tracker, manager = _controller(
        tmp_path,
        config=_config(mission=mission),
    )
    manager.tasks.append(
        FakeTask(
            "057",
            f"Execute: {mission}",
            "DONE",
            {"dispatch": "true", "goal_id": "completed-goal"},
        )
    )

    result = controller.tick()

    assert result.state == AutopilotState.IDLE
    assert result.reason == "mission_completed"
    assert tracker.count() == 0
    assert len(manager.tasks) == 1


def test_pause_resume_and_operator_replan_are_idempotent(tmp_path):
    controller, tracker, manager = _controller(tmp_path)
    tracker.create("operator controlled")
    controller.pause()
    assert controller.tick().reason == "operator_pause"
    controller.pause()
    controller.resume()
    assert controller.tick().state == AutopilotState.DISPATCHING
    assert len(manager.tasks) == 1

    controller.request_replan()
    replanned = controller.tick()
    assert replanned.state == AutopilotState.DISPATCHING
    assert tracker.get(controller.status.goal_id).replans == 1
    assert len(manager.tasks) == 2


def test_status_and_events_are_operational_metadata_only(tmp_path):
    class EventCapture:
        def __init__(self):
            self.events = []

        def publish(self, topic, payload, source=""):
            self.events.append((topic, payload, source))

    events = EventCapture()
    controller, tracker, manager = _controller(
        tmp_path,
        config=_config(mission="secret mission must not be persisted"),
    )
    controller.event_bus = events
    result = controller.tick()
    assert result.state == AutopilotState.DISPATCHING
    status = controller.status_dict()
    assert status["mission_configured"] is True
    assert status["goal_title"] == "secret mission must not be persisted"
    assert "Autopilot mission" not in str(events.events)


def test_materialization_preserves_dispatch_metadata_in_both_task_backends(tmp_path):
    for backend in ("markdown", "sqlite"):
        workspace = tmp_path / backend
        manager = get_workspace_manager(workspace, backend=backend)
        manager.init_project("Autopilot test")
        tracker = GoalTracker(workspace / "goals.db")
        goal_id = tracker.create(f"backend {backend}")
        controller = AutopilotController(
            workspace,
            config=_config(),
            tracker=tracker,
            task_manager=manager,
            plan_fn=lambda _goal: [{"title": "governed step"}],
        )

        result = controller.tick()
        assert result.state == AutopilotState.DISPATCHING
        task = manager.list_tasks()[0]
        assert task.status == "READY"
        assert task.metadata["dispatch"] == "true"
        assert task.metadata["goal_id"] == goal_id
        assert task.metadata["step_key"]
        assert float(task.metadata["budget_max_cost_usd"]) == 2.0


def test_materialized_task_uses_stricter_autopilot_and_loop_cost_cap(tmp_path):
    workspace = tmp_path / "budget-cap"
    manager = get_workspace_manager(workspace, backend="sqlite")
    manager.init_project("Autopilot budget test")
    tracker = GoalTracker(workspace / "goals.db")
    tracker.create("budgeted mission")
    config = SimpleNamespace(
        autopilot=_config(max_cost_usd=2.0),
        loop=SimpleNamespace(max_cost_usd=0.35),
    )
    controller = AutopilotController(
        workspace,
        config=config,
        tracker=tracker,
        task_manager=manager,
        plan_fn=lambda _goal: [{"title": "budgeted step"}],
    )

    result = controller.tick()

    assert result.state == AutopilotState.DISPATCHING
    task = manager.list_tasks()[0]
    assert float(task.metadata["budget_max_cost_usd"]) == 0.35

    # A positive effective cap makes unknown receipt cost insufficient proof.
    receipt = {
        "schema": "bauer.dispatch-gate-receipt.v1",
        "status": "passed",
        "verifier": "task_dispatcher",
        "verification_id": "dispatch-run-1:verify-1",
        "kernel_run_id": "kernel-run-1",
        "kanban_task_id": task.id,
        "kanban_claim_id": "claim-1",
        "dispatcher_run_id": "dispatch-run-1",
        "gate_names": ["NonEmptyOutput"],
        "cost_known": False,
    }
    manager.update_task_metadata(task.id, metadata={"gate_receipt": json.dumps(receipt)})
    manager.update_task_status(task.id, "DONE")

    blocked = controller.tick()

    assert blocked.state == AutopilotState.BLOCKED
    assert blocked.reason == "task_cost_unknown"


def test_planner_materializes_single_parent_dependency(tmp_path):
    workspace = tmp_path / "single-parent"
    manager = get_workspace_manager(workspace, backend="markdown")
    manager.init_project("Autopilot dependency test")
    tracker = GoalTracker(workspace / "goals.db")
    tracker.create("ordered mission")
    controller = AutopilotController(
        workspace,
        config=_config(),
        tracker=tracker,
        task_manager=manager,
        plan_fn=lambda _goal: [
            {"id": 10, "title": "child", "depends_on": [20]},
            {"id": 20, "title": "parent", "depends_on": []},
        ],
    )

    result = controller.tick()

    assert result.state == AutopilotState.DISPATCHING
    tasks = {task.title: task for task in manager.list_tasks()}
    assert tasks["child"].parent_id == tasks["parent"].id


def test_planner_materializes_multiple_parents_in_sqlite(tmp_path):
    workspace = tmp_path / "multiple-parents"
    manager = get_workspace_manager(workspace, backend="sqlite")
    manager.init_project("Autopilot dependency test")
    tracker = GoalTracker(workspace / "goals.db")
    tracker.create("ordered mission")
    controller = AutopilotController(
        workspace,
        config=_config(),
        tracker=tracker,
        task_manager=manager,
        plan_fn=lambda _goal: [
            {"id": 1, "title": "first parent", "depends_on": []},
            {"id": 2, "title": "second parent", "depends_on": []},
            {"id": 3, "title": "child", "depends_on": [1, 2]},
        ],
    )

    result = controller.tick()

    assert result.state == AutopilotState.DISPATCHING
    tasks = {task.title: task for task in manager.list_tasks()}
    assert set(manager.get_task_parent_ids(tasks["child"].id)) == {
        tasks["first parent"].id,
        tasks["second parent"].id,
    }


def test_sqlite_task_is_not_dispatchable_until_dependency_edge_is_committed(
    tmp_path, monkeypatch
):
    from bauer import kanban_db

    workspace = tmp_path / "publish-order"
    manager = get_workspace_manager(workspace, backend="sqlite")
    manager.init_project("Autopilot publication order")
    tracker = GoalTracker(workspace / "goals.db")
    tracker.create("ordered mission")
    original_link = kanban_db.link_tasks
    saw_undispatchable_child = False

    def assert_child_held_back(conn, parent_id, child_id):
        nonlocal saw_undispatchable_child
        assert manager.get_task(child_id).status == "TODO"
        saw_undispatchable_child = True
        return original_link(conn, parent_id, child_id)

    monkeypatch.setattr(kanban_db, "link_tasks", assert_child_held_back)
    controller = AutopilotController(
        workspace,
        config=_config(),
        tracker=tracker,
        task_manager=manager,
        plan_fn=lambda _goal: [
            {"id": 1, "title": "parent"},
            {"id": 2, "title": "child", "depends_on": [1]},
        ],
    )

    controller.tick()

    assert saw_undispatchable_child
    assert manager.get_task_parent_ids("002") == ["001"]
    assert manager.get_task("002").status == "READY"


def test_planner_dependencies_are_idempotent_when_relinked(tmp_path):
    workspace = tmp_path / "relink"
    manager = get_workspace_manager(workspace, backend="sqlite")
    manager.init_project("Autopilot dependency test")
    tracker = GoalTracker(workspace / "goals.db")
    tracker.create("ordered mission")
    controller = AutopilotController(
        workspace,
        config=_config(),
        tracker=tracker,
        task_manager=manager,
        plan_fn=lambda _goal: [
            {"id": 1, "title": "parent"},
            {"id": 2, "title": "child", "depends_on": [1]},
        ],
    )

    controller.tick()
    controller._status.goal_id = tracker.list_by_status(GoalStatus.RUNNING)[0].id
    goal = tracker.get(controller.status.goal_id)
    controller._materialize(goal, [])
    tasks = {task.title: task for task in manager.list_tasks()}

    assert manager.get_task_parent_ids(tasks["child"].id) == [tasks["parent"].id]


def test_invalid_or_cyclic_planner_dependencies_are_rejected_before_persist(tmp_path):
    invalid_plans = [
        [{"id": 1, "title": "missing", "depends_on": [99]}],
        [{"id": 1, "title": "self", "depends_on": [1]}],
        [
            {"id": 1, "title": "first", "depends_on": [2]},
            {"id": 2, "title": "second", "depends_on": [1]},
        ],
    ]
    for index, plan in enumerate(invalid_plans):
        controller, tracker, manager = _controller(
            tmp_path / str(index), plan_fn=lambda _goal, plan=plan: plan
        )
        tracker.create("invalid mission")

        result = controller.tick()

        assert result.state == AutopilotState.ERROR
        goal = tracker.list_by_status(GoalStatus.RUNNING)[0]
        assert goal.steps == []
        assert manager.tasks == []


def test_markdown_backend_fails_closed_for_multiple_parents_before_materializing(tmp_path):
    workspace = tmp_path / "markdown-multiple-parents"
    manager = get_workspace_manager(workspace, backend="markdown")
    manager.init_project("Autopilot dependency test")
    tracker = GoalTracker(workspace / "goals.db")
    tracker.create("ordered mission")
    controller = AutopilotController(
        workspace,
        config=_config(),
        tracker=tracker,
        task_manager=manager,
        plan_fn=lambda _goal: [
            {"id": 1, "title": "first parent"},
            {"id": 2, "title": "second parent"},
            {"id": 3, "title": "child", "depends_on": [1, 2]},
        ],
    )

    result = controller.tick()

    assert result.state == AutopilotState.ERROR
    assert manager.list_tasks() == []
