"""Tests for Tier 4 (Production Hardening):
- bauer/checkpoint.py  (CheckpointManager + RecoveryManager)
- bauer/observability.py (Counter, Gauge, Histogram, MetricsRegistry)
- bauer/audit_trail.py (AuditTrail)
"""

from __future__ import annotations

import time

import pytest

from bauer.checkpoint import Checkpoint, CheckpointManager, RecoveryManager, RecoveryResult
from bauer.observability import (
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
    make_daemon_metrics,
)
from bauer.audit_trail import AuditEvent, AuditTrail


# ===========================================================================
# CheckpointManager
# ===========================================================================


class TestCheckpointManager:
    def _mgr(self, session_id: str = "sess_test") -> CheckpointManager:
        return CheckpointManager(db_path=":memory:", session_id=session_id)

    # ── save / latest ────────────────────────────────────────────────────────

    def test_save_returns_id(self):
        m = self._mgr()
        cp_id = m.save()
        assert cp_id.startswith("cp_")

    def test_latest_returns_most_recent(self):
        m = self._mgr()
        m.save(goals=["goal_1"])
        time.sleep(0.01)
        m.save(goals=["goal_2"])
        cp = m.latest()
        assert cp is not None
        assert cp.active_goals == ["goal_2"]

    def test_latest_none_when_empty(self):
        m = self._mgr()
        assert m.latest() is None

    def test_save_goals_budget_payload(self):
        m = self._mgr()
        m.save(
            goals=["g1", "g2"],
            budget={"cost_usd": 0.5, "llm_calls": 3},
            payload={"key": "value"},
        )
        cp = m.latest()
        assert cp.active_goals == ["g1", "g2"]
        assert cp.budget["cost_usd"] == 0.5
        assert cp.payload["key"] == "value"

    def test_save_shutdown_reason(self):
        m = self._mgr()
        m.save(shutdown_reason="requested")
        cp = m.latest()
        assert cp.shutdown_reason == "requested"

    # ── mark_shutdown ────────────────────────────────────────────────────────

    def test_mark_shutdown_updates_latest(self):
        m = self._mgr()
        m.save()
        m.mark_shutdown("budget_exhausted")
        cp = m.latest()
        assert cp.shutdown_reason == "budget_exhausted"

    def test_mark_shutdown_no_checkpoint_safe(self):
        m = self._mgr()
        m.mark_shutdown("graceful")  # No checkpoint exists — should not crash

    # ── count / list_all ─────────────────────────────────────────────────────

    def test_count(self):
        m = self._mgr()
        m.save()
        m.save()
        assert m.count() == 2

    def test_list_all(self):
        m = self._mgr()
        m.save(goals=["a"])
        m.save(goals=["b"])
        cps = m.list_all()
        assert len(cps) == 2
        # Newest first
        assert cps[0].active_goals == ["b"]

    def test_delete_all(self):
        m = self._mgr()
        m.save()
        m.save()
        deleted = m.delete_all()
        assert deleted == 2
        assert m.count() == 0

    # ── keep_last_n pruning ──────────────────────────────────────────────────

    def test_pruning_keeps_last_n(self):
        m = CheckpointManager(db_path=":memory:", session_id="s", keep_last_n=3)
        for i in range(6):
            m.save(goals=[f"g{i}"])
        assert m.count() == 3
        cps = m.list_all()
        # Should have g5, g4, g3 (last 3)
        all_goals = [cp.active_goals[0] for cp in cps]
        assert "g5" in all_goals
        assert "g0" not in all_goals

    # ── Checkpoint.to_dict ────────────────────────────────────────────────────

    def test_checkpoint_to_dict(self):
        m = self._mgr()
        m.save(goals=["g1"], budget={"cost": 0.1})
        cp = m.latest()
        d = cp.to_dict()
        assert d["session_id"] == "sess_test"
        assert d["active_goals"] == ["g1"]
        assert d["budget"]["cost"] == 0.1
        assert "id" in d
        assert "created_at" in d

    # ── file-backed DB ────────────────────────────────────────────────────────

    def test_persistent_db(self, tmp_path):
        db = tmp_path / "checkpoints.db"
        m1 = CheckpointManager(db_path=db, session_id="s1")
        m1.save(goals=["persistent_goal"])
        del m1

        m2 = CheckpointManager(db_path=db, session_id="s1")
        cp = m2.latest()
        assert cp is not None
        assert "persistent_goal" in cp.active_goals


# ===========================================================================
# RecoveryManager
# ===========================================================================


