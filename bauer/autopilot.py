"""Governed persistent mission controller for the always-on runtime.

The autopilot is deliberately a producer/reconciler.  It claims goals,
turns planner steps into durable Kanban tasks, and observes task outcomes.  It
never invokes a provider, shell, dispatcher worker, or Kernel run directly;
the existing dispatcher remains the sole task executor.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Sequence

from .autonomous_budget import AutonomousBudget
from .goal_tracker import GoalRecord, GoalStatus, GoalTracker
from .workspace_manager_factory import get_workspace_manager

logger = logging.getLogger(__name__)


class AutopilotState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    IDLE = "idle"
    PLANNING = "planning"
    DISPATCHING = "dispatching"
    VERIFYING = "verifying"
    BLOCKED = "blocked"
    ERROR = "error"
    STOPPING = "stopping"


@dataclass(frozen=True)
class TickResult:
    """Safe, compact result of one controller reconciliation cycle."""

    state: AutopilotState
    goal_id: str | None = None
    actions: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class AutopilotStatus:
    """Operational status persisted by the controller."""

    state: AutopilotState = AutopilotState.STOPPED
    goal_id: str | None = None
    goal_title: str = ""
    last_cycle_at: float | None = None
    heartbeat_at: float | None = None
    reason: str = ""
    last_error: str = ""
    tasks_pending: int = 0
    tasks_in_progress: int = 0
    budget: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "goal_id": self.goal_id,
            "goal_title": self.goal_title,
            "last_cycle_at": self.last_cycle_at,
            "heartbeat_at": self.heartbeat_at,
            "reason": self.reason,
            "last_error": self.last_error,
            "tasks_pending": self.tasks_pending,
            "tasks_in_progress": self.tasks_in_progress,
            "budget": self.budget,
        }


PlanFn = Callable[[GoalRecord], Sequence[Any]]
KillSwitchFn = Callable[[], bool]


def _default_plan(goal: GoalRecord) -> list[dict[str, str]]:
    """Deterministic fallback plan; no provider is called by the controller."""
    return [{"title": goal.title, "description": goal.description}]


def _valid_goal_gate_receipt(
    receipt: Any,
    task_id: str,
    *,
    require_known_cost: bool = True,
    require_known_tools: bool = True,
    require_known_time: bool = True,
) -> bool:
    """A DONE materialized task only proves goal completion with gate evidence."""
    cost_usd = receipt.get("cost_usd") if isinstance(receipt, dict) else None
    tool_calls = receipt.get("tool_calls") if isinstance(receipt, dict) else None
    elapsed_seconds = receipt.get("elapsed_seconds") if isinstance(receipt, dict) else None
    return bool(
        isinstance(receipt, dict)
        and receipt.get("schema") == "bauer.dispatch-gate-receipt.v1"
        and receipt.get("status") == "passed"
        and receipt.get("verifier") == "task_dispatcher"
        and receipt.get("verification_id")
        and receipt.get("kernel_run_id")
        and receipt.get("kanban_task_id") == task_id
        and receipt.get("kanban_claim_id")
        and receipt.get("dispatcher_run_id")
        and (
            not require_known_cost
            or (
                receipt.get("cost_known") is True
                and isinstance(cost_usd, (float, int))
                and cost_usd >= 0
            )
        )
        and (
            not require_known_tools
            or isinstance(tool_calls, int) and tool_calls >= 0
        )
        and (
            not require_known_time
            or isinstance(elapsed_seconds, (int, float))
            and elapsed_seconds >= 0
        )
        and isinstance(receipt.get("gate_names"), list)
        and receipt["gate_names"]
        and all(isinstance(gate, str) and gate for gate in receipt["gate_names"])
    )


class AutopilotController:
    """Persistent, bounded controller that feeds the existing dispatcher."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        config: Any,
        tracker: GoalTracker | None = None,
        task_manager: Any | None = None,
        plan_fn: PlanFn | None = None,
        event_bus: Any | None = None,
        kill_switch: KillSwitchFn | None = None,
        budget: AutonomousBudget | None = None,
        session_id: str | None = None,
        state_file: str | Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.root_config = config
        self.config = getattr(config, "autopilot", config)
        self.session_id = session_id or f"autopilot_{uuid.uuid4().hex[:12]}"
        runtime_dir = self.workspace / ".bauer_runtime"
        self.tracker = tracker or GoalTracker(
            runtime_dir / "goals.db", session_id=self.session_id
        )
        self.task_manager = task_manager or get_workspace_manager(self.workspace)
        self.plan_fn = plan_fn or _default_plan
        self.event_bus = event_bus
        self.kill_switch = kill_switch or (lambda: False)
        self.budget = budget or self._build_budget()
        self.state_file = Path(state_file) if state_file else runtime_dir / "autopilot.json"
        self.pause_file = runtime_dir / "AUTOPILOT_PAUSE"
        self.replan_file = runtime_dir / "AUTOPILOT_REPLAN"
        self.clock = clock
        self.stop_event = threading.Event()
        self._paused = False
        self._replan_requested = False
        self._manual_replan_requested = False
        self._status = AutopilotStatus()
        self._load_status()

    # ------------------------------------------------------------------
    # Lifecycle and operator controls
    # ------------------------------------------------------------------

    @property
    def status(self) -> AutopilotStatus:
        return self._status

    def pause(self, reason: str = "operator_pause") -> dict[str, Any]:
        self.pause_file.parent.mkdir(parents=True, exist_ok=True)
        self.pause_file.write_text(reason[:200], encoding="utf-8")
        self._paused = True
        self._set_state(AutopilotState.BLOCKED, reason=reason)
        return self.status_dict()

    def resume(self) -> dict[str, Any]:
        try:
            self.pause_file.unlink()
        except FileNotFoundError:
            logger.debug("autopilot pause marker already absent")
        self._paused = False
        self._set_state(AutopilotState.IDLE, reason="resumed")
        return self.status_dict()

    def request_replan(self) -> dict[str, Any]:
        self.replan_file.parent.mkdir(parents=True, exist_ok=True)
        self.replan_file.write_text("requested", encoding="utf-8")
        self._replan_requested = True
        self._manual_replan_requested = True
        self._emit("autopilot.replan_requested", goal_id=self._status.goal_id)
        return self.status_dict()

    def stop(self) -> dict[str, Any]:
        self._set_state(AutopilotState.STOPPING, reason="operator_stop")
        self.stop_event.set()
        return self.status_dict()

    def run_forever(self) -> None:
        """Run ticks until SIGTERM/stop is requested, sleeping between ticks."""
        self.stop_event.clear()
        self._set_state(AutopilotState.STARTING)
        try:
            while not self.stop_event.is_set():
                self.tick()
                self.stop_event.wait(max(1.0, float(self.config.poll_interval_s)))
        except KeyboardInterrupt:
            self.stop_event.set()
        finally:
            self._set_state(AutopilotState.STOPPING, reason="stopped")
            self._set_state(AutopilotState.STOPPED, reason="stopped")

    # ------------------------------------------------------------------
    # Controller cycle
    # ------------------------------------------------------------------

    def tick(self) -> TickResult:
        """Perform one bounded reconciliation cycle without executing work."""
        now = self.clock()
        self._apply_control_files()
        self._status.last_cycle_at = now
        self._status.heartbeat_at = now
        self._persist_status()

        if not bool(getattr(self.config, "enabled", False)):
            return self._result(AutopilotState.STOPPED, reason="disabled")
        if self.stop_event.is_set():
            return self._result(AutopilotState.STOPPING, reason="stop_requested")
        if self._paused:
            return self._result(AutopilotState.BLOCKED, reason="operator_pause")
        if self._blocked_by_guardrails():
            return self._result(self._status.state, reason=self._status.reason)

        actions: list[str] = []
        self.tracker.release_or_requeue_stale(now=now)
        current = self._current_goal()
        if current is not None and self._manual_replan_requested:
            if current.replans >= int(self.config.max_replans_per_goal):
                self._manual_replan_requested = False
                self._replan_requested = False
                self.tracker.update_status(current.id, GoalStatus.BLOCKED, error="max_replans_exceeded")
                return self._result(
                    AutopilotState.BLOCKED,
                    goal_id=current.id,
                    reason="max_replans_exceeded",
                )
            count = self.tracker.increment_replans(current.id)
            self.tracker.clear_materialized_tasks(current.id)
            self._manual_replan_requested = False
            current = self.tracker.get(current.id)
            if current is None:
                return self._result(AutopilotState.ERROR, reason="goal_disappeared")
            self._set_state(AutopilotState.PLANNING, reason="operator_replan")
            self._emit("autopilot.replan", goal_id=current.id, count=count or 0)
        if current is not None:
            if not self._replan_requested:
                reconciliation = self._reconcile(current, actions)
                if reconciliation is not None:
                    return reconciliation

        if current is None:
            seeded = self._seed_mission_if_needed()
            if seeded is not None:
                current = self._claim_goal()
            else:
                mission = str(getattr(self.config, "mission", "") or "").strip()
                existing = self._find_existing_mission_task(mission)
                if existing is not None:
                    status = str(getattr(existing, "status", "")).upper()
                    if status in {"FAILED", "BLOCKED"}:
                        return self._result(
                            AutopilotState.BLOCKED,
                            reason="mission_task_failed",
                        )
                    if status != "DONE":
                        return self._result(
                            AutopilotState.DISPATCHING,
                            reason="mission_task_in_flight",
                        )
        if current is None:
            mission = str(getattr(self.config, "mission", "") or "").strip()
            existing = self._find_existing_mission_task(mission)
            mission_completed = (
                existing is not None
                and str(getattr(existing, "status", "")).upper() == "DONE"
            )
            adopted = self._adopt_existing_kanban_task(
                allow_configured_mission=mission_completed,
            )
            if adopted is not None:
                return adopted
            if mission_completed:
                return self._result(AutopilotState.IDLE, reason="mission_completed")
            if not self.tracker.list_active():
                return self._result(AutopilotState.BLOCKED, reason="mission_required")
            current = self._claim_goal()
        if current is None:
            return self._result(AutopilotState.IDLE, actions=actions, reason="no_goal")

        # A pending goal recovered after a supervisor restart is claimed only
        # in this cycle. Reconcile it once more after claiming so a task that
        # finished while the controller was down can complete the goal without
        # an unnecessary extra dispatch cycle.
        if not self._replan_requested:
            reconciliation = self._reconcile(current, actions)
            if reconciliation is not None:
                return reconciliation

        self._status.goal_id = current.id
        if current.status != GoalStatus.RUNNING or current.lease_owner != self.session_id:
            return self._result(AutopilotState.IDLE, goal_id=current.id, reason="not_owned")
        self.tracker.heartbeat(current.id, self.session_id)

        try:
            if self._needs_plan(current):
                self._set_state(AutopilotState.PLANNING)
                current = self._plan(current)
            self._set_state(AutopilotState.DISPATCHING)
            self._materialize(current, actions)
            self.tracker.heartbeat(current.id, self.session_id)
            return self._result(
                AutopilotState.DISPATCHING,
                goal_id=current.id,
                actions=actions,
                reason="tasks_materialized" if actions else "awaiting_tasks",
            )
        except Exception as exc:  # controller must remain alive after one bad tick
            message = str(exc)[:500]
            self._status.last_error = message
            self._set_state(AutopilotState.ERROR, reason="cycle_error")
            self._emit("autopilot.failed", goal_id=current.id, reason="cycle_error")
            logger.exception("autopilot tick failed for goal %s", current.id)
            return self._result(AutopilotState.ERROR, goal_id=current.id, reason=message)

    # ------------------------------------------------------------------
    # Reconciliation and materialization
    # ------------------------------------------------------------------

    def _reconcile(self, goal: GoalRecord, actions: list[str]) -> TickResult | None:
        links = self.tracker.list_materialized_tasks(goal.id)
        if not links:
            return None
        self._set_state(AutopilotState.VERIFYING)
        tasks = []
        for link in links:
            try:
                tasks.append(self.task_manager.get_task(link["task_id"]))
            except Exception:
                tasks.append(None)

        statuses = [getattr(task, "status", "MISSING") for task in tasks]
        if any(status in {"BLOCKED", "MISSING"} for status in statuses):
            self.tracker.update_status(goal.id, GoalStatus.BLOCKED, error="task_blocked_or_missing")
            self._emit("autopilot.goal.blocked", goal_id=goal.id, reason="task_blocked_or_missing")
            self._status.goal_id = None
            return self._result(AutopilotState.BLOCKED, goal_id=goal.id, reason="task_blocked_or_missing")
        if any(status == "FAILED" for status in statuses):
            return self._handle_failed_goal(goal)
        if statuses and all(status == "DONE" for status in statuses):
            for task in tasks:
                receipt = getattr(task, "metadata", {}).get("gate_receipt")
                if isinstance(receipt, str):
                    try:
                        receipt = json.loads(receipt)
                    except (TypeError, json.JSONDecodeError):
                        receipt = None
                cap = getattr(task, "metadata", {}).get("budget_max_cost_usd", "1")
                try:
                    require_known_cost = float(cap) > 0
                except (TypeError, ValueError):
                    require_known_cost = True
                try:
                    require_known_tools = float(
                        getattr(task, "metadata", {}).get("budget_max_tool_calls", 1)
                    ) > 0
                except (TypeError, ValueError):
                    require_known_tools = True
                try:
                    require_known_time = float(
                        getattr(task, "metadata", {}).get("budget_max_minutes", 1)
                    ) > 0
                except (TypeError, ValueError):
                    require_known_time = True
                if not _valid_goal_gate_receipt(
                    receipt,
                    str(getattr(task, "id", "")),
                    require_known_cost=require_known_cost,
                    require_known_tools=require_known_tools,
                    require_known_time=require_known_time,
                ):
                    reason = (
                        "task_cost_unknown"
                        if require_known_cost
                        and isinstance(receipt, dict)
                        and receipt.get("cost_known") is not True
                        else "task_gate_receipt_missing"
                    )
                    self.tracker.update_status(
                        goal.id, GoalStatus.BLOCKED, error=reason
                    )
                    self._emit(
                        "autopilot.goal.blocked",
                        goal_id=goal.id,
                        reason=reason,
                    )
                    self._status.goal_id = None
                    return self._result(
                        AutopilotState.BLOCKED,
                        goal_id=goal.id,
                        reason=reason,
                    )
            self.tracker.mark_complete(goal.id)
            self._emit("autopilot.goal.completed", goal_id=goal.id)
            self._status.goal_id = None
            actions.append("goal_completed")
            return self._result(AutopilotState.IDLE, goal_id=goal.id, actions=actions, reason="goal_completed")
        self.tracker.heartbeat(goal.id, self.session_id)
        return self._result(AutopilotState.DISPATCHING, goal_id=goal.id, reason="tasks_in_flight")

    def _handle_failed_goal(self, goal: GoalRecord) -> TickResult:
        if goal.replans < int(self.config.max_replans_per_goal):
            count = self.tracker.increment_replans(goal.id)
            self.tracker.clear_materialized_tasks(goal.id)
            self._replan_requested = True
            self._manual_replan_requested = False
            self._set_state(AutopilotState.PLANNING, reason="failed_task_replan")
            self._emit("autopilot.replan", goal_id=goal.id, count=count or 0)
            return self._result(AutopilotState.PLANNING, goal_id=goal.id, reason="failed_task_replan")
        self.tracker.update_status(goal.id, GoalStatus.BLOCKED, error="max_replans_exceeded")
        self._emit("autopilot.goal.blocked", goal_id=goal.id, reason="max_replans_exceeded")
        self._status.goal_id = None
        return self._result(AutopilotState.BLOCKED, goal_id=goal.id, reason="max_replans_exceeded")

    def _plan(self, goal: GoalRecord) -> GoalRecord:
        raw_steps = list(self.plan_fn(goal))
        if not raw_steps:
            raise ValueError("planner returned no steps")
        steps: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_steps):
            if hasattr(raw, "to_dict"):
                item = dict(raw.to_dict())
            elif hasattr(raw, "model_dump"):
                item = dict(raw.model_dump())
            elif isinstance(raw, dict):
                item = dict(raw)
            else:
                item = {"title": str(raw)}
            title = str(item.get("title") or "").strip()
            if not title:
                # PlannerOutput/PlanStep currently calls this field ``goal``.
                title = str(item.get("goal") or "").strip()
            if not title:
                raise ValueError(f"planner step {index} has no title")
            item["title"] = title[:500]
            item.setdefault("description", "")
            item["status"] = "pending"
            item["metadata"] = dict(item.get("metadata") or {})
            # Planner contracts use integer IDs. Legacy plans without IDs use
            # their one-based position, preserving the historical shape.
            item["id"] = self._normalize_step_id(item.get("id", index + 1), index)
            item["depends_on"] = self._normalize_dependencies(
                item.get("depends_on", []), item["id"], index
            )
            item["metadata"]["step_key"] = self._step_key(goal, index, title)
            steps.append(item)
        self._validate_plan_graph(steps)
        self.tracker.update_steps(goal.id, steps)
        self._replan_requested = False
        refreshed = self.tracker.get(goal.id)
        if refreshed is None:
            raise ValueError(f"goal disappeared during planning: {goal.id}")
        return refreshed

    def _materialize(self, goal: GoalRecord, actions: list[str]) -> None:
        steps = [dict(step) for step in goal.steps]
        # Validate persisted/recovered plans too: they may predate validation
        # or have been written by an older controller version.
        for index, step in enumerate(steps):
            step["id"] = self._normalize_step_id(step.get("id", index + 1), index)
            step["depends_on"] = self._normalize_dependencies(
                step.get("depends_on", []), step["id"], index
            )
        self._validate_plan_graph(steps)
        parents_by_step = {
            step["id"]: [str(parent) for parent in step["depends_on"]]
            for step in steps
        }
        if self._is_markdown_backend() and any(
            len(parents) > 1 for parents in parents_by_step.values()
        ):
            raise ValueError("Markdown Kanban supports only one parent per task")

        # Topological order guarantees that every parent task exists before
        # the child is created, even when the planner lists steps out of order.
        ordered_steps = self._topological_steps(steps)
        task_ids_by_step: dict[str, str] = {}
        from .task_dispatcher import TaskDispatcher

        dispatcher = TaskDispatcher(self.workspace)
        for step in ordered_steps:
            index = steps.index(step)
            metadata = dict(step.get("metadata") or {})
            step_key = str(metadata.get("step_key") or self._step_key(goal, index, str(step.get("title", ""))))
            task_id = self.tracker.get_materialized_task(goal.id, step_key)
            if task_id is None:
                existing = self._find_task(goal.id, step_key)
                if existing is not None:
                    task_id = existing.id
                    self.tracker.record_materialized_task(goal.id, task_id, step_key)
                    actions.append(f"relinked:{task_id}")
                else:
                    task = self.task_manager.add_task(
                        str(step["title"]),
                        description=str(step.get("description") or ""),
                        # SQLite edges are separate rows. Keep a new card
                        # undispatchable until all predecessors are committed.
                        status="TODO" if self._is_sqlite_backend() else "READY",
                        parent_id=(
                            task_ids_by_step[parents_by_step[str(step["id"])][0]]
                            if self._is_markdown_backend() and parents_by_step[str(step["id"])]
                            else ""
                        ),
                        metadata={
                            "dispatch": "true",
                            "autopilot_mission": goal.title,
                            "goal_id": goal.id,
                            "step_key": step_key,
                            "budget_max_cost_usd": self._effective_max_cost_usd(),
                            "budget_max_minutes": self._effective_max_minutes(),
                            "budget_max_tool_calls": self._effective_max_tool_calls(),
                        },
                    )
                    task_id = task.id
                    self.tracker.record_materialized_task(goal.id, task_id, step_key)
                    actions.append(f"materialized:{task_id}")
                    self._emit("autopilot.task.materialized", goal_id=goal.id, task_id=task_id)
            self._set_task_budget_metadata(task_id)
            task_ids_by_step[str(step["id"])] = str(task_id)
            desired_parents = [
                task_ids_by_step[parent] for parent in parents_by_step[str(step["id"])]
            ]
            with dispatcher._lock():
                current_task = self.task_manager.get_task(str(task_id))
                if current_task.status == "IN_PROGRESS":
                    parent_reader = getattr(self.task_manager, "get_task_parent_ids", None)
                    current_parents = (
                        parent_reader(str(task_id)) if callable(parent_reader)
                        else ([current_task.parent_id] if current_task.parent_id else [])
                    )
                    if set(current_parents) != set(desired_parents):
                        raise ValueError("cannot revise dependencies of an in-progress task")
                elif self._is_sqlite_backend() and current_task.status in {"READY", "TODO"}:
                    self.task_manager.update_task_status(str(task_id), "TODO")
                self._sync_task_dependencies(str(task_id), desired_parents)
                if self._is_sqlite_backend() and current_task.status in {"READY", "TODO"}:
                    self.task_manager.update_task_status(str(task_id), "READY")
            metadata["task_id"] = task_id
            step["metadata"] = metadata
            step["status"] = "dispatching"
        self.tracker.update_steps(goal.id, steps)

    @staticmethod
    def _normalize_step_id(value: Any, index: int) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError(f"planner step {index} has invalid id")
        try:
            step_id = int(str(value).strip())
        except ValueError as exc:
            raise ValueError(f"planner step {index} has invalid id") from exc
        if step_id < 1:
            raise ValueError(f"planner step {index} id must be positive")
        return str(step_id)

    @staticmethod
    def _normalize_dependencies(raw: Any, step_id: str, index: int) -> list[str]:
        if raw is None:
            raw = []
        if not isinstance(raw, (list, tuple, set)):
            raise ValueError(f"planner step {index} depends_on must be a list")
        dependencies: list[str] = []
        for dependency in raw:
            try:
                normalized_int = AutopilotController._normalize_step_id(dependency, index)
            except ValueError as exc:
                raise ValueError(f"planner step {index} has invalid dependency id") from exc
            normalized = normalized_int
            if normalized == step_id:
                raise ValueError(f"planner step {step_id} cannot depend on itself")
            if normalized not in dependencies:
                dependencies.append(normalized)
        return dependencies

    @staticmethod
    def _validate_plan_graph(steps: list[dict[str, Any]]) -> None:
        ids = [str(step["id"]) for step in steps]
        if len(ids) != len(set(ids)):
            raise ValueError("planner step IDs must be unique")
        known = set(ids)
        for step in steps:
            missing = [parent for parent in step["depends_on"] if parent not in known]
            if missing:
                raise ValueError(
                    f"planner step {step['id']} depends on missing step(s): {', '.join(missing)}"
                )
        AutopilotController._topological_steps(steps)

    @staticmethod
    def _topological_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id = {str(step["id"]): step for step in steps}
        pending = {step_id: set(step["depends_on"]) for step_id, step in by_id.items()}
        ordered: list[dict[str, Any]] = []
        while pending:
            ready = [step_id for step_id in by_id if step_id in pending and not pending[step_id]]
            if not ready:
                raise ValueError("planner dependencies contain a cycle")
            for step_id in ready:
                ordered.append(by_id[step_id])
                del pending[step_id]
                for dependencies in pending.values():
                    dependencies.discard(step_id)
        return ordered

    def _is_markdown_backend(self) -> bool:
        from .workspace_manager import WorkspaceManager

        return isinstance(self.task_manager, WorkspaceManager)

    def _is_sqlite_backend(self) -> bool:
        from .workspace_manager_sqlite import WorkspaceManagerSqlite

        return isinstance(self.task_manager, WorkspaceManagerSqlite)

    def _sync_task_dependencies(self, task_id: str, parent_ids: list[str]) -> None:
        """Make the task's parent set match the validated planner DAG."""
        if self._is_markdown_backend():
            task = self.task_manager.get_task(task_id)
            current_parent = str(getattr(task, "parent_id", "") or "")
            desired_parent = parent_ids[0] if parent_ids else ""
            if current_parent != desired_parent:
                self.task_manager.update_task_metadata(task_id, parent_id=desired_parent)
            return

        # The SQLite WorkspaceManager exposes the full predecessor query but
        # has no public multi-parent mutator yet. Use its Kanban connection and
        # canonical DAG primitives, retaining their cycle check/idempotency.
        from . import kanban_db as kb
        from .workspace_manager_sqlite import WorkspaceManagerSqlite

        if not isinstance(self.task_manager, WorkspaceManagerSqlite):
            if parent_ids:
                raise ValueError("task backend cannot represent planner dependencies")
            return
        conn = self.task_manager._connect()
        try:
            existing = set(kb.parents_of(conn, task_id))
            desired = set(parent_ids)
            for parent_id in sorted(existing - desired):
                kb.unlink_tasks(conn, parent_id, task_id)
            for parent_id in parent_ids:
                kb.link_tasks(conn, parent_id, task_id)
        finally:
            conn.close()

    def _set_task_budget_metadata(self, task_id: str) -> None:
        """Persist the effective cost cap also when repairing/relinking a task."""
        limits = {
            "budget_max_cost_usd": self._effective_max_cost_usd(),
            "budget_max_minutes": self._effective_max_minutes(),
            "budget_max_tool_calls": self._effective_max_tool_calls(),
        }
        update_metadata = getattr(self.task_manager, "update_task_metadata", None)
        if callable(update_metadata):
            update_metadata(task_id, metadata=limits)
            return
        # Lightweight task managers may expose mutable task records without
        # the workspace metadata update API.
        try:
            task = self.task_manager.get_task(task_id)
        except (AttributeError, KeyError):
            return
        metadata = getattr(task, "metadata", None)
        if isinstance(metadata, dict):
            metadata.update({key: str(value) for key, value in limits.items()})

    def _find_task(self, goal_id: str, step_key: str) -> Any | None:
        for task in self.task_manager.list_tasks():
            metadata = getattr(task, "metadata", {}) or {}
            if metadata.get("goal_id") == goal_id and metadata.get("step_key") == step_key:
                return task
        return None

    def _find_existing_mission_task(self, mission: str) -> Any | None:
        """Find the newest task representing the configured mission.

        Mission deduplication must survive goal replans and controller restarts.
        The goal id is intentionally not part of this lookup: a new goal for
        the same continuous mission must not create another Kanban card while
        its previous card is still unfinished. The metadata marker covers new
        tasks; the title match keeps older workspaces idempotent after upgrade.
        Keeping the newest DONE card in the result is important: old FAILED
        duplicates must not block the next mission after the canonical card
        completes.
        """
        clean_mission = " ".join(str(mission or "").split()).casefold()
        if not clean_mission:
            return None
        accepted_titles = {clean_mission, f"execute: {clean_mission}"}
        candidates: list[Any] = []
        for task in self.task_manager.list_tasks():
            metadata = getattr(task, "metadata", {}) or {}
            marker = " ".join(str(metadata.get("autopilot_mission", "")).split()).casefold()
            title = " ".join(str(getattr(task, "title", "")).split()).casefold()
            is_marked = marker == clean_mission
            is_legacy_mission = (
                title in accepted_titles
                and (
                    str(metadata.get("dispatch", "")).casefold() == "true"
                    or bool(metadata.get("goal_id"))
                    or title.startswith("execute: ")
                )
            )
            if is_marked or is_legacy_mission:
                candidates.append(task)

        if not candidates:
            return None

        # Workspace managers return tasks in creation order. Selecting the
        # newest matching card makes the repair monotonic when legacy duplicate
        # cards already exist: the latest card is the canonical one.
        return candidates[-1]

    # ------------------------------------------------------------------
    # Goal selection and guards
    # ------------------------------------------------------------------

    def _build_budget(self) -> AutonomousBudget:
        """Apply the stricter of autopilot and global loop limits."""
        loop = getattr(self.root_config, "loop", None)
        max_minutes = int(self.config.max_minutes)
        max_tool_calls = int(self.config.max_tool_calls)
        if loop is not None:
            max_minutes = min(max_minutes, int(getattr(loop, "max_minutes", max_minutes)))
            max_tool_calls = min(max_tool_calls, int(getattr(loop, "max_tool_calls", max_tool_calls)))
        return AutonomousBudget(
            max_wall_seconds=max_minutes * 60,
            max_tool_calls=max_tool_calls,
            max_cost_usd=self._effective_max_cost_usd(),
        )

    def _effective_max_cost_usd(self) -> float:
        """Return the stricter configured cost cap for this mission's workers."""
        autopilot_cap = float(getattr(self.config, "max_cost_usd", 1_000_000.0))
        loop = getattr(self.root_config, "loop", None)
        if loop is None:
            return autopilot_cap
        return min(autopilot_cap, float(getattr(loop, "max_cost_usd", autopilot_cap)))

    def _effective_max_minutes(self) -> int:
        limit = int(getattr(self.config, "max_minutes", 525_600))
        loop = getattr(self.root_config, "loop", None)
        return min(limit, int(getattr(loop, "max_minutes", limit))) if loop is not None else limit

    def _effective_max_tool_calls(self) -> int:
        limit = int(getattr(self.config, "max_tool_calls", 100_000_000))
        loop = getattr(self.root_config, "loop", None)
        return min(limit, int(getattr(loop, "max_tool_calls", limit))) if loop is not None else limit

    def _current_goal(self) -> GoalRecord | None:
        goal_id = self._status.goal_id
        if not goal_id:
            return None
        goal = self.tracker.get(goal_id)
        if goal is None or goal.status != GoalStatus.RUNNING:
            self._status.goal_id = None
            return None
        if goal.lease_owner != self.session_id:
            self._status.goal_id = None
            return None
        return goal

    def _seed_mission_if_needed(self) -> GoalRecord | None:
        mission = str(getattr(self.config, "mission", "") or "").strip()
        if not mission:
            return None
        # A missão configurada é uma semente inicial. Depois que sua task
        # canônica termina, o controller segue as tasks existentes do Kanban;
        # resemeá-la a cada tick criaria um loop infinito de missões idênticas.
        if self.tracker.list_active():
            return None
        existing = self._find_existing_mission_task(mission)
        if existing is not None:
            return None
        goal_id = self.tracker.create(mission, description="Autopilot mission")
        self._emit("autopilot.goal.created", goal_id=goal_id)
        return self.tracker.get(goal_id)

    def _adopt_existing_kanban_task(
        self,
        *,
        allow_configured_mission: bool = False,
    ) -> TickResult | None:
        """Promove uma task TODO quando o operador não declarou missão.

        O dispatcher continua sendo o único executor. Este caminho apenas
        opta uma task existente para a fila READY, uma por vez, para manter a
        sequência e não inundar o workspace com workers concorrentes.
        """
        if str(getattr(self.config, "mission", "") or "").strip() and not allow_configured_mission:
            return None
        tasks = list(self.task_manager.list_tasks())
        if any(getattr(task, "status", "") in {"READY", "IN_PROGRESS"} for task in tasks):
            return self._result(AutopilotState.DISPATCHING, reason="kanban_tasks_in_flight")
        candidates = [task for task in tasks if getattr(task, "status", "") == "TODO"]
        if not candidates:
            return None

        def _priority(task: Any) -> tuple[int, str]:
            order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            return order.get(str(getattr(task, "priority", "medium")).lower(), 2), str(task.id)

        task = sorted(candidates, key=_priority)[0]
        from .task_dispatcher import TaskDispatcher

        ready = TaskDispatcher(self.workspace).mark_ready(task.id)
        return self._result(
            AutopilotState.DISPATCHING,
            actions=[f"kanban_ready:{ready.id}"],
            reason="kanban_task_adopted",
        )

    def _claim_goal(self) -> GoalRecord | None:
        running = self.tracker.list_by_status(GoalStatus.RUNNING)
        if len(running) >= int(self.config.max_active_goals):
            self._set_state(AutopilotState.IDLE, reason="max_active_goals")
            return None
        goal = self.tracker.claim_next(self.session_id, lease_seconds=self._lease_seconds())
        if goal is not None:
            self._emit("autopilot.goal.claimed", goal_id=goal.id)
        return goal

    def _needs_plan(self, goal: GoalRecord) -> bool:
        if self._replan_requested:
            return True
        return not goal.steps or all(
            str(step.get("status", "pending")) in {"failed", "skipped"}
            for step in goal.steps
        )

    def _apply_control_files(self) -> None:
        if self.pause_file.exists():
            self._paused = True
            self._status.reason = "operator_pause"
        elif self._paused and self._status.reason == "operator_pause":
            self._paused = False
        if self.replan_file.exists():
            self._replan_requested = True
            self._manual_replan_requested = True
            try:
                self.replan_file.unlink()
            except FileNotFoundError:
                logger.debug("autopilot replan marker already absent")

    def _blocked_by_guardrails(self) -> bool:
        if bool(self.kill_switch()):
            self._set_state(AutopilotState.BLOCKED, reason="kill_switch")
            self._emit("autopilot.blocked", reason="kill_switch")
            return True
        if self.budget.is_exhausted:
            self._set_state(AutopilotState.BLOCKED, reason="budget_exhausted")
            self._emit("autopilot.blocked", reason="budget_exhausted")
            return True
        if str(getattr(self.config, "approval_mode", "threshold")) == "deny_all":
            self._set_state(AutopilotState.BLOCKED, reason="approval_required")
            self._emit("autopilot.blocked", reason="approval_required")
            return True
        return False

    def _lease_seconds(self) -> int:
        return max(30, int(float(getattr(self.config, "poll_interval_s", 30.0)) * 3))

    def _step_key(self, goal: GoalRecord, index: int, title: str) -> str:
        digest = hashlib.sha256(f"{goal.id}:{goal.replans}:{index}:{title}".encode()).hexdigest()[:16]
        return f"goal-{goal.id}-step-{digest}"

    # ------------------------------------------------------------------
    # Status/event plumbing
    # ------------------------------------------------------------------

    def status_dict(self) -> dict[str, Any]:
        data = self._status.to_dict()
        if self._status.goal_id:
            try:
                goal = self.tracker.get(self._status.goal_id)
                data["goal_title"] = goal.title if goal is not None else ""
            except Exception:
                data["goal_title"] = ""
        try:
            tasks = self.task_manager.list_tasks()
            data["tasks_pending"] = sum(
                1 for task in tasks if getattr(task, "status", "") in {"TODO", "READY"}
            )
            data["tasks_in_progress"] = sum(
                1 for task in tasks if getattr(task, "status", "") == "IN_PROGRESS"
            )
        except Exception:
            logger.debug("autopilot task status unavailable", exc_info=True)
        try:
            data["budget"] = self.budget.to_dict()
        except Exception:
            data["budget"] = {}
        data["mission_configured"] = bool(str(getattr(self.config, "mission", "") or "").strip())
        data["workspace"] = str(self.workspace)
        data["session_id"] = self.session_id
        return data

    def _result(
        self,
        state: AutopilotState,
        *,
        goal_id: str | None = None,
        actions: list[str] | None = None,
        reason: str = "",
    ) -> TickResult:
        self._set_state(state, reason=reason)
        if goal_id is not None:
            self._status.goal_id = goal_id
        self._persist_status()
        return TickResult(state=state, goal_id=goal_id or self._status.goal_id, actions=tuple(actions or ()), reason=reason)

    def _set_state(self, state: AutopilotState, *, reason: str = "") -> None:
        changed = self._status.state != state
        self._status.state = state
        if reason:
            self._status.reason = reason[:200]
        self._status.heartbeat_at = self.clock()
        if changed:
            self._emit(f"autopilot.{state.value}", goal_id=self._status.goal_id, reason=reason)
        self._persist_status()

    def _emit(self, topic: str, **payload: Any) -> None:
        if self.event_bus is None:
            return
        safe = {key: value for key, value in payload.items() if key in {"goal_id", "task_id", "reason", "count"} and value is not None}
        try:
            self.event_bus.publish(topic, safe, source="autopilot")
        except TypeError:
            self.event_bus.publish(topic, safe)
        except Exception:
            logger.debug("autopilot event publish failed", exc_info=True)

    def _persist_status(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.status_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.state_file)
        except OSError:
            logger.debug("autopilot status persistence failed", exc_info=True)

    def _load_status(self) -> None:
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            state = str(raw.get("state", AutopilotState.STOPPED.value))
            self._status.state = AutopilotState(state)
            self._status.goal_id = raw.get("goal_id")
            self._status.goal_title = str(raw.get("goal_title") or "")[:200]
            self._status.reason = str(raw.get("reason") or "")[:200]
            self._status.last_error = str(raw.get("last_error") or "")[:500]
        except (OSError, json.JSONDecodeError, ValueError):
            return


__all__ = ["AutopilotController", "AutopilotState", "AutopilotStatus", "TickResult"]
