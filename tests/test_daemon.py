"""Tests for bauer/daemon.py and bauer/process_supervisor.py."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bauer.daemon import (
    BauerDaemon,
    DaemonConfig,
    DaemonStateDB,
    get_daemon_pid,
    is_daemon_alive,
)
from bauer.process_supervisor import ProcessSupervisor


# ===========================================================================
# ProcessSupervisor
# ===========================================================================


class TestProcessSupervisor:
    def test_initial_healthy(self):
        s = ProcessSupervisor(worker_id=1, max_restarts=5)
        assert s.is_healthy
        assert s.consecutive_failures == 0
        assert s.total_failures == 0

    def test_should_restart_records_failure(self):
        s = ProcessSupervisor(max_restarts=3)
        ok = s.should_restart(ValueError("boom"))
        assert ok is True
        assert s.consecutive_failures == 1
        assert s.total_failures == 1

    def test_should_restart_false_after_max(self):
        s = ProcessSupervisor(max_restarts=2)
        s.should_restart(ValueError("1"))
        s.should_restart(ValueError("2"))
        ok = s.should_restart(ValueError("3"))  # 3rd failure > max_restarts=2
        assert ok is False

    def test_record_failure_explicit(self):
        s = ProcessSupervisor(max_restarts=5)
        s.record_failure(RuntimeError("x"))
        assert s.consecutive_failures == 1

    def test_reset_clears_counters(self):
        s = ProcessSupervisor(max_restarts=5)
        s.record_failure(ValueError("a"))
        s.record_failure(ValueError("b"))
        s.reset()
        assert s.consecutive_failures == 0
        assert s.total_failures == 0
        assert s.is_healthy

    def test_reset_after_stability(self):
        """If worker was stable long enough, consecutive counter resets."""
        s = ProcessSupervisor(max_restarts=3, reset_after_success_seconds=1.0)
        s.record_failure(ValueError("first"))
        assert s.consecutive_failures == 1
        # Simulate worker running for > reset_after_success_seconds
        s._last_success_start = time.monotonic() - 2.0  # 2s ago
        # Second failure should reset consecutive to 0 then increment to 1
        s.record_failure(ValueError("second after stability"))
        assert s.consecutive_failures == 1  # reset + 1, not 2

    def test_backoff_seconds_for(self):
        s = ProcessSupervisor(backoff_base=10.0, backoff_cap=300.0)
        assert s.backoff_seconds_for(1) == 10.0
        assert s.backoff_seconds_for(2) == 20.0
        assert s.backoff_seconds_for(3) == 40.0

    def test_backoff_cap_respected(self):
        s = ProcessSupervisor(backoff_base=10.0, backoff_cap=30.0)
        # 10 * 2^(10-1) = 5120, should be capped at 30
        assert s.backoff_seconds_for(10) == 30.0

    @pytest.mark.asyncio
    async def test_wait_backoff_does_not_raise(self):
        s = ProcessSupervisor(max_restarts=5, backoff_base=0.001, backoff_cap=0.01)
        s.record_failure(ValueError("oops"))
        # Should complete quickly with tiny backoff
        await asyncio.wait_for(s.wait_backoff(), timeout=1.0)

    def test_stats_returns_dict(self):
        s = ProcessSupervisor(worker_id=42, max_restarts=5)
        d = s.stats()
        assert d["worker_id"] == 42
        assert "consecutive_failures" in d
        assert "is_healthy" in d


# ===========================================================================
# DaemonStateDB
# ===========================================================================


class TestDaemonStateDB:
    def test_create_and_upsert(self, tmp_path):
        db = DaemonStateDB(tmp_path / "daemon_state.db")
        db.upsert("sess_1", pid=1234, board="default", workers=2)
        rows = db.get_running()
        assert len(rows) == 1
        assert rows[0]["id"] == "sess_1"
        assert rows[0]["status"] == "running"

    def test_heartbeat_updates_timestamp(self, tmp_path):
        db = DaemonStateDB(tmp_path / "ds.db")
        db.upsert("sess_2", pid=1234, board="default", workers=1)
        t0 = db.get_running()[0]["last_heartbeat"]
        time.sleep(0.01)
        db.heartbeat("sess_2")
        t1 = db.get_running()[0]["last_heartbeat"]
        assert t1 > t0

    def test_mark_stopped(self, tmp_path):
        db = DaemonStateDB(tmp_path / "ds.db")
        db.upsert("sess_3", pid=9999, board="default", workers=2)
        db.mark_stopped("sess_3", reason="test_shutdown")
        running = db.get_running()
        assert len(running) == 0
        latest = db.get_latest()
        assert latest["status"] == "stopped"
        assert latest["shutdown_reason"] == "test_shutdown"

    def test_get_latest_none_on_empty(self, tmp_path):
        db = DaemonStateDB(tmp_path / "ds.db")
        assert db.get_latest() is None

    def test_upsert_updates_existing(self, tmp_path):
        db = DaemonStateDB(tmp_path / "ds.db")
        db.upsert("sess_4", pid=1, board="a", workers=1, status="running")
        db.upsert("sess_4", pid=1, board="a", workers=1, status="running",
                  shutdown_reason="changed")
        rows = db.get_running()
        assert len(rows) == 1  # no duplicate


# ===========================================================================
# DaemonConfig
# ===========================================================================


class TestDaemonConfig:
    def test_default_values(self):
        cfg = DaemonConfig()
        assert cfg.board == "default"
        assert cfg.workers == 2
        assert cfg.max_cost_usd == 5.0

    def test_get_state_dir_default(self, monkeypatch: pytest.MonkeyPatch):
        # "default" = sem BAUER_HOME no ambiente (o conftest.py global seta
        # BAUER_HOME p/ tmp por hermeticidade — remove aqui pra testar o
        # fallback real ~/.bauer).
        monkeypatch.delenv("BAUER_HOME", raising=False)
        cfg = DaemonConfig()
        state_dir = cfg.get_state_dir()
        assert state_dir.name == "daemon"
        assert ".bauer" in str(state_dir)

    def test_get_state_dir_override(self, tmp_path):
        cfg = DaemonConfig(state_dir=tmp_path)
        assert cfg.get_state_dir() == tmp_path

    def test_get_state_dir_bauer_home_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BAUER_HOME", str(tmp_path))
        cfg = DaemonConfig()
        assert cfg.get_state_dir() == tmp_path / "daemon"


# ===========================================================================
# BauerDaemon — unit tests (no actual task execution)
# ===========================================================================


class TestBauerDaemon:
    def _make_daemon(self, tmp_path, **kw) -> BauerDaemon:
        cfg = DaemonConfig(
            board="test",
            workers=1,
            max_cost_usd=1.0,
            max_wall_seconds=60,
            max_llm_calls=5,
            max_tool_calls=10,
            poll_interval_seconds=0.05,
            heartbeat_interval_seconds=0.1,
            diagnostics_interval_seconds=0.2,
            budget_check_interval_seconds=0.05,
            state_dir=tmp_path / "daemon",
            # Very short backoff so tests don't wait 10+ seconds per restart
            supervisor_max_restarts=2,
            supervisor_backoff_base=0.01,
            supervisor_backoff_cap=0.05,
            **kw,
        )
        return BauerDaemon(cfg)

    def test_initial_not_running(self, tmp_path):
        d = self._make_daemon(tmp_path)
        # Before start(), shutdown event is set or we check via is_running
        # The event is created but not set — daemon is "ready to run"
        assert not d._shutdown_event.is_set()

    def test_request_shutdown_sets_event(self, tmp_path):
        d = self._make_daemon(tmp_path)
        d._setup_components()
        d.request_shutdown("test")
        assert d._shutdown_event.is_set()
        assert d._shutdown_reason == "test"

    def test_stats_structure(self, tmp_path):
        d = self._make_daemon(tmp_path)
        d._setup_components()
        s = d.stats()
        assert "session_id" in s
        assert "budget" in s
        assert s["board"] == "test"

    @pytest.mark.asyncio
    async def test_start_shuts_down_when_budget_exhausted(self, tmp_path):
        """Daemon should exit quickly if budget is already exhausted at start."""
        d = self._make_daemon(tmp_path)
        d._setup_components()

        # Exhaust the budget immediately
        d._budget._llm_calls = d._budget.max_llm_calls  # exhaust

        escalation_calls = []

        async def _on_escalation(reason, ctx):
            escalation_calls.append(reason)

        d._on_escalation = _on_escalation

        # Patch _claim_next_task to return None (no tasks)
        with patch.object(d, "_claim_next_task", new=AsyncMock(return_value=None)):
            exit_code = await asyncio.wait_for(d.start(), timeout=3.0)

        # budget_watchdog should have triggered shutdown
        assert d._shutdown_event.is_set()
        assert "budget_exhausted" in d._shutdown_reason

    @pytest.mark.asyncio
    async def test_start_processes_task_stub(self, tmp_path):
        """Workers claim and execute a stubbed task."""
        d = self._make_daemon(tmp_path)
        d._setup_components()

        # Fake one ready task then return None (no more)
        calls = [0]

        async def fake_claim(worker_id):
            if calls[0] == 0:
                calls[0] += 1
                return ("t_001", "Test task")
            # After one task, signal shutdown
            d.request_shutdown("done")
            return None

        with patch.object(d, "_claim_next_task", side_effect=fake_claim):
            with patch.object(d, "_run_task_via_dispatcher", new=AsyncMock()):
                exit_code = await asyncio.wait_for(d.start(), timeout=5.0)

        assert d._tasks_completed >= 1

    @pytest.mark.asyncio
    async def test_worker_crash_retries_and_shuts_down(self, tmp_path):
        """Worker crashes max_restarts+1 times → daemon shuts down."""
        # supervisor_max_restarts=2, backoff_base=0.01 → total wait < 0.5s
        d = self._make_daemon(tmp_path)
        d._setup_components()

        crash_count = [0]

        async def crash_claim(worker_id):
            crash_count[0] += 1
            raise RuntimeError("simulated crash")

        with patch.object(d, "_claim_next_task", side_effect=crash_claim):
            exit_code = await asyncio.wait_for(d.start(), timeout=5.0)

        assert exit_code == 1  # crash exit
        assert "exceeded_restarts" in d._shutdown_reason

    @pytest.mark.asyncio
    async def test_escalation_callback_called(self, tmp_path):
        """on_escalation is called when budget is exhausted."""
        escalations = []

        async def on_esc(reason, ctx):
            escalations.append(reason)

        d = self._make_daemon(tmp_path)
        d._on_escalation = on_esc
        d._setup_components()

        # Force budget exhaustion
        d._budget._cost_usd = d._budget.max_cost_usd

        with patch.object(d, "_claim_next_task", new=AsyncMock(return_value=None)):
            await asyncio.wait_for(d.start(), timeout=3.0)

        assert "budget_exhausted" in escalations


# ===========================================================================
# get_daemon_pid / is_daemon_alive
# ===========================================================================


class TestDaemonPidHelpers:
    def test_get_pid_returns_none_if_no_file(self, tmp_path):
        assert get_daemon_pid(tmp_path) is None

    def test_get_pid_reads_pid_file(self, tmp_path):
        (tmp_path / "daemon.pid").write_text("12345")
        assert get_daemon_pid(tmp_path) == 12345

    def test_get_pid_returns_none_on_bad_content(self, tmp_path):
        (tmp_path / "daemon.pid").write_text("not_a_number")
        assert get_daemon_pid(tmp_path) is None

    def test_is_alive_false_if_no_pid(self, tmp_path):
        assert not is_daemon_alive(tmp_path)

    def test_is_alive_true_for_current_process(self, tmp_path):
        """Writing own PID → process exists → should return True."""
        (tmp_path / "daemon.pid").write_text(str(os.getpid()))
        assert is_daemon_alive(tmp_path)

    def test_is_alive_false_for_nonexistent_pid(self, tmp_path):
        # PID 9999999 very unlikely to exist
        (tmp_path / "daemon.pid").write_text("9999999")
        assert not is_daemon_alive(tmp_path)