class TestRecoveryManager:
    def _setup(self):
        """Create a shared :memory: DB used by both mgr and recovery."""
        # Both share the same :memory: connection — use file-backed for this
        import tempfile, os
        tmpdir = tempfile.mkdtemp()
        db = str(tmpdir) + "/test.db"
        return db

    def test_latest_no_checkpoints(self):
        rm = RecoveryManager(db_path=":memory:")
        result = rm.latest()
        assert isinstance(result, RecoveryResult)
        assert result.interrupted is False
        assert result.checkpoint is None

    def test_latest_interrupted_when_no_shutdown_reason(self, tmp_path):
        db = tmp_path / "cp.db"
        m = CheckpointManager(db_path=db, session_id="s1")
        m.save(goals=["g1"])  # No shutdown reason → interrupted

        rm = RecoveryManager(db_path=db)
        result = rm.latest()
        assert result.interrupted is True
        assert result.active_goals == ["g1"]
        assert result.session_id == "s1"

    def test_latest_graceful_shutdown(self, tmp_path):
        db = tmp_path / "cp.db"
        m = CheckpointManager(db_path=db, session_id="s1")
        m.save()
        m.mark_shutdown("requested")

        rm = RecoveryManager(db_path=db)
        result = rm.latest()
        assert result.interrupted is False

    def test_latest_non_graceful_shutdown(self, tmp_path):
        db = tmp_path / "cp.db"
        m = CheckpointManager(db_path=db, session_id="s1")
        m.save()
        m.mark_shutdown("budget_exhausted")

        rm = RecoveryManager(db_path=db)
        result = rm.latest()
        assert result.interrupted is True

    def test_list_sessions(self, tmp_path):
        db = tmp_path / "cp.db"
        CheckpointManager(db_path=db, session_id="sess_a").save()
        CheckpointManager(db_path=db, session_id="sess_b").save()

        rm = RecoveryManager(db_path=db)
        sessions = rm.list_sessions()
        assert "sess_a" in sessions
        assert "sess_b" in sessions

    def test_recovery_result_properties(self, tmp_path):
        db = tmp_path / "cp.db"
        m = CheckpointManager(db_path=db, session_id="s1")
        m.save(
            goals=["g1"],
            budget={"cost_usd": 0.3},
            payload={"custom": True},
        )

        rm = RecoveryManager(db_path=db)
        result = rm.latest()
        assert result.active_goals == ["g1"]
        assert result.budget["cost_usd"] == 0.3
        assert result.payload["custom"] is True


# ===========================================================================
# Counter
# ===========================================================================


class TestCounter:
    def test_initial_value_zero(self):
        c = Counter("my_counter")
        assert c.value == 0

    def test_inc_by_one(self):
        c = Counter("c")
        c.inc()
        assert c.value == 1

    def test_inc_by_n(self):
        c = Counter("c")
        c.inc(5)
        assert c.value == 5

    def test_inc_negative_raises(self):
        c = Counter("c")
        with pytest.raises(ValueError):
            c.inc(-1)

    def test_reset(self):
        c = Counter("c")
        c.inc(10)
        c.reset()
        assert c.value == 0

    def test_render_contains_total(self):
        c = Counter("requests", "Total HTTP requests")
        c.inc(3)
        text = c.render()
        assert "requests_total 3" in text
        assert "# TYPE requests counter" in text
        assert "# HELP requests Total HTTP requests" in text

    def test_render_with_labels(self):
        c = Counter("errors", labels={"method": "GET", "status": "500"})
        c.inc(2)
        text = c.render()
        assert "errors_total{" in text
        assert 'method="GET"' in text

    def test_inc_zero_allowed(self):
        c = Counter("c")
        c.inc(0)
        assert c.value == 0


# ===========================================================================
# Gauge
# ===========================================================================


class TestGauge:
    def test_initial_value_zero(self):
        g = Gauge("g")
        assert g.value == 0.0

    def test_set(self):
        g = Gauge("g")
        g.set(3.14)
        assert g.value == pytest.approx(3.14)

    def test_inc(self):
        g = Gauge("g")
        g.set(5.0)
        g.inc(2.0)
        assert g.value == pytest.approx(7.0)

    def test_dec(self):
        g = Gauge("g")
        g.set(10.0)
        g.dec(3.0)
        assert g.value == pytest.approx(7.0)

    def test_render(self):
        g = Gauge("workers_active", "Active workers")
        g.set(4)
        text = g.render()
        assert "workers_active 4" in text
        assert "# TYPE workers_active gauge" in text

    def test_render_float(self):
        g = Gauge("cost_usd")
        g.set(0.5)
        text = g.render()
        assert "0.5" in text


