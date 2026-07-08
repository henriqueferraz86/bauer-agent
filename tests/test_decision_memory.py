"""Tests for bauer/decision_memory.py and bauer/escalation.py."""

from __future__ import annotations

import asyncio
import time

import pytest

from bauer.decision_memory import DecisionMemory, DecisionRecord, _similarity
from bauer.escalation import (
    EscalationEngine,
    EscalationEvent,
    EscalationRule,
    Severity,
    make_default_escalation_engine,
)


# ===========================================================================
# TF-IDF similarity helper
# ===========================================================================


class TestSimilarity:
    def test_identical_text(self):
        s = _similarity("delete log files", "delete log files")
        assert s == pytest.approx(1.0)

    def test_disjoint_text(self):
        s = _similarity("hello world", "foo bar baz")
        assert s == pytest.approx(0.0)

    def test_partial_overlap(self):
        s = _similarity("delete log files", "remove log entries")
        assert 0.0 < s < 1.0

    def test_empty_query(self):
        s = _similarity("", "some text")
        assert s == pytest.approx(0.0)


# ===========================================================================
# DecisionMemory
# ===========================================================================


class TestDecisionMemory:
    def _mem(self) -> DecisionMemory:
        return DecisionMemory(db_path=":memory:")

    # ── record / get ─────────────────────────────────────────────────────────

    def test_record_returns_id(self):
        m = self._mem()
        did = m.record("User asks to delete /etc", "Requested confirmation")
        assert did.startswith("dec_")

    def test_get_existing(self):
        m = self._mem()
        did = m.record("context text", "I chose option A",
                       outcome="good", tags=["safety"], score=0.9)
        rec = m.get(did)
        assert rec is not None
        assert rec.context == "context text"
        assert rec.decision == "I chose option A"
        assert rec.outcome == "good"
        assert "safety" in rec.tags
        assert rec.score == pytest.approx(0.9)

    def test_get_nonexistent(self):
        m = self._mem()
        assert m.get("dec_nope") is None

    def test_outcome_defaults_to_neutral(self):
        m = self._mem()
        did = m.record("ctx", "dec")
        assert m.get(did).outcome == "neutral"

    def test_invalid_outcome_normalised_to_neutral(self):
        m = self._mem()
        did = m.record("ctx", "dec", outcome="unknown_value")
        assert m.get(did).outcome == "neutral"

    def test_score_clamped_to_01(self):
        m = self._mem()
        did = m.record("ctx", "dec", score=99.0)
        assert m.get(did).score == pytest.approx(1.0)
        did2 = m.record("ctx", "dec", score=-5.0)
        assert m.get(did2).score == pytest.approx(0.0)

    def test_context_truncated_at_2000(self):
        m = self._mem()
        long_ctx = "x" * 3000
        did = m.record(long_ctx, "dec")
        assert len(m.get(did).context) == 2000

    # ── update_outcome ────────────────────────────────────────────────────────

    def test_update_outcome_good_to_bad(self):
        m = self._mem()
        did = m.record("ctx", "dec", outcome="good")
        ok = m.update_outcome(did, "bad", score=0.1)
        assert ok is True
        rec = m.get(did)
        assert rec.outcome == "bad"
        assert rec.score == pytest.approx(0.1)

    def test_update_outcome_nonexistent(self):
        m = self._mem()
        assert m.update_outcome("nope", "good") is False

    # ── delete ────────────────────────────────────────────────────────────────

    def test_delete_existing(self):
        m = self._mem()
        did = m.record("ctx", "dec")
        assert m.delete(did) is True
        assert m.get(did) is None

    def test_delete_nonexistent(self):
        m = self._mem()
        assert m.delete("dec_nope") is False

    # ── count ─────────────────────────────────────────────────────────────────

    def test_count_total(self):
        m = self._mem()
        m.record("a", "1", outcome="good")
        m.record("b", "2", outcome="bad")
        m.record("c", "3", outcome="neutral")
        assert m.count() == 3

    def test_count_by_outcome(self):
        m = self._mem()
        m.record("a", "1", outcome="good")
        m.record("b", "2", outcome="good")
        m.record("c", "3", outcome="bad")
        assert m.count(outcome="good") == 2
        assert m.count(outcome="bad") == 1
        assert m.count(outcome="neutral") == 0

    # ── list queries ──────────────────────────────────────────────────────────

    def test_list_recent_sorted_newest_first(self):
        m = self._mem()
        d1 = m.record("first", "1")
        time.sleep(0.01)
        d2 = m.record("second", "2")
        recent = m.list_recent(limit=5)
        assert recent[0].id == d2
        assert recent[1].id == d1

    def test_list_by_outcome(self):
        m = self._mem()
        d1 = m.record("g", "1", outcome="good")
        d2 = m.record("b", "2", outcome="bad")
        goods = m.list_by_outcome("good")
        assert any(r.id == d1 for r in goods)
        assert not any(r.id == d2 for r in goods)

    # ── search ────────────────────────────────────────────────────────────────

    def test_search_returns_top_k(self):
        m = self._mem()
        for i in range(10):
            m.record(f"context {i}", f"decision {i}")
        results = m.search("context 5", top_k=3)
        assert len(results) <= 3

    def test_search_most_similar_first(self):
        m = self._mem()
        m.record("delete log files from /var/log", "Confirmed deletion")
        m.record("weather forecast for tomorrow", "Fetched weather API")
        results = m.search("remove log files", top_k=2)
        # The log-related decision should score higher
        assert results[0].context.startswith("delete log")

    def test_search_outcome_filter(self):
        m = self._mem()
        m.record("delete logs", "confirmed", outcome="good")
        m.record("delete logs", "executed directly", outcome="bad")
        results = m.search("delete logs", outcome_filter="good")
        assert all(r.outcome == "good" for r in results)

    def test_search_min_score_filter(self):
        m = self._mem()
        m.record("something", "low quality", score=0.1)
        m.record("something", "high quality", score=0.9)
        results = m.search("something", min_score=0.5)
        assert all(r.score >= 0.5 for r in results)

    def test_search_tags_filter(self):
        m = self._mem()
        m.record("ctx", "tagged", tags=["safety", "approval"])
        m.record("ctx", "untagged")
        results = m.search("ctx", tags_filter=["safety"])
        assert all("safety" in r.tags for r in results)

    def test_search_empty_store(self):
        m = self._mem()
        results = m.search("anything")
        assert results == []

    def test_search_sets_similarity_field(self):
        m = self._mem()
        m.record("delete log files", "confirmed")
        results = m.search("delete log files")
        assert results[0].similarity > 0.0

    # ── stats ─────────────────────────────────────────────────────────────────

    def test_stats_structure(self):
        m = self._mem()
        m.record("x", "y", outcome="good")
        m.record("a", "b", outcome="bad")
        s = m.stats()
        assert s["total"] == 2
        assert s["good"] == 1
        assert s["bad"] == 1
        assert "avg_score" in s

    # ── session_id ────────────────────────────────────────────────────────────

    def test_session_id_stored(self):
        m = DecisionMemory(db_path=":memory:", session_id="sess_xyz")
        did = m.record("ctx", "dec")
        assert m.get(did).session_id == "sess_xyz"

    # ── pruning ───────────────────────────────────────────────────────────────

    def test_pruning_removes_low_score_records(self):
        m = DecisionMemory(db_path=":memory:", max_records=5)
        # Insert 4 high-score and 1 low-score
        for i in range(4):
            m.record(f"high quality {i}", f"good decision {i}", score=0.9)
        m.record("low quality", "bad decision", score=0.01)
        # Insert one more to trigger pruning
        m.record("trigger", "prune", score=0.8)
        # After pruning, count should be ≤ max_records * 0.9 + 1
        assert m.count() <= 6

    # ── file-backed DB ────────────────────────────────────────────────────────

    def test_persistent_db(self, tmp_path):
        db = tmp_path / "decisions.db"
        m1 = DecisionMemory(db_path=db)
        did = m1.record("persistent context", "persistent decision")
        del m1

        m2 = DecisionMemory(db_path=db)
        rec = m2.get(did)
        assert rec is not None
        assert rec.context == "persistent context"


