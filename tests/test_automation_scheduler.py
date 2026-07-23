"""Tests for durable automation/cron scheduler."""

from __future__ import annotations

from pathlib import Path

from bauer.automation_scheduler import AutomationScheduler
from bauer.automation_store import AutomationStore, next_run_after, parse_schedule
from bauer.workspace_manager_factory import get_workspace_manager


def test_parse_schedule_interval_daily_once_and_cron():
    interval = parse_schedule("every 30m")
    daily = parse_schedule("daily 08:30")
    once = parse_schedule("at 2026-06-01T10:00:00+00:00")
    cron = parse_schedule("cron: */15 * * * *")

    assert interval["seconds"] == 1800
    assert daily["hour"] == 8
    assert daily["minute"] == 30
    assert once["type"] == "once"
    assert cron["minute"] == "*/15"


def test_next_run_after_daily_rolls_to_next_day():
    schedule = parse_schedule("daily 08:30")

    assert next_run_after(schedule, after="2026-06-01T08:31:00+00:00") == "2026-06-02T08:30:00+00:00"


def test_tick_enqueues_due_job_as_ready_task(tmp_path: Path):
    workspace = tmp_path / "workspace"
    get_workspace_manager(workspace).init_project("Automation Test")
    store = AutomationStore(workspace)
    job = store.create_job(
        name="daily-report",
        prompt="Generate the daily report",
        schedule="every 1h",
        next_run_at="2026-06-01T09:00:00+00:00",
        metadata={"assignee": "ops", "priority": "high", "max_retries": 3},
    )

    result = AutomationScheduler(workspace).tick(now="2026-06-01T09:00:00+00:00")

    tasks = get_workspace_manager(workspace).list_tasks()
    runs = store.list_runs(job_id=job.job_id)
    updated = store.get_job(job.job_id)
    assert result.due == ["daily-report"]
    assert result.queued == ["001"]
    assert tasks[0].status == "READY"
    assert tasks[0].metadata["automation_job"] == job.job_id
    assert tasks[0].metadata["automation_run"] == runs[0].run_id
    assert tasks[0].metadata["dispatch"] == "true"
    assert tasks[0].assignee == "ops"
    assert tasks[0].priority == "high"
    assert runs[0].task_id == "001"
    assert updated is not None
    assert updated.run_count == 1
    assert updated.next_run_at == "2026-06-01T10:00:00+00:00"


def test_tick_skips_paused_jobs(tmp_path: Path):
    workspace = tmp_path / "workspace"
    store = AutomationStore(workspace)
    job = store.create_job(
        name="paused-job",
        prompt="Should not run",
        schedule="every 1h",
        next_run_at="2026-06-01T09:00:00+00:00",
    )
    store.update_job(job.job_id, status="paused")

    result = AutomationScheduler(workspace).tick(now="2026-06-01T09:00:00+00:00")

    assert result.due == []
    assert get_workspace_manager(workspace).list_tasks() == []


def test_once_job_completes_after_queue(tmp_path: Path):
    workspace = tmp_path / "workspace"
    store = AutomationStore(workspace)
    job = store.create_job(
        name="one-shot",
        prompt="Run once",
        schedule="at 2026-06-01T09:00:00+00:00",
    )

    AutomationScheduler(workspace).tick(now="2026-06-01T09:00:00+00:00")

    updated = store.get_job(job.job_id)
    assert updated is not None
    assert updated.status == "completed"
    assert updated.next_run_at == ""


def test_manual_run_enqueues_even_when_not_due(tmp_path: Path):
    workspace = tmp_path / "workspace"
    store = AutomationStore(workspace)
    job = store.create_job(
        name="manual",
        prompt="Run manually",
        schedule="every 1d",
        next_run_at="2026-06-02T09:00:00+00:00",
    )

    run = AutomationScheduler(workspace).run_now("manual", due_at="2026-06-01T09:00:00+00:00")

    task = get_workspace_manager(workspace).get_task(run.task_id)
    updated = store.get_job(job.job_id)
    assert task.status == "READY"
    assert task.metadata["automation_name"] == "manual"
    assert updated is not None
    assert updated.run_count == 1