# ===========================================================================
# Histogram
# ===========================================================================


class TestHistogram:
    def test_initial_count_zero(self):
        h = Histogram("h", buckets=[1.0, 5.0, 10.0])
        assert h.count == 0
        assert h.sum == 0.0

    def test_observe_updates_count_and_sum(self):
        h = Histogram("h", buckets=[1.0, 5.0])
        h.observe(2.0)
        h.observe(3.0)
        assert h.count == 2
        assert h.sum == pytest.approx(5.0)

    def test_render_contains_buckets(self):
        h = Histogram("latency", buckets=[0.5, 1.0, 2.0])
        h.observe(0.3)
        h.observe(0.7)
        h.observe(1.5)
        text = h.render()
        assert 'le="0.5"' in text
        assert 'le="+Inf"' in text
        assert "latency_sum" in text
        assert "latency_count" in text

    def test_inf_bucket_gets_all_observations(self):
        h = Histogram("h", buckets=[1.0])
        h.observe(0.5)
        h.observe(2.0)  # above all buckets
        text = h.render()
        assert 'le="+Inf"} 2' in text

    def test_render_with_labels(self):
        h = Histogram("rtt", labels={"region": "us"}, buckets=[0.1, 1.0])
        text = h.render()
        assert 'region="us"' in text


# ===========================================================================
# MetricsRegistry
# ===========================================================================


class TestMetricsRegistry:
    def test_counter_registered(self):
        reg = MetricsRegistry(namespace="bauer")
        c = reg.counter("tasks_total", "Tasks")
        assert c is reg.get("tasks_total")

    def test_same_name_returns_same_instance(self):
        reg = MetricsRegistry()
        c1 = reg.counter("c")
        c2 = reg.counter("c")
        assert c1 is c2

    def test_namespace_prefix(self):
        reg = MetricsRegistry(namespace="myapp")
        c = reg.counter("requests")
        assert c.name == "myapp_requests"

    def test_names(self):
        reg = MetricsRegistry(namespace="ns")
        reg.counter("a")
        reg.gauge("b")
        assert len(reg.names()) == 2

    def test_render_all(self):
        reg = MetricsRegistry()
        c = reg.counter("hits")
        g = reg.gauge("workers")
        c.inc(3)
        g.set(2)
        text = reg.render()
        assert "hits_total 3" in text
        assert "workers 2" in text
        assert "# EOF" in text

    def test_render_empty(self):
        reg = MetricsRegistry()
        assert reg.render() == "# EOF\n"

    def test_snapshot(self):
        reg = MetricsRegistry()
        c = reg.counter("hits")
        g = reg.gauge("workers")
        h = reg.histogram("latency", buckets=[0.1, 1.0])
        c.inc(5)
        g.set(3)
        h.observe(0.5)
        snap = reg.snapshot()
        assert snap["hits"] == 5
        assert snap["workers"] == 3.0
        assert snap["latency"]["count"] == 1

    def test_make_daemon_metrics(self):
        m = make_daemon_metrics()
        assert "tasks_completed" in m
        assert "budget_cost_usd" in m
        assert "task_duration_seconds" in m
        # Increment and render
        m["tasks_completed"].inc()
        m["budget_cost_usd"].set(0.25)
        text = m["registry"].render()
        assert "tasks_completed" in text


# ===========================================================================
# AuditTrail
# ===========================================================================