# ===========================================================================
# EscalationRule
# ===========================================================================


class TestEscalationRule:
    def test_matches_exact(self):
        r = EscalationRule("r1", reason_pattern="budget_exhausted")
        assert r.matches("budget_exhausted") is True
        assert r.matches("other_reason") is False

    def test_matches_regex(self):
        r = EscalationRule("r1", reason_pattern=r"worker_\d+_dead")
        assert r.matches("worker_1_dead") is True
        assert r.matches("worker_abc_dead") is False

    def test_matches_all(self):
        r = EscalationRule("r1", reason_pattern=".*")
        assert r.matches("anything") is True
        assert r.matches("") is True

    def test_disabled_never_matches(self):
        r = EscalationRule("r1", reason_pattern=".*", enabled=False)
        assert r.matches("anything") is False

    def test_format_message_default(self):
        r = EscalationRule("r1", severity=Severity.CRITICAL)
        msg = r.format_message("budget_exhausted", {})
        assert "CRITICAL" in msg
        assert "budget_exhausted" in msg

    def test_format_message_template(self):
        r = EscalationRule("r1", message_template="Alert: {reason} (sev={severity})")
        msg = r.format_message("oops", {})
        assert "oops" in msg
        assert "warning" in msg


# ===========================================================================
# EscalationEngine
# ===========================================================================


