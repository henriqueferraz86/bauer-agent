"""Tests for bauer/autonomous_planner.py and bauer/goal_tracker.py."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from bauer.autonomous_planner import (
    AutonomousPlanner,
    PlanEvent,
    PlannerConfig,
    PlanResult,
    PlanStatus,
    PlanStep,
    StepStatus,
)
from bauer.goal_tracker import GoalRecord, GoalStatus, GoalTracker


# ===========================================================================
# GoalTracker
# ===========================================================================


class TestGoalTracker:
    def _tracker(self) -> GoalTracker:
        return GoalTracker(db_path=":memory:")

    # ── create / get ────────────────────────────────────────────────────────

    def test_create_returns_id(self):
        t = self._tracker()
        gid = t.create("Fix the bug")
        assert gid.startswith("goal_")

    def test_get_existing_goal(self):
        t = self._tracker()
        gid = t.create("My goal", description="details", priority=3)
        rec = t.get(gid)
        assert rec is not None
        assert rec.title == "My goal"
        assert rec.description == "details"
        assert rec.priority == 3
        assert rec.status == GoalStatus.PENDING

    def test_get_nonexistent_returns_none(self):
        t = self._tracker()
        assert t.get("goal_doesnotexist") is None

    def test_title_truncated_at_200(self):
        t = self._tracker()
        long_title = "x" * 300
        gid = t.create(long_title)
        assert len(t.get(gid).title) == 200

    # ── status transitions ───────────────────────────────────────────────────

    def test_update_status_to_running(self):
        t = self._tracker()
        gid = t.create("g")
        ok = t.update_status(gid, GoalStatus.RUNNING)
        assert ok is True
        rec = t.get(gid)
        assert rec.status == GoalStatus.RUNNING
        assert rec.started_at is not None

    def test_update_status_running_sets_started_at_once(self):
        t = self._tracker()
        gid = t.create("g")
        t.update_status(gid, GoalStatus.RUNNING)
        started = t.get(gid).started_at
        time.sleep(0.01)
        t.update_status(gid, GoalStatus.RUNNING)  # second call
        # started_at should NOT change on second RUNNING update
        assert t.get(gid).started_at == started

    def test_update_status_done_sets_completed_at(self):
        t = self._tracker()
        gid = t.create("g")
        t.update_status(gid, GoalStatus.RUNNING)
        t.update_status(gid, GoalStatus.DONE)
        rec = t.get(gid)
        assert rec.status == GoalStatus.DONE
        assert rec.completed_at is not None

    def test_update_status_failed_with_error(self):
        t = self._tracker()
        gid = t.create("g")
        t.update_status(gid, GoalStatus.FAILED, error="boom")
        rec = t.get(gid)
        assert rec.status == GoalStatus.FAILED
        assert rec.error == "boom"

    def test_update_status_returns_false_for_unknown_id(self):
        t = self._tracker()
        assert t.update_status("goal_nope", GoalStatus.DONE) is False

    def test_update_status_string_input(self):
        t = self._tracker()
        gid = t.create("g")
        t.update_status(gid, "running")
        assert t.get(gid).status == GoalStatus.RUNNING

    # ── mark_complete / mark_failed / cancel ────────────────────────────────

    def test_mark_complete(self):
        t = self._tracker()
        gid = t.create("g")
        ok = t.mark_complete(gid, summary="all done")
        assert ok is True
        assert t.get(gid).status == GoalStatus.DONE

    def test_mark_failed(self):
        t = self._tracker()
        gid = t.create("g")
        t.mark_failed(gid, error="network error")
        rec = t.get(gid)
        assert rec.status == GoalStatus.FAILED
        assert rec.error == "network error"

    def test_cancel(self):
        t = self._tracker()
        gid = t.create("g")
        t.cancel(gid)
        assert t.get(gid).status == GoalStatus.CANCELLED

    def test_delete(self):
        t = self._tracker()
        gid = t.create("g")
        ok = t.delete(gid)
        assert ok is True
        assert t.get(gid) is None

    # ── update_steps ────────────────────────────────────────────────────────

    def test_update_steps_persists_json(self):
        t = self._tracker()
        gid = t.create("g")
        steps = [{"title": "step 1", "status": "done"}, {"title": "step 2", "status": "pending"}]
        t.update_steps(gid, steps)
        rec = t.get(gid)
        assert len(rec.steps) == 2
        assert rec.steps[0]["title"] == "step 1"

    # ── list queries ─────────────────────────────────────────────────────────

    def test_list_active_returns_pending_and_running(self):
        t = self._tracker()
        g1 = t.create("pending goal")
        g2 = t.create("running goal")
        g3 = t.create("done goal")
        t.update_status(g2, GoalStatus.RUNNING)
        t.update_status(g3, GoalStatus.DONE)
        active = t.list_active()
        ids = [r.id for r in active]
        assert g1 in ids
        assert g2 in ids
        assert g3 not in ids

    def test_list_active_ordered_by_priority(self):
        t = self._tracker()
        g_low = t.create("low priority", priority=9)
        g_high = t.create("high priority", priority=1)
        active = t.list_active()
        ids = [r.id for r in active]
        assert ids.index(g_high) < ids.index(g_low)

    def test_list_by_status(self):
        t = self._tracker()
        g1 = t.create("a")
        g2 = t.create("b")
        t.mark_failed(g1)
        records = t.list_by_status(GoalStatus.FAILED)
        assert any(r.id == g1 for r in records)
        assert not any(r.id == g2 for r in records)

    def test_list_all_most_recent_first(self):
        t = self._tracker()
        g1 = t.create("first")
        time.sleep(0.01)
        g2 = t.create("second")
        all_goals = t.list_all()
        assert all_goals[0].id == g2
        assert all_goals[1].id == g1

    def test_count_total(self):
        t = self._tracker()
        t.create("a")
        t.create("b")
        assert t.count() == 2

    def test_count_by_status(self):
        t = self._tracker()
        g1 = t.create("a")
        t.create("b")
        t.mark_complete(g1)
        assert t.count(GoalStatus.DONE) == 1
        assert t.count(GoalStatus.PENDING) == 1

    # ── GoalRecord properties ─────────────────────────────────────────────

    def test_record_is_terminal(self):
        t = self._tracker()
        gid = t.create("g")
        t.mark_complete(gid)
        rec = t.get(gid)
        assert rec.is_terminal is True

    def test_record_not_terminal_when_running(self):
        t = self._tracker()
        gid = t.create("g")
        t.update_status(gid, GoalStatus.RUNNING)
        rec = t.get(gid)
        assert rec.is_terminal is False

    def test_elapsed_seconds_none_when_not_started(self):
        t = self._tracker()
        gid = t.create("g")
        rec = t.get(gid)
        assert rec.elapsed_seconds is None

    def test_elapsed_seconds_computed(self):
        t = self._tracker()
        gid = t.create("g")
        t.update_status(gid, GoalStatus.RUNNING)
        time.sleep(0.02)
        t.mark_complete(gid)
        rec = t.get(gid)
        assert rec.elapsed_seconds is not None
        assert rec.elapsed_seconds >= 0.01

    # ── session_id ─────────────────────────────────────────────────────────

    def test_session_id_stored(self):
        t = GoalTracker(db_path=":memory:", session_id="sess_abc")
        gid = t.create("g")
        assert t.get(gid).session_id == "sess_abc"

    def test_session_id_override_per_goal(self):
        t = GoalTracker(db_path=":memory:", session_id="default_sess")
        gid = t.create("g", session_id="override_sess")
        assert t.get(gid).session_id == "override_sess"

    # ── file-backed DB ────────────────────────────────────────────────────

    def test_persistent_db(self, tmp_path):
        db = tmp_path / "goals.db"
        t1 = GoalTracker(db_path=db)
        gid = t1.create("persistent goal")
        del t1  # close connection

        t2 = GoalTracker(db_path=db)
        rec = t2.get(gid)
        assert rec is not None
        assert rec.title == "persistent goal"


# ===========================================================================
# PlanStep
# ===========================================================================


class TestPlanStep:
    def test_to_dict_round_trip(self):
        step = PlanStep(title="run tests", max_retries=3, timeout_seconds=30.0)
        d = step.to_dict()
        step2 = PlanStep.from_dict(d)
        assert step2.title == "run tests"
        assert step2.max_retries == 3
        assert step2.timeout_seconds == 30.0

    def test_from_dict_defaults(self):
        step = PlanStep.from_dict({"title": "minimal"})
        assert step.max_retries == 2
        assert step.status == StepStatus.PENDING
        assert step.attempts == 0


# ===========================================================================
# AutonomousPlanner
# ===========================================================================


class TestAutonomousPlanner:
    def _planner(self, **kw) -> AutonomousPlanner:
        return AutonomousPlanner(PlannerConfig(**kw))

    @pytest.mark.asyncio
    async def test_execute_goal_success_default(self):
        p = self._planner()
        result = await p.execute_goal("goal_1", "Fix bug")
        assert result.status == PlanStatus.DONE
        assert result.steps_done == 1
        assert result.steps_total == 1

    @pytest.mark.asyncio
    async def test_execute_goal_custom_decompose(self):
        async def decompose(title, desc):
            return [PlanStep("step A"), PlanStep("step B"), PlanStep("step C")]

        p = self._planner(decompose_fn=decompose)
        result = await p.execute_goal("goal_2", "Multi-step goal")
        assert result.status == PlanStatus.DONE
        assert result.steps_done == 3
        assert result.steps_total == 3

    @pytest.mark.asyncio
    async def test_step_failure_propagates_to_plan(self):
        call_count = [0]

        async def failing_execute(step, goal_id):
            call_count[0] += 1
            raise RuntimeError("simulated failure")

        # max_retries=0 to fail immediately
        async def decompose(title, desc):
            return [PlanStep("fail step", max_retries=0)]

        p = self._planner(decompose_fn=decompose, execute_fn=failing_execute)
        result = await p.execute_goal("goal_3", "Failing goal")
        assert result.status == PlanStatus.FAILED
        assert result.steps_failed == 1
        assert call_count[0] == 1  # 0 retries + 1 attempt

    @pytest.mark.asyncio
    async def test_step_retry_on_failure(self):
        call_count = [0]

        async def flaky_execute(step, goal_id):
            call_count[0] += 1
            if call_count[0] < 3:
                raise RuntimeError("transient error")
            # 3rd attempt succeeds

        async def decompose(title, desc):
            return [PlanStep("flaky step", max_retries=3)]

        p = self._planner(decompose_fn=decompose, execute_fn=flaky_execute)
        result = await p.execute_goal("goal_4", "Flaky goal")
        assert result.status == PlanStatus.DONE
        assert call_count[0] == 3  # failed twice, succeeded on 3rd

    @pytest.mark.asyncio
    async def test_step_exceeds_max_retries(self):
        call_count = [0]

        async def always_fail(step, goal_id):
            call_count[0] += 1
            raise RuntimeError("always fails")

        async def decompose(title, desc):
            return [PlanStep("bad step", max_retries=2)]

        p = self._planner(decompose_fn=decompose, execute_fn=always_fail)
        result = await p.execute_goal("goal_5", "Always fails")
        assert result.status == PlanStatus.FAILED
        assert call_count[0] == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_step_timeout_causes_failure(self):
        async def slow_execute(step, goal_id):
            await asyncio.sleep(10.0)  # much longer than timeout

        async def decompose(title, desc):
            return [PlanStep("slow step", max_retries=0, timeout_seconds=0.05)]

        p = self._planner(decompose_fn=decompose, execute_fn=slow_execute)
        result = await p.execute_goal("goal_6", "Slow goal")
        assert result.status == PlanStatus.FAILED

    @pytest.mark.asyncio
    async def test_max_steps_truncation(self):
        async def many_steps(title, desc):
            return [PlanStep(f"step {i}") for i in range(30)]

        p = self._planner(decompose_fn=many_steps, max_steps=5)
        result = await p.execute_goal("goal_7", "Many steps goal")
        assert result.steps_total == 5

    @pytest.mark.asyncio
    async def test_shutdown_event_cancels_plan(self):
        executed_steps = []
        shutdown = asyncio.Event()

        async def slow_execute(step, goal_id):
            executed_steps.append(step.title)
            if len(executed_steps) == 1:
                shutdown.set()

        async def decompose(title, desc):
            return [PlanStep(f"step {i}") for i in range(5)]

        p = self._planner(decompose_fn=decompose, execute_fn=slow_execute)
        result = await p.execute_goal(
            "goal_8", "Goal with shutdown",
            shutdown_event=shutdown,
        )
        assert result.status == PlanStatus.CANCELLED
        assert len(executed_steps) == 1

    @pytest.mark.asyncio
    async def test_events_emitted(self):
        events = []

        async def on_event(ev: PlanEvent):
            events.append(ev)

        async def decompose(title, desc):
            return [PlanStep("step one"), PlanStep("step two")]

        p = self._planner(decompose_fn=decompose, on_event=on_event)
        result = await p.execute_goal("goal_9", "Tracked goal")
        assert result.status == PlanStatus.DONE

        kinds = [e.kind for e in events]
        assert "step_started" in kinds
        assert "step_done" in kinds
        assert "plan_done" in kinds

    @pytest.mark.asyncio
    async def test_failed_decompose_returns_failed_result(self):
        async def broken_decompose(title, desc):
            raise ValueError("bad decompose")

        p = self._planner(decompose_fn=broken_decompose)
        result = await p.execute_goal("goal_10", "Broken decompose goal")
        assert result.status == PlanStatus.FAILED
        assert "decompose failed" in result.error

    @pytest.mark.asyncio
    async def test_empty_decompose_falls_back_to_single_step(self):
        async def empty_decompose(title, desc):
            return []

        p = self._planner(decompose_fn=empty_decompose)
        result = await p.execute_goal("goal_11", "Empty decompose")
        assert result.status == PlanStatus.DONE
        assert result.steps_total == 1

    @pytest.mark.asyncio
    async def test_execute_with_goal_tracker(self, tmp_path):
        """Full integration: planner updates GoalTracker throughout."""
        tracker = GoalTracker(db_path=":memory:")
        gid = tracker.create("tracked goal")

        async def decompose(title, desc):
            return [PlanStep("task A"), PlanStep("task B")]

        p = self._planner(decompose_fn=decompose)
        result = await p.execute_goal(gid, "tracked goal", tracker=tracker)

        assert result.status == PlanStatus.DONE
        rec = tracker.get(gid)
        assert rec.status == GoalStatus.DONE
        assert len(rec.steps) >= 2

    @pytest.mark.asyncio
    async def test_failed_goal_updates_tracker(self):
        tracker = GoalTracker(db_path=":memory:")
        gid = tracker.create("will fail")

        async def failing_execute(step, goal_id):
            raise RuntimeError("oops")

        async def decompose(title, desc):
            return [PlanStep("bad step", max_retries=0)]

        p = self._planner(decompose_fn=decompose, execute_fn=failing_execute)
        result = await p.execute_goal(gid, "will fail", tracker=tracker)

        assert result.status == PlanStatus.FAILED
        rec = tracker.get(gid)
        assert rec.status == GoalStatus.FAILED

    @pytest.mark.asyncio
    async def test_elapsed_seconds_populated(self):
        p = self._planner()
        result = await p.execute_goal("goal_12", "Quick goal")
        assert result.elapsed_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_plan_result_fields(self):
        async def decompose(title, desc):
            return [PlanStep("a"), PlanStep("b")]

        p = self._planner(decompose_fn=decompose)
        result = await p.execute_goal("goal_13", "Two steps")
        assert result.goal_id == "goal_13"
        assert result.steps_total == 2
        assert result.steps_done == 2
        assert result.steps_failed == 0
        assert result.error is None

    @pytest.mark.asyncio
    async def test_inter_step_delay(self):
        """inter_step_delay_seconds is respected."""
        start = time.monotonic()

        async def decompose(title, desc):
            return [PlanStep("a"), PlanStep("b")]

        p = self._planner(decompose_fn=decompose, inter_step_delay_seconds=0.05)
        await p.execute_goal("goal_14", "Delayed steps")
        elapsed = time.monotonic() - start
        # 1 delay between step a and b → ≥ 0.05s
        assert elapsed >= 0.04

    @pytest.mark.asyncio
    async def test_step_step_timeout_applied_from_config(self):
        """step_timeout_seconds from config applied to steps with no override."""
        async def slow(step, goal_id):
            await asyncio.sleep(10.0)

        async def decompose(title, desc):
            return [PlanStep("slow step", max_retries=0)]  # no timeout_seconds

        p = self._planner(
            decompose_fn=decompose,
            execute_fn=slow,
            step_timeout_seconds=0.05,
        )
        result = await p.execute_goal("goal_15", "Config timeout")
        assert result.status == PlanStatus.FAILED

    @pytest.mark.asyncio
    async def test_on_event_exception_does_not_crash(self):
        """A crashing on_event callback should not propagate."""
        async def bad_event(ev: PlanEvent):
            raise RuntimeError("callback crash")

        p = self._planner(on_event=bad_event)
        result = await p.execute_goal("goal_16", "Event crash goal")
        assert result.status == PlanStatus.DONE  # planner survives bad callback
