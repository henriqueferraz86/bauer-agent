from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import asdict
from pathlib import Path

from bauer.core.events import EventBus
from bauer.core.policy.engine import PolicyEngine
from bauer.core.runtime.autonomy import BudgetManager
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.resilience import RuntimeControl, RuntimeRecovery, WorkerRegistry
from bauer.core.runtime.scheduler import Scheduler, next_run_after
from bauer.core.runtime.state_store import JsonlStateStore


class FakeAdapter:
    name = "fake"

    def run_agent(self, request: dict):
        return {
            "status": "completed",
            "run_id": request["run_id"],
            "runtime_adapter": self.name,
            "output": f"ok:{request['input']['message']}",
            "cost_estimate": 0.25,
            "tool_calls_count": 1,
        }


class FailingAdapter:
    name = "fake"

    def run_agent(self, request: dict):
        raise RuntimeError("adapter down")


class FlakyAdapter:
    def __init__(self):
        self.calls = 0

    def run_agent(self, request: dict):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary failure")
        return {"status": "completed", "run_id": request["run_id"], "output": "ok"}


def _task(next_run_at: str | None = None) -> dict:
    return {
        "id": "daily_project_review",
        "name": "Revisao diaria dos projetos",
        "agent_id": "productivity",
        "runtime_adapter": "fake",
        "schedule": {"type": "cron", "expression": "0 9 * * *"},
        "input": {"message": "Revise o Kanban e gere plano do dia."},
        "policy": {"max_cost_usd": 0.50, "max_runtime_s": 300, "approval_required": False},
        "next_run_at": next_run_at,
    }


def test_next_run_after_cron_expression():
    assert next_run_after(
        {"type": "cron", "expression": "0 9 * * *"},
        after="2026-07-08T08:59:00+00:00",
    ) == "2026-07-08T09:00:00+00:00"
    assert next_run_after(
        {"type": "cron", "expression": "0 9 * * *"},
        after="2026-07-08T09:01:00+00:00",
    ) == "2026-07-09T09:00:00+00:00"


def test_scheduler_persists_tasks_across_instances(tmp_path: Path):
    root = tmp_path / "runtime"
    scheduler = Scheduler(root=root, adapter_factory=lambda _name: FakeAdapter())
    task = scheduler.add_task(_task())

    reloaded = Scheduler(root=root, adapter_factory=lambda _name: FakeAdapter()).get_task(task.id)

    assert reloaded is not None
    assert reloaded.id == "daily_project_review"
    assert reloaded.next_run_at is not None
    assert reloaded.policy["max_cost_usd"] == 0.5


def test_worker_tick_runs_due_task_and_records_events(tmp_path: Path):
    root = tmp_path / "runtime"
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    due_at = (now - timedelta(minutes=1)).isoformat()
    scheduler = Scheduler(root=root, adapter_factory=lambda _name: FakeAdapter())
    scheduler.add_task(_task(next_run_at=due_at))

    results = scheduler.tick(now=now)

    assert results[0]["status"] == "completed"
    runs = RunManager(root=root).list_runs()
    assert len(runs) == 1
    assert runs[0].status == "completed"
    events = EventBus(root=root).list_events(run_id=runs[0].id)
    assert "schedule.triggered" in {event.event_type for event in events}
    assert {"run.created", "run.started", "run.completed"} <= {event.event_type for event in events}


def test_worker_records_failure_without_raising(tmp_path: Path):
    root = tmp_path / "runtime"
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    scheduler = Scheduler(root=root, adapter_factory=lambda _name: FailingAdapter())
    scheduler.add_task(_task(next_run_at=(now - timedelta(minutes=1)).isoformat()))

    results = scheduler.tick(now=now)

    assert results[0]["status"] == "failed"
    runs = RunManager(root=root).list_runs()
    assert runs[0].status == "failed"
    events = EventBus(root=root).list_events(run_id=runs[0].id)
    assert "schedule.failed" in {event.event_type for event in events}


def test_retry_policy_retries_failed_attempt(tmp_path: Path):
    root = tmp_path / "runtime"
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    adapter = FlakyAdapter()
    scheduler = Scheduler(root=root, adapter_factory=lambda _name: adapter)
    scheduler.add_task(
        {
            **_task(next_run_at=now.isoformat()),
            "policy": {"retry_count": 1, "retry_backoff": 0},
        }
    )

    result = scheduler.tick(now=now)[0]

    assert result["status"] == "completed"
    assert result["attempts"] == 2
    assert adapter.calls == 2


def test_kill_switch_blocks_new_execution(tmp_path: Path):
    root = tmp_path / "runtime"
    RuntimeControl(root=root).set_kill_switch(True)
    scheduler = Scheduler(root=root, adapter_factory=lambda _name: FakeAdapter())
    scheduler.add_task(_task(next_run_at=datetime(2026, 7, 8, 9, 0, tzinfo=UTC).isoformat()))

    result = scheduler.run_task("daily_project_review")

    assert result == {"task_id": "daily_project_review", "status": "blocked", "reason": "kill_switch"}
    assert RunManager(root=root).list_runs() == []


def test_budget_blocks_execution_when_daily_limit_exceeded(tmp_path: Path):
    root = tmp_path / "runtime"
    budget = BudgetManager(root=root)
    budget.set_profile(daily_budget_usd=0.10)
    budget.record_run_cost(run_id="old-run", agent_id="productivity", cost_usd=0.10)
    scheduler = Scheduler(root=root, adapter_factory=lambda _name: FakeAdapter())
    scheduler.add_task({**_task(), "policy": {"estimated_cost_usd": 0.01}})

    result = scheduler.run_task("daily_project_review")

    assert result["status"] == "blocked"
    assert result["reason"] == "budget"
    assert RunManager(root=root).list_runs() == []
    events = EventBus(root=root).list_events()
    assert "budget.exceeded" in {event.event_type for event in events}


