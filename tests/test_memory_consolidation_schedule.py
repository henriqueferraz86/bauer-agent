"""Sprint 67 (plano 065): consolidação agendada, opt-in e governada pelo Kernel."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from bauer.automation_scheduler import AutomationScheduler
from bauer.automation_store import AutomationStore
from bauer.core.runtime.resilience import RuntimeControl
from bauer.memory_consolidation import (
    CONSOLIDATED_FILE,
    ConsolidationLedger,
    ConsolidationSettings,
)
from bauer.memory_consolidation_schedule import (
    KILL_SWITCH_ENV,
    backoff_seconds,
    run_scheduled_consolidation,
    runtime_root_for,
    schedule_consolidation,
)
from bauer.memory_scope import resolve_workspace_scope
from bauer.sqlite_session_store import SqliteSessionStore

GOOD = json.dumps([{"kind": "fact", "text": "Deploy roda toda sexta.", "confidence": 0.8}])


class FakeLLM:
    def __init__(self, response: str | Exception = GOOD) -> None:
        self.response = response
        self.calls = 0

    def __call__(self, messages):
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def ws(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sessions = tmp_path / "state" / "sessions"
    SqliteSessionStore(sessions, semantic_indexing_enabled=False, workspace=workspace).save(
        "s1", [{"role": "user", "content": "quando é o deploy?"},
               {"role": "assistant", "content": "Toda sexta."}],
    )
    return workspace, sessions


def _enable(workspace: Path, **changes) -> None:
    scope = resolve_workspace_scope(workspace)
    assert scope is not None
    settings = ConsolidationSettings.load(scope)
    settings.enabled = True
    settings.min_session_age_hours = 0
    for key, value in changes.items():
        setattr(settings, key, value)
    settings.save(scope)


def test_scheduled_run_is_governed_and_writes_memory(ws):
    workspace, sessions = ws
    _enable(workspace)
    llm = FakeLLM()
    result = run_scheduled_consolidation(workspace, sessions_dir=sessions, llm=llm)
    assert result["status"] == "completed", result
    assert result["governed"] is True
    assert result["kernel_run_id"]
    assert result["sessions_ok"] == 1
    assert "Deploy roda toda sexta." in (workspace / "memory" / CONSOLIDATED_FILE).read_text(encoding="utf-8")
    scope = resolve_workspace_scope(workspace)
    assert scope is not None
    runs = ConsolidationLedger(scope).recent_runs()
    assert runs[0]["trigger"] == "scheduled"
    assert runs[0]["tokens_used"] > 0


def test_runtime_kill_switch_blocks_before_any_llm_call(ws):
    workspace, sessions = ws
    _enable(workspace)
    RuntimeControl(root=runtime_root_for(sessions)).set_kill_switch(True)
    llm = FakeLLM()
    result = run_scheduled_consolidation(workspace, sessions_dir=sessions, llm=llm)
    assert result["status"] == "skipped"
    assert result["reason"] == "governance"
    assert llm.calls == 0
    assert not (workspace / "memory" / CONSOLIDATED_FILE).exists()


def test_feature_kill_switch_env(ws, monkeypatch):
    workspace, sessions = ws
    _enable(workspace)
    monkeypatch.setenv(KILL_SWITCH_ENV, "1")
    llm = FakeLLM()
    result = run_scheduled_consolidation(workspace, sessions_dir=sessions, llm=llm)
    assert result == {"status": "skipped", "reason": "kill_switch_env"}
    assert llm.calls == 0


def test_scheduled_run_requires_opt_in(ws):
    workspace, sessions = ws
    llm = FakeLLM()
    result = run_scheduled_consolidation(workspace, sessions_dir=sessions, llm=llm)
    assert result == {"status": "skipped", "reason": "disabled"}
    assert llm.calls == 0


def test_daily_token_budget_is_enforced(ws):
    workspace, sessions = ws
    _enable(workspace, daily_token_budget=1)
    scope = resolve_workspace_scope(workspace)
    assert scope is not None
    ledger = ConsolidationLedger(scope)
    run_id = ledger.start_run(trigger="scheduled")
    ledger.finish_run(run_id, status="completed", sessions_ok=0, sessions_failed=0,
                      records_written=0, tokens_used=5)
    llm = FakeLLM()
    result = run_scheduled_consolidation(workspace, sessions_dir=sessions, llm=llm)
    assert result["status"] == "skipped" and result["reason"] == "daily_budget"
    assert llm.calls == 0


def test_daily_budget_limits_the_run(ws):
    workspace, sessions = ws
    _enable(workspace, daily_token_budget=3)
    llm = FakeLLM()
    result = run_scheduled_consolidation(workspace, sessions_dir=sessions, llm=llm)
    assert result["status"] == "partial"
    assert result["budget_exhausted"] is True
    assert llm.calls == 0


def test_backoff_grows_and_caps():
    assert backoff_seconds(0) == 0
    assert backoff_seconds(1) == 900
    assert backoff_seconds(2) == 1800
    assert backoff_seconds(50) == 6 * 3600


def test_cron_tick_runs_job_inline_without_kanban_task(ws, monkeypatch):
    workspace, sessions = ws
    _enable(workspace)
    llm = FakeLLM()
    monkeypatch.setattr("bauer.memory_consolidation.default_llm", lambda cfg=None: llm)
    job = schedule_consolidation(workspace, sessions_dir=sessions, schedule="every 6h")
    store = AutomationStore(workspace)
    store.update_job(job.job_id, next_run_at="2026-06-01T09:00:00+00:00")

    result = AutomationScheduler(workspace).tick(now="2026-06-01T09:00:00+00:00")

    assert result.failed == []
    runs = store.list_runs(job_id=job.job_id)
    assert runs[0].status == "completed"
    assert runs[0].task_id == ""
    assert runs[0].metadata["outcome"]["sessions_ok"] == 1
    assert not (workspace / "TASKS.md").exists()
    updated = store.get_job(job.job_id)
    assert updated is not None
    assert updated.next_run_at == "2026-06-01T15:00:00+00:00"
    assert updated.metadata["consecutive_failures"] == 0
    assert llm.calls == 1


def test_cron_failure_retries_with_backoff_and_succeeds_later(ws, monkeypatch):
    workspace, sessions = ws
    _enable(workspace)
    llm = FakeLLM(RuntimeError("provider fora"))
    monkeypatch.setattr("bauer.memory_consolidation.default_llm", lambda cfg=None: llm)
    job = schedule_consolidation(workspace, sessions_dir=sessions, schedule="every 6h")
    store = AutomationStore(workspace)
    store.update_job(job.job_id, next_run_at="2026-06-01T09:00:00+00:00")
    scheduler = AutomationScheduler(workspace)

    scheduler.tick(now="2026-06-01T09:00:00+00:00")
    failed = store.get_job(job.job_id)
    assert failed is not None
    assert store.list_runs(job_id=job.job_id)[0].status == "failed"
    assert failed.metadata["consecutive_failures"] == 1
    assert failed.next_run_at == datetime.fromisoformat("2026-06-01T09:15:00+00:00").isoformat()

    llm.response = GOOD
    scheduler.tick(now="2026-06-01T09:15:00+00:00")
    recovered = store.get_job(job.job_id)
    assert recovered is not None
    assert recovered.metadata["consecutive_failures"] == 0
    # queued_at tem resolução de segundo: compara o conjunto, não a ordem.
    assert sorted(r.status for r in store.list_runs(job_id=job.job_id)) == ["completed", "failed"]
    scope = resolve_workspace_scope(workspace)
    assert scope is not None
    entry = ConsolidationLedger(scope).entries()["s1"]
    assert entry["status"] == "consolidated" and entry["attempts"] == 2
