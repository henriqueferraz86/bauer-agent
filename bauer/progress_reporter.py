"""Progress reporter — structured progress updates for the autonomous agent.

The reporter aggregates metrics from across the system (budget, goals,
workers, triggers) and emits periodic structured reports.  Reports can
be consumed by:

* A human operator watching logs
* An external monitoring system (Prometheus, Grafana, etc.)
* The daemon's own decision loop

Report structure
----------------
::

    ProgressReport {
        session_id: str
        timestamp: float
        period_seconds: float        # seconds since last report
        goals_active: int
        goals_done: int
        goals_failed: int
        tasks_completed: int
        tasks_failed: int
        budget: BudgetSummary {cost_pct, time_pct, status}
        workers: list[WorkerSummary {id, healthy, failures}]
        escalations: int             # escalations since last report
        triggers_fired: int          # trigger events since last report
        throughput_tasks_per_hour: float
        notes: list[str]             # human-readable observations
    }

Usage::

    from bauer.progress_reporter import ProgressReporter

    reporter = ProgressReporter(session_id="daemon_abc")
    reporter.record_task_completed()
    reporter.record_escalation()

    report = reporter.generate()
    print(report.summary())
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Sub-dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BudgetSummary:
    cost_usd: float = 0.0
    max_cost_usd: float = 0.0
    cost_pct: float = 0.0
    elapsed_seconds: float = 0.0
    max_wall_seconds: int = 0
    time_pct: float = 0.0
    llm_calls: int = 0
    max_llm_calls: int = 0
    tool_calls: int = 0
    max_tool_calls: int = 0
    status: str = "ok"   # "ok" | "warning" | "exhausted"

    @classmethod
    def from_budget(cls, budget: Any) -> "BudgetSummary":
        """Build from an :class:`~bauer.autonomous_budget.AutonomousBudget`."""
        if budget is None:
            return cls()
        snap = budget.snapshot()
        return cls(
            cost_usd=round(snap.cost_usd, 4),
            max_cost_usd=snap.max_cost_usd,
            cost_pct=round(snap.cost_pct, 1),
            elapsed_seconds=round(snap.elapsed_seconds, 1),
            max_wall_seconds=snap.max_wall_seconds,
            time_pct=round(snap.time_pct, 1),
            llm_calls=snap.llm_calls,
            max_llm_calls=snap.max_llm_calls,
            tool_calls=snap.tool_calls,
            max_tool_calls=snap.max_tool_calls,
            status=snap.status.value,
        )


@dataclass
class WorkerSummary:
    worker_id: int | str
    healthy: bool = True
    consecutive_failures: int = 0
    total_failures: int = 0


@dataclass
class ProgressReport:
    """Snapshot of the daemon's current state."""

    session_id: str
    timestamp: float
    period_seconds: float

    goals_active: int = 0
    goals_done: int = 0
    goals_failed: int = 0

    tasks_completed: int = 0
    tasks_failed: int = 0

    budget: BudgetSummary = field(default_factory=BudgetSummary)
    workers: list[WorkerSummary] = field(default_factory=list)

    escalations: int = 0
    triggers_fired: int = 0

    throughput_tasks_per_hour: float = 0.0

    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        lines = [
            f"[{self.session_id}] period={self.period_seconds:.0f}s | "
            f"goals={self.goals_active}active/{self.goals_done}done | "
            f"tasks={self.tasks_completed}done/{self.tasks_failed}fail | "
            f"budget={self.budget.status}({self.budget.cost_pct:.0f}%$ "
            f"{self.budget.time_pct:.0f}%t) | "
            f"escalations={self.escalations} | "
            f"throughput={self.throughput_tasks_per_hour:.1f}/h"
        ]
        if self.notes:
            lines.append("  notes: " + "; ".join(self.notes))
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "period_seconds": self.period_seconds,
            "goals": {
                "active": self.goals_active,
                "done": self.goals_done,
                "failed": self.goals_failed,
            },
            "tasks": {
                "completed": self.tasks_completed,
                "failed": self.tasks_failed,
                "throughput_per_hour": self.throughput_tasks_per_hour,
            },
            "budget": {
                "cost_usd": self.budget.cost_usd,
                "cost_pct": self.budget.cost_pct,
                "time_pct": self.budget.time_pct,
                "status": self.budget.status,
            },
            "workers": [
                {
                    "id": w.worker_id,
                    "healthy": w.healthy,
                    "consecutive_failures": w.consecutive_failures,
                }
                for w in self.workers
            ],
            "escalations": self.escalations,
            "triggers_fired": self.triggers_fired,
            "notes": self.notes,
        }

    @property
    def is_healthy(self) -> bool:
        """True if no critical issues are detected."""
        if self.budget.status == "exhausted":
            return False
        if self.tasks_failed > self.tasks_completed:
            return False
        if any(not w.healthy for w in self.workers):
            return False
        return True


# ---------------------------------------------------------------------------
# ProgressReporter
# ---------------------------------------------------------------------------