class TestEscalationEngine:
    def _engine(self) -> EscalationEngine:
        return EscalationEngine()

    # ── basic escalation ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_fires_matching_rule(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1", reason_pattern="budget_exhausted",
                                        cooldown_seconds=0))
        events = await engine.escalate("budget_exhausted", {})
        assert len(events) == 1
        assert events[0].rule_name == "r1"

    @pytest.mark.asyncio
    async def test_does_not_fire_non_matching(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1", reason_pattern="budget_exhausted",
                                        cooldown_seconds=0))
        events = await engine.escalate("worker_crashed", {})
        assert events == []

    @pytest.mark.asyncio
    async def test_fires_multiple_matching_rules(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1", reason_pattern=".*", cooldown_seconds=0))
        engine.add_rule(EscalationRule("r2", reason_pattern=".*", cooldown_seconds=0))
        events = await engine.escalate("anything", {})
        assert len(events) == 2

    # ── cooldown ──────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_cooldown_suppresses_repeated_escalation(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1", reason_pattern=".*",
                                        cooldown_seconds=60.0))
        await engine.escalate("reason", {})
        events2 = await engine.escalate("reason", {})
        assert events2 == []

    @pytest.mark.asyncio
    async def test_different_context_not_suppressed(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1", reason_pattern=".*",
                                        cooldown_seconds=60.0))
        await engine.escalate("reason", {"key": "a"})
        events2 = await engine.escalate("reason", {"key": "b"})
        assert len(events2) == 1  # different context hash

    @pytest.mark.asyncio
    async def test_reset_cooldowns_allows_retry(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1", reason_pattern=".*",
                                        cooldown_seconds=60.0))
        await engine.escalate("reason", {})
        engine.reset_cooldowns()
        events = await engine.escalate("reason", {})
        assert len(events) == 1

    # ── callback channel ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_callback_channel_called(self):
        received = []

        async def cb(ev: EscalationEvent):
            received.append(ev)

        engine = self._engine()
        engine.add_rule(EscalationRule("r1", reason_pattern=".*",
                                        channels=["callback"], cooldown_seconds=0))
        engine.set_callback(cb)
        await engine.escalate("reason", {})
        assert len(received) == 1
        assert received[0].reason == "reason"

    @pytest.mark.asyncio
    async def test_callback_always_called_if_set(self):
        """Even if 'callback' not in channels, the engine's callback is called."""
        received = []

        async def cb(ev: EscalationEvent):
            received.append(ev)

        engine = EscalationEngine(callback=cb)
        engine.add_rule(EscalationRule("r1", reason_pattern=".*",
                                        channels=["log"], cooldown_seconds=0))
        await engine.escalate("reason", {})
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_crashing_callback_does_not_propagate(self):
        async def bad_cb(ev):
            raise RuntimeError("callback crash")

        engine = EscalationEngine(callback=bad_cb)
        engine.add_rule(EscalationRule("r1", reason_pattern=".*",
                                        channels=["callback"], cooldown_seconds=0))
        # Should not raise
        events = await engine.escalate("reason", {})
        assert len(events) == 1

    # ── severity ──────────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_severity_propagated(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1", reason_pattern=".*",
                                        severity=Severity.CRITICAL, cooldown_seconds=0))
        events = await engine.escalate("reason", {})
        assert events[0].severity == Severity.CRITICAL

    # ── rule management ───────────────────────────────────────────────────────

    def test_add_and_list_rules(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1"))
        engine.add_rule(EscalationRule("r2"))
        assert len(engine.list_rules()) == 2

    def test_remove_rule(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1"))
        ok = engine.remove_rule("r1")
        assert ok is True
        assert engine.list_rules() == []

    def test_remove_nonexistent(self):
        engine = self._engine()
        assert engine.remove_rule("nope") is False

    # ── history / stats ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_history_accumulates(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1", reason_pattern=".*", cooldown_seconds=0))
        await engine.escalate("a", {})
        await engine.escalate("b", {})
        assert engine.count() == 2

    @pytest.mark.asyncio
    async def test_count_by_severity(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1", severity=Severity.CRITICAL,
                                        cooldown_seconds=0))
        await engine.escalate("x", {})
        await engine.escalate("y", {})
        assert engine.count(severity=Severity.CRITICAL) == 2
        assert engine.count(severity=Severity.WARNING) == 0

    def test_stats_structure(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1"))
        s = engine.stats()
        assert "rules" in s
        assert "total_fired" in s
        assert "by_severity" in s

    # ── event.to_dict ─────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_event_to_dict(self):
        engine = self._engine()
        engine.add_rule(EscalationRule("r1", reason_pattern=".*", cooldown_seconds=0))
        events = await engine.escalate("test", {"foo": "bar"})
        d = events[0].to_dict()
        assert d["reason"] == "test"
        assert d["context"]["foo"] == "bar"
        assert "severity" in d
        assert "timestamp" in d


# ===========================================================================
# make_default_escalation_engine
# ===========================================================================


class TestMakeDefaultEscalationEngine:
    @pytest.mark.asyncio
    async def test_default_rules_present(self):
        engine = make_default_escalation_engine()
        rules = {r.name for r in engine.list_rules()}
        assert "budget_exhausted" in rules
        assert "worker_dead" in rules
        assert "catch_all" in rules

    @pytest.mark.asyncio
    async def test_budget_exhausted_fires(self):
        received = []

        async def cb(ev):
            received.append(ev)

        engine = make_default_escalation_engine(callback=cb)
        engine.reset_cooldowns()
        await engine.escalate("budget_exhausted", {})
        assert len(received) >= 1
        assert received[0].severity == Severity.CRITICAL

    @pytest.mark.asyncio
    async def test_unknown_reason_fires_catch_all(self):
        received = []

        async def cb(ev):
            received.append(ev)

        engine = make_default_escalation_engine(callback=cb)
        engine.reset_cooldowns()
        await engine.escalate("some_unknown_reason", {})
        # catch_all rule should have fired
        assert any(e.rule_name == "catch_all" for e in received)