class TestAuditTrail:
    def _trail(self) -> AuditTrail:
        return AuditTrail(db_path=":memory:")

    # ── log / get ─────────────────────────────────────────────────────────────

    def test_log_returns_id(self):
        t = self._trail()
        eid = t.log("tool_call", actor="worker_0", resource="run_command",
                     action="execute", outcome="success")
        assert eid.startswith("audit_")

    def test_get_existing_event(self):
        t = self._trail()
        eid = t.log(
            "tool_call",
            actor="worker_0",
            resource="write_file",
            action="write",
            outcome="success",
            detail={"path": "/tmp/x.txt"},
            duration_ms=12.3,
        )
        ev = t.get(eid)
        assert ev is not None
        assert ev.event_type == "tool_call"
        assert ev.actor == "worker_0"
        assert ev.resource == "write_file"
        assert ev.detail["path"] == "/tmp/x.txt"
        assert ev.duration_ms == pytest.approx(12.3)

    def test_get_nonexistent(self):
        t = self._trail()
        assert t.get("audit_nope") is None

    def test_append_only_no_update(self):
        """Audit trail has no update method — every call creates a new row."""
        t = self._trail()
        t.log("tool_call", actor="a")
        t.log("tool_call", actor="b")
        assert t.count(event_type="tool_call") == 2

    # ── session_id ────────────────────────────────────────────────────────────

    def test_session_id_stored(self):
        t = AuditTrail(db_path=":memory:", session_id="sess_abc")
        eid = t.log("startup")
        ev = t.get(eid)
        assert ev.session_id == "sess_abc"

    def test_session_id_override_per_log(self):
        t = AuditTrail(db_path=":memory:", session_id="default")
        eid = t.log("startup", session_id="override")
        ev = t.get(eid)
        assert ev.session_id == "override"

    # ── query ────────────────────────────────────────────────────────────────

    def test_query_by_event_type(self):
        t = self._trail()
        t.log("tool_call")
        t.log("llm_call")
        t.log("tool_call")
        results = t.query(event_type="tool_call")
        assert len(results) == 2

    def test_query_by_actor(self):
        t = self._trail()
        t.log("tool_call", actor="worker_0")
        t.log("tool_call", actor="worker_1")
        results = t.query(actor="worker_0")
        assert len(results) == 1

    def test_query_by_outcome(self):
        t = self._trail()
        t.log("tool_call", outcome="success")
        t.log("tool_call", outcome="failure")
        results = t.query(outcome="failure")
        assert len(results) == 1

    def test_query_limit_offset(self):
        t = self._trail()
        for i in range(10):
            t.log("tool_call", actor=f"w{i}")
        results = t.query(limit=3)
        assert len(results) == 3

    def test_query_since_until(self):
        t = self._trail()
        now = time.time()
        t.log("startup", created_at=now - 100)
        t.log("tool_call", created_at=now - 10)
        t.log("shutdown", created_at=now)
        results = t.query(since=now - 50)
        assert len(results) == 2  # tool_call + shutdown

    def test_query_returns_newest_first(self):
        t = self._trail()
        t.log("tool_call", actor="first", created_at=time.time() - 10)
        t.log("tool_call", actor="second", created_at=time.time())
        results = t.query(event_type="tool_call")
        assert results[0].actor == "second"

    # ── count ─────────────────────────────────────────────────────────────────

    def test_count_total(self):
        t = self._trail()
        t.log("a")
        t.log("b")
        t.log("c")
        assert t.count() == 3

    def test_count_by_event_type(self):
        t = self._trail()
        t.log("tool_call")
        t.log("tool_call")
        t.log("llm_call")
        assert t.count(event_type="tool_call") == 2
        assert t.count(event_type="llm_call") == 1

    def test_count_by_outcome(self):
        t = self._trail()
        t.log("x", outcome="success")
        t.log("x", outcome="failure")
        t.log("x", outcome="failure")
        assert t.count(outcome="failure") == 2

    # ── stats ─────────────────────────────────────────────────────────────────

    def test_stats_structure(self):
        t = self._trail()
        t.log("tool_call", outcome="success")
        t.log("llm_call", outcome="failure")
        s = t.stats()
        assert s["total"] == 2
        assert "by_type" in s
        assert "by_outcome" in s
        assert s["by_type"]["tool_call"] == 1

    # ── purge_before ──────────────────────────────────────────────────────────

    def test_purge_before(self):
        t = self._trail()
        now = time.time()
        t.log("old_event", created_at=now - 200)
        t.log("new_event", created_at=now)
        deleted = t.purge_before(now - 100)
        assert deleted == 1
        assert t.count() == 1

    # ── event.to_dict ─────────────────────────────────────────────────────────

    def test_to_dict(self):
        t = self._trail()
        eid = t.log("tool_call", actor="w0", detail={"cmd": "pytest"})
        ev = t.get(eid)
        d = ev.to_dict()
        assert d["event_type"] == "tool_call"
        assert d["actor"] == "w0"
        assert d["detail"]["cmd"] == "pytest"

    # ── file-backed DB ────────────────────────────────────────────────────────

    def test_persistent_db(self, tmp_path):
        db = tmp_path / "audit.db"
        t1 = AuditTrail(db_path=db, session_id="s1")
        eid = t1.log("startup")
        del t1

        t2 = AuditTrail(db_path=db)
        ev = t2.get(eid)
        assert ev is not None
        assert ev.event_type == "startup"