class ProgressReporter:
    """Accumulate metrics and generate periodic :class:`ProgressReport` objects.

    Parameters
    ----------
    session_id:
        Daemon session identifier.
    report_interval_seconds:
        Minimum seconds between reports (used for throughput calculation).
    """

    def __init__(
        self,
        session_id: str = "unknown",
        *,
        report_interval_seconds: float = 60.0,
    ) -> None:
        self._session_id = session_id
        self._interval = report_interval_seconds

        # Counters reset between reports
        self._period_tasks_completed: int = 0
        self._period_tasks_failed: int = 0
        self._period_escalations: int = 0
        self._period_triggers: int = 0

        # Lifetime totals
        self._total_tasks_completed: int = 0
        self._total_tasks_failed: int = 0
        self._total_escalations: int = 0
        self._total_triggers: int = 0

        # Timestamps
        self._start_time: float = time.monotonic()
        self._last_report_time: float = time.monotonic()

        # Latest external state snapshots
        self._budget: Any = None
        self._workers: list[Any] = []  # list[ProcessSupervisor]
        self._goal_tracker: Any = None  # GoalTracker | None

    # ------------------------------------------------------------------
    # Metric recording
    # ------------------------------------------------------------------

    def record_task_completed(self) -> None:
        self._period_tasks_completed += 1
        self._total_tasks_completed += 1

    def record_task_failed(self) -> None:
        self._period_tasks_failed += 1
        self._total_tasks_failed += 1

    def record_escalation(self) -> None:
        self._period_escalations += 1
        self._total_escalations += 1

    def record_trigger_fired(self) -> None:
        self._period_triggers += 1
        self._total_triggers += 1

    # ------------------------------------------------------------------
    # External state snapshots
    # ------------------------------------------------------------------

    def set_budget(self, budget: Any) -> None:
        """Inject the live :class:`~bauer.autonomous_budget.AutonomousBudget`."""
        self._budget = budget

    def set_workers(self, supervisors: list[Any]) -> None:
        """Inject the list of live :class:`~bauer.process_supervisor.ProcessSupervisor`."""
        self._workers = supervisors

    def set_goal_tracker(self, tracker: Any) -> None:
        """Inject the live :class:`~bauer.goal_tracker.GoalTracker`."""
        self._goal_tracker = tracker

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def generate(self) -> ProgressReport:
        """Build and return a :class:`ProgressReport` for the current period."""
        now = time.monotonic()
        period = now - self._last_report_time

        # Budget summary
        budget_summary = BudgetSummary.from_budget(self._budget)

        # Worker summaries
        workers: list[WorkerSummary] = []
        for sup in self._workers:
            workers.append(WorkerSummary(
                worker_id=sup.worker_id,
                healthy=sup.is_healthy,
                consecutive_failures=sup.consecutive_failures,
                total_failures=sup.total_failures,
            ))

        # Goal counts from tracker
        goals_active = goals_done = goals_failed = 0
        if self._goal_tracker is not None:
            try:
                from .goal_tracker import GoalStatus
                goals_active = self._goal_tracker.count(GoalStatus.RUNNING)
                goals_active += self._goal_tracker.count(GoalStatus.PENDING)
                goals_done = self._goal_tracker.count(GoalStatus.DONE)
                goals_failed = self._goal_tracker.count(GoalStatus.FAILED)
            except Exception:
                pass

        # Throughput: tasks completed in this period, extrapolated to 1h
        throughput = (
            (self._period_tasks_completed / period * 3600) if period > 0 else 0.0
        )

        # Notes / observations
        notes: list[str] = []
        if budget_summary.status == "warning":
            notes.append(
                f"budget warning: {budget_summary.cost_pct:.0f}% cost, "
                f"{budget_summary.time_pct:.0f}% time"
            )
        if budget_summary.status == "exhausted":
            notes.append("budget EXHAUSTED")
        if self._period_escalations > 0:
            notes.append(f"{self._period_escalations} escalation(s) this period")
        if any(not w.healthy for w in workers):
            unhealthy = [str(w.worker_id) for w in workers if not w.healthy]
            notes.append(f"unhealthy workers: {', '.join(unhealthy)}")

        report = ProgressReport(
            session_id=self._session_id,
            timestamp=time.time(),
            period_seconds=round(period, 1),
            goals_active=goals_active,
            goals_done=goals_done,
            goals_failed=goals_failed,
            tasks_completed=self._period_tasks_completed,
            tasks_failed=self._period_tasks_failed,
            budget=budget_summary,
            workers=workers,
            escalations=self._period_escalations,
            triggers_fired=self._period_triggers,
            throughput_tasks_per_hour=round(throughput, 2),
            notes=notes,
        )

        # Reset period counters
        self._period_tasks_completed = 0
        self._period_tasks_failed = 0
        self._period_escalations = 0
        self._period_triggers = 0
        self._last_report_time = now

        return report

    # ------------------------------------------------------------------
    # Totals
    # ------------------------------------------------------------------

    @property
    def total_tasks_completed(self) -> int:
        return self._total_tasks_completed

    @property
    def total_tasks_failed(self) -> int:
        return self._total_tasks_failed

    @property
    def total_escalations(self) -> int:
        return self._total_escalations

    @property
    def total_triggers_fired(self) -> int:
        return self._total_triggers

    def lifetime_stats(self) -> dict[str, Any]:
        elapsed = time.monotonic() - self._start_time
        return {
            "session_id": self._session_id,
            "elapsed_seconds": round(elapsed, 1),
            "total_tasks_completed": self._total_tasks_completed,
            "total_tasks_failed": self._total_tasks_failed,
            "total_escalations": self._total_escalations,
            "total_triggers_fired": self._total_triggers,
        }
