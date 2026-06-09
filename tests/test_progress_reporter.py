"""Tests for bauer/progress_reporter.py."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from bauer.progress_reporter import (
    BudgetSummary,
    ProgressReport,
    ProgressReporter,
    WorkerSummary,
)


# ===========================================================================
# BudgetSummary
# ===========================================================================


class TestBudgetSummary:
    def test_defaults(self):
        b = BudgetSummary()
        assert b.status == "ok"
        assert b.cost_pct == 0.0

    def test_from_budget_none(self):
        b = BudgetSummary.from_budget(None)
        assert b.cost_usd == 0.0
        assert b.status == "ok"

    def test_from_budget_live(self):
        from bauer.autonomous_budget import AutonomousBudget

        budget = AutonomousBudget(max_cost_usd=1.0, max_llm_calls=10)
        budget._cost_usd = 0.5
        budget._llm_calls = 3

        b = BudgetSummary.from_budget(budget)
        assert b.cost_usd == pytest.approx(0.5)
        assert b.cost_pct == pytest.approx(50.0)
        assert b.llm_calls == 3
        assert b.status == "ok"

    def test_from_budget_exhausted(self):
        from bauer.autonomous_budget import AutonomousBudget

        budget = AutonomousBudget(max_cost_usd=1.0)
        budget._cost_usd = 1.0  # exactly at limit

        b = BudgetSummary.from_budget(budget)
        assert b.status == "exhausted"


# ===========================================================================
# WorkerSummary
# ===========================================================================


class TestWorkerSummary:
    def test_defaults(self):
        w = WorkerSummary(worker_id=0)
        assert w.healthy is True
        assert w.consecutive_failures == 0


# ===========================================================================
# ProgressReport
# ===========================================================================


class TestProgressReport:
    def _report(self, **kw) -> ProgressReport:
        defaults = dict(
            session_id="sess_1",
            timestamp=time.time(),
            period_seconds=60.0,
        )
        defaults.update(kw)
        return ProgressReport(**defaults)

    def test_summary_contains_session(self):
        r = self._report()
        assert "sess_1" in r.summary()

    def test_summary_contains_budget_status(self):
        r = self._report(budget=BudgetSummary(status="warning", cost_pct=82.0))
        s = r.summary()
        assert "warning" in s

    def test_to_dict_structure(self):
        r = self._report(tasks_completed=5, tasks_failed=1)
        d = r.to_dict()
        assert d["session_id"] == "sess_1"
        assert d["tasks"]["completed"] == 5
        assert d["tasks"]["failed"] == 1
        assert "budget" in d
        assert "workers" in d

    def test_is_healthy_true_by_default(self):
        r = self._report()
        assert r.is_healthy is True

    def test_is_healthy_false_on_exhausted_budget(self):
        r = self._report(budget=BudgetSummary(status="exhausted"))
        assert r.is_healthy is False

    def test_is_healthy_false_when_more_failures_than_completions(self):
        r = self._report(tasks_completed=1, tasks_failed=2)
        assert r.is_healthy is False

    def test_is_healthy_false_with_unhealthy_worker(self):
        r = self._report(workers=[WorkerSummary(worker_id=0, healthy=False)])
        assert r.is_healthy is False

    def test_notes_in_to_dict(self):
        r = self._report(notes=["budget warning", "worker 0 unhealthy"])
        d = r.to_dict()
        assert len(d["notes"]) == 2


# ===========================================================================
# ProgressReporter
# ===========================================================================


class TestProgressReporter:
    def _reporter(self, **kw) -> ProgressReporter:
        return ProgressReporter(session_id="sess_test", **kw)

    # ── metric recording ────────────────────────────────────────────────────

    def test_record_task_completed(self):
        r = self._reporter()
        r.record_task_completed()
        r.record_task_completed()
        assert r.total_tasks_completed == 2

    def test_record_task_failed(self):
        r = self._reporter()
        r.record_task_failed()
        assert r.total_tasks_failed == 1

    def test_record_escalation(self):
        r = self._reporter()
        r.record_escalation()
        assert r.total_escalations == 1

    def test_record_trigger_fired(self):
        r = self._reporter()
        r.record_trigger_fired()
        assert r.total_triggers_fired == 1

    # ── generate ────────────────────────────────────────────────────────────

    def test_generate_returns_report(self):
        r = self._reporter()
        report = r.generate()
        assert isinstance(report, ProgressReport)
        assert report.session_id == "sess_test"

    def test_generate_includes_period_tasks(self):
        r = self._reporter()
        r.record_task_completed()
        r.record_task_completed()
        r.record_task_failed()
        report = r.generate()
        assert report.tasks_completed == 2
        assert report.tasks_failed == 1

    def test_generate_resets_period_counters(self):
        r = self._reporter()
        r.record_task_completed()
        r.generate()  # first report
        r.record_task_completed()
        report2 = r.generate()  # second report
        # Only 1 new task in second period
        assert report2.tasks_completed == 1

    def test_generate_includes_escalations(self):
        r = self._reporter()
        r.record_escalation()
        r.record_escalation()
        report = r.generate()
        assert report.escalations == 2

    def test_generate_escalations_reset_next_period(self):
        r = self._reporter()
        r.record_escalation()
        r.generate()
        r.record_escalation()
        report2 = r.generate()
        assert report2.escalations == 1

    def test_generate_includes_triggers(self):
        r = self._reporter()
        r.record_trigger_fired()
        report = r.generate()
        assert report.triggers_fired == 1

    # ── budget integration ───────────────────────────────────────────────────

    def test_generate_with_budget(self):
        from bauer.autonomous_budget import AutonomousBudget

        budget = AutonomousBudget(max_cost_usd=1.0, max_llm_calls=10)
        budget._cost_usd = 0.8

        r = self._reporter()
        r.set_budget(budget)
        report = r.generate()
        assert report.budget.cost_pct == pytest.approx(80.0)
        assert report.budget.status == "warning"

    def test_generate_notes_budget_warning(self):
        from bauer.autonomous_budget import AutonomousBudget

        budget = AutonomousBudget(max_cost_usd=1.0, warn_at_cost_pct=0.7)
        budget._cost_usd = 0.75

        r = self._reporter()
        r.set_budget(budget)
        report = r.generate()
        assert any("warning" in n for n in report.notes)

    # ── worker integration ───────────────────────────────────────────────────

    def test_generate_with_workers(self):
        from bauer.process_supervisor import ProcessSupervisor

        sup = ProcessSupervisor(worker_id=0, max_restarts=5)
        sup.record_failure(RuntimeError("oops"))

        r = self._reporter()
        r.set_workers([sup])
        report = r.generate()
        assert len(report.workers) == 1
        assert report.workers[0].consecutive_failures == 1

    def test_generate_notes_unhealthy_worker(self):
        from bauer.process_supervisor import ProcessSupervisor

        sup = ProcessSupervisor(worker_id=1, max_restarts=1)
        sup.record_failure(RuntimeError("a"))
        sup.record_failure(RuntimeError("b"))  # exceeds max_restarts

        r = self._reporter()
        r.set_workers([sup])
        report = r.generate()
        assert any("unhealthy" in n for n in report.notes)

    # ── goal tracker integration ─────────────────────────────────────────────

    def test_generate_with_goal_tracker(self):
        from bauer.goal_tracker import GoalTracker, GoalStatus

        tracker = GoalTracker(db_path=":memory:")
        g1 = tracker.create("goal A")
        g2 = tracker.create("goal B")
        tracker.update_status(g1, GoalStatus.RUNNING)
        tracker.update_status(g2, GoalStatus.DONE)

        r = self._reporter()
        r.set_goal_tracker(tracker)
        report = r.generate()
        assert report.goals_active >= 1
        assert report.goals_done >= 1

    # ── throughput ────────────────────────────────────────────────────────────

    def test_throughput_computed(self):
        r = self._reporter()
        # Record tasks and then fake the period
        for _ in range(6):
            r.record_task_completed()
        # Backdate the last report time to simulate 1 minute elapsed
        r._last_report_time = r._last_report_time - 60
        report = r.generate()
        # 6 tasks / 60s * 3600 = 360 tasks/hour
        assert report.throughput_tasks_per_hour == pytest.approx(360.0, rel=0.1)

    # ── lifetime_stats ────────────────────────────────────────────────────────

    def test_lifetime_stats_structure(self):
        r = self._reporter()
        r.record_task_completed()
        r.record_escalation()
        stats = r.lifetime_stats()
        assert stats["session_id"] == "sess_test"
        assert stats["total_tasks_completed"] == 1
        assert stats["total_escalations"] == 1
        assert "elapsed_seconds" in stats

    def test_lifetime_totals_not_reset_by_generate(self):
        r = self._reporter()
        r.record_task_completed()
        r.record_task_completed()
        r.generate()  # resets period counters
        r.record_task_completed()
        # Total should be 3 even after generate
        assert r.total_tasks_completed == 3

    # ── summary ──────────────────────────────────────────────────────────────

    def test_summary_string(self):
        r = self._reporter()
        r.record_task_completed()
        report = r.generate()
        s = report.summary()
        assert "sess_test" in s
        assert "tasks=" in s

    def test_summary_with_notes(self):
        r = self._reporter()
        r.record_escalation()
        from bauer.autonomous_budget import AutonomousBudget
        budget = AutonomousBudget(max_cost_usd=1.0)
        budget._cost_usd = 1.0  # exhausted
        r.set_budget(budget)
        report = r.generate()
        s = report.summary()
        assert "notes" in s