def test_autonomy_locked_blocks_execution(tmp_path: Path):
    root = tmp_path / "runtime"
    BudgetManager(root=root).set_profile(mode="locked")
    scheduler = Scheduler(root=root, adapter_factory=lambda _name: FakeAdapter())
    scheduler.add_task(_task())

    result = scheduler.run_task("daily_project_review")

    assert result["status"] == "blocked"
    assert "locked" in result["error"]


def test_completed_run_records_cost_estimate_and_budget_ledger(tmp_path: Path):
    root = tmp_path / "runtime"
    scheduler = Scheduler(root=root, adapter_factory=lambda _name: FakeAdapter())
    scheduler.add_task(_task(next_run_at=datetime(2026, 7, 8, 9, 0, tzinfo=UTC).isoformat()))

    result = scheduler.run_task("daily_project_review")

    run = RunManager(root=root).get_run(result["run_id"])
    assert run is not None
    assert run.cost_estimate == 0.25
    assert run.tool_calls_count == 1
    assert BudgetManager(root=root).status()["daily"]["used_usd"] == 0.25


def test_policy_engine_denies_runtime_execute_over_budget(tmp_path: Path):
    root = tmp_path / "runtime"
    manager = BudgetManager(root=root)
    manager.set_profile(daily_budget_usd=0.10)
    manager.record_run_cost(run_id="old-run", agent_id="agent-1", cost_usd=0.10)

    decision = PolicyEngine(workspace=tmp_path, runtime_root=root).evaluate(
        "runtime.execute",
        {"agent_id": "agent-1", "estimated_cost_usd": 0.01},
    )

    assert decision.action == "deny"
    assert decision.matched_rules == ["budget.exceeded"]


def test_worker_dead_appears_offline(tmp_path: Path):
    root = tmp_path / "runtime"
    store = JsonlStateStore(root)
    registry = WorkerRegistry(store=store)
    worker = registry.heartbeat("worker-1")
    old = asdict(worker)
    old["last_seen_at"] = "2000-01-01T00:00:00+00:00"
    store.upsert("workers", old)

    workers = registry.list(stale_after_s=1)

    assert workers[0]["id"] == "worker-1"
    assert workers[0]["computed_status"] == "offline"


def test_recovery_marks_stuck_run_failed(tmp_path: Path):
    root = tmp_path / "runtime"
    store = JsonlStateStore(root)
    manager = RunManager(store=store)
    run = manager.create_run(session_id="s1", agent_id="a1", status="running")
    old = asdict(run)
    old["updated_at"] = "2000-01-01T00:00:00+00:00"
    old["started_at"] = "2000-01-01T00:00:00+00:00"
    store.upsert("runs", old)

    recovered = RuntimeRecovery(store=store).recover_stuck_runs(max_age_s=1)

    assert recovered == [
        {
            "run_id": run.id,
            "status": "failed",
            "error": "runtime recovery: run stuck for more than 1s",
        }
    ]
    assert manager.get_run(run.id).status == "failed"  # type: ignore[union-attr]


def test_recovery_preserves_waiting_approval_and_paused(tmp_path: Path):
    """Regressão: um run velho em waiting_approval/paused está esperando um
    humano de propósito — o recovery NÃO deve matá-lo por idade (senão
    descarta a aprovação pendente e quebra o approve()/resume() posterior)."""
    root = tmp_path / "runtime"
    store = JsonlStateStore(root)
    manager = RunManager(store=store)

    for waiting_status in ("waiting_approval", "paused"):
        run = manager.create_run(session_id="s1", agent_id="a1", status=waiting_status)
        old = asdict(run)
        old["updated_at"] = "2000-01-01T00:00:00+00:00"
        old["started_at"] = "2000-01-01T00:00:00+00:00"
        store.upsert("runs", old)

    recovered = RuntimeRecovery(store=store).recover_stuck_runs(max_age_s=1)

    assert recovered == []  # nenhum run de espera intencional foi tocado
    for run_id in [r.id for r in manager.list_runs()]:
        assert manager.get_run(run_id).status in ("waiting_approval", "paused")  # type: ignore[union-attr]


def test_scheduler_pause_resume_delete(tmp_path: Path):
    scheduler = Scheduler(root=tmp_path / "runtime", adapter_factory=lambda _name: FakeAdapter())
    scheduler.add_task(_task())

    assert scheduler.pause_task("daily_project_review").status == "paused"
    assert scheduler.resume_task("daily_project_review").status == "active"
    assert scheduler.delete_task("daily_project_review").status == "deleted"
    assert scheduler.get_task("daily_project_review") is None


def test_once_task_completes_after_run(tmp_path: Path):
    now = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)
    scheduler = Scheduler(root=tmp_path / "runtime", adapter_factory=lambda _name: FakeAdapter())
    scheduler.add_task(
        {
            **_task(next_run_at=now.isoformat()),
            "id": "once_review",
            "schedule": {"type": "once", "at": now.isoformat()},
        }
    )

    scheduler.tick(now=now)

    task = scheduler.get_task("once_review")
    assert task is not None
    assert task.status == "completed"
    assert scheduler.tick(now=now) == []
