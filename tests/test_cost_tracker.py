"""Testes de bauer/cost_tracker.py — UsageRecord, CostTracker, singleton."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.cost_tracker import (
    CostTracker,
    UsageRecord,
    get_cost_tracker,
    reset_cost_trackers,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tracker(tmp_path: Path, session_id: str = "s1", budget_usd: float = 0.0) -> CostTracker:
    return CostTracker(session_id=session_id, budget_usd=budget_usd, file_path=tmp_path / "cost.jsonl")


def _fake_catalog(model: str, provider: str, cost_in: float = 2.0, cost_out: float = 6.0):
    """Retorna patch para catalog_models que devolve preços fixos."""
    def _catalog(provider: str = "", **kw):
        return [{"id": model, "cost_in": cost_in, "cost_out": cost_out}]
    return _catalog


# ---------------------------------------------------------------------------
# TestUsageRecord
# ---------------------------------------------------------------------------

class TestUsageRecord:
    def test_total_tokens(self):
        r = UsageRecord("s", "m", "p", 100, 50, 0.0)
        assert r.total_tokens == 150

    def test_to_dict_has_keys(self):
        r = UsageRecord("s", "m", "p", 100, 50, 0.001)
        d = r.to_dict()
        assert all(k in d for k in ("session_id", "model", "prompt_tokens", "completion_tokens", "cost_usd", "ts"))

    def test_cost_rounded(self):
        r = UsageRecord("s", "m", "p", 0, 0, 0.123456789)
        d = r.to_dict()
        assert d["cost_usd"] == round(0.123456789, 8)

    def test_ts_auto_set(self):
        t0 = time.time()
        r = UsageRecord("s", "m", "p", 0, 0, 0.0)
        assert r.ts >= t0


# ---------------------------------------------------------------------------
# TestCostTrackerRecord
# ---------------------------------------------------------------------------

class TestCostTrackerRecord:
    def test_record_returns_usage_record(self, tmp_path):
        t = _tracker(tmp_path)
        with patch.object(t, "_get_price", return_value={"cost_in": 2.0, "cost_out": 6.0}):
            rec = t.record("gpt-4", "openai", 1000, 500)
        assert isinstance(rec, UsageRecord)

    def test_record_calculates_cost(self, tmp_path):
        t = _tracker(tmp_path)
        # cost = (1000 * 2.0 + 500 * 6.0) / 1_000_000 = 0.005
        with patch.object(t, "_get_price", return_value={"cost_in": 2.0, "cost_out": 6.0}):
            rec = t.record("m", "p", 1000, 500)
        assert rec.cost_usd == pytest.approx(0.005, rel=1e-4)

    def test_record_persists_to_file(self, tmp_path):
        t = _tracker(tmp_path)
        with patch.object(t, "_get_price", return_value={"cost_in": 0.0, "cost_out": 0.0}):
            t.record("m", "p", 100, 50)
        fp = tmp_path / "cost.jsonl"
        assert fp.exists()
        lines = fp.read_text().strip().splitlines()
        assert len(lines) == 1

    def test_multiple_records_appended(self, tmp_path):
        t = _tracker(tmp_path)
        with patch.object(t, "_get_price", return_value={"cost_in": 0.0, "cost_out": 0.0}):
            t.record("m", "p", 100, 50)
            t.record("m", "p", 200, 100)
        lines = (tmp_path / "cost.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2

    def test_record_stores_session_id(self, tmp_path):
        t = _tracker(tmp_path, session_id="mysession")
        with patch.object(t, "_get_price", return_value={"cost_in": 0.0, "cost_out": 0.0}):
            rec = t.record("m", "p", 100, 50)
        assert rec.session_id == "mysession"

    def test_zero_cost_when_price_missing(self, tmp_path):
        t = _tracker(tmp_path)
        with patch.object(t, "_get_price", return_value={"cost_in": 0.0, "cost_out": 0.0}):
            rec = t.record("unknown", "unknown", 1000, 500)
        assert rec.cost_usd == 0.0


# ---------------------------------------------------------------------------
# TestSessionTotals
# ---------------------------------------------------------------------------

class TestSessionTotals:
    def test_initial_totals_zero(self, tmp_path):
        t = _tracker(tmp_path)
        totals = t.session_totals()
        assert totals["total_tokens"] == 0
        assert totals["cost_usd"] == 0.0
        assert totals["calls"] == 0

    def test_totals_accumulate(self, tmp_path):
        t = _tracker(tmp_path)
        with patch.object(t, "_get_price", return_value={"cost_in": 2.0, "cost_out": 6.0}):
            t.record("m", "p", 100, 50)
            t.record("m", "p", 200, 100)
        totals = t.session_totals()
        assert totals["calls"] == 2
        assert totals["total_tokens"] == 450

    def test_totals_session_id(self, tmp_path):
        t = _tracker(tmp_path, session_id="abc")
        totals = t.session_totals()
        assert totals["session_id"] == "abc"

    def test_budget_in_totals(self, tmp_path):
        t = _tracker(tmp_path, budget_usd=1.0)
        totals = t.session_totals()
        assert totals["budget_usd"] == 1.0

    def test_budget_remaining_decreases(self, tmp_path):
        t = _tracker(tmp_path, budget_usd=0.01)
        # cost = (1000 * 2.0 + 500 * 6.0) / 1M = 0.005
        with patch.object(t, "_get_price", return_value={"cost_in": 2.0, "cost_out": 6.0}):
            t.record("m", "p", 1000, 500)
        totals = t.session_totals()
        assert totals["budget_remaining_usd"] == pytest.approx(0.005, rel=1e-3)

    def test_no_budget_remaining_none(self, tmp_path):
        t = _tracker(tmp_path, budget_usd=0.0)
        totals = t.session_totals()
        assert totals["budget_remaining_usd"] is None


# ---------------------------------------------------------------------------
# TestBudgetExceeded
# ---------------------------------------------------------------------------

class TestBudgetExceeded:
    def test_no_budget_never_exceeded(self, tmp_path):
        t = _tracker(tmp_path, budget_usd=0.0)
        with patch.object(t, "_get_price", return_value={"cost_in": 100.0, "cost_out": 100.0}):
            t.record("m", "p", 10000, 10000)
        assert t.budget_exceeded() is False

    def test_budget_exceeded_when_over(self, tmp_path):
        t = _tracker(tmp_path, budget_usd=0.001)
        # cost > 0.001
        with patch.object(t, "_get_price", return_value={"cost_in": 100.0, "cost_out": 100.0}):
            t.record("m", "p", 10000, 10000)
        assert t.budget_exceeded() is True

    def test_alert_called_when_exceeded(self, tmp_path):
        alerts = []
        t = CostTracker(session_id="s", budget_usd=0.001, file_path=tmp_path / "c.jsonl",
                        alert_callback=lambda totals: alerts.append(totals))
        with patch.object(t, "_get_price", return_value={"cost_in": 100.0, "cost_out": 100.0}):
            t.record("m", "p", 100, 100)
        assert len(alerts) >= 1


# ---------------------------------------------------------------------------
# TestFormatStatus
# ---------------------------------------------------------------------------

class TestFormatStatus:
    def test_format_status_no_budget(self, tmp_path):
        t = _tracker(tmp_path)
        status = t.format_status()
        assert "$" in status
        assert "tokens" in status

    def test_format_status_with_budget(self, tmp_path):
        t = _tracker(tmp_path, budget_usd=1.0)
        status = t.format_status()
        assert "%" in status


# ---------------------------------------------------------------------------
# TestLoadHistory
# ---------------------------------------------------------------------------

class TestLoadHistory:
    def test_load_history_from_file(self, tmp_path):
        fp = tmp_path / "cost.jsonl"
        rec = UsageRecord("s1", "m", "p", 100, 50, 0.001)
        with open(fp, "w") as f:
            f.write(json.dumps(rec.to_dict()) + "\n")
        history = CostTracker.load_history(file_path=fp)
        assert len(history) == 1
        assert history[0]["session_id"] == "s1"

    def test_filter_by_session(self, tmp_path):
        fp = tmp_path / "cost.jsonl"
        with open(fp, "w") as f:
            f.write(json.dumps(UsageRecord("s1", "m", "p", 100, 50, 0.001).to_dict()) + "\n")
            f.write(json.dumps(UsageRecord("s2", "m", "p", 100, 50, 0.001).to_dict()) + "\n")
        history = CostTracker.load_history(file_path=fp, session_id="s1")
        assert all(h["session_id"] == "s1" for h in history)

    def test_respects_limit(self, tmp_path):
        fp = tmp_path / "cost.jsonl"
        with open(fp, "w") as f:
            for i in range(20):
                r = UsageRecord(f"s{i}", "m", "p", 100, 50, 0.0)
                f.write(json.dumps(r.to_dict()) + "\n")
        history = CostTracker.load_history(file_path=fp, limit=5)
        assert len(history) == 5

    def test_missing_file_returns_empty(self, tmp_path):
        history = CostTracker.load_history(file_path=tmp_path / "nonexistent.jsonl")
        assert history == []

    def test_invalid_json_skipped(self, tmp_path):
        fp = tmp_path / "cost.jsonl"
        with open(fp, "w") as f:
            f.write('{"session_id": "s1", "model": "m", "ts": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0}\n')
            f.write("bad json\n")
        history = CostTracker.load_history(file_path=fp)
        assert len(history) == 1


# ---------------------------------------------------------------------------
# TestGetPriceWithCatalog
# ---------------------------------------------------------------------------

class TestGetPriceWithCatalog:
    def test_price_from_catalog(self, tmp_path):
        t = _tracker(tmp_path)
        with patch("bauer.models_dev.catalog_models") as mock_catalog:
            mock_catalog.return_value = [{"id": "gpt-4", "cost_in": 30.0, "cost_out": 60.0}]
            price = t._get_price("gpt-4", "openai")
        assert price["cost_in"] == 30.0

    def test_price_fallback_on_exception(self, tmp_path):
        t = _tracker(tmp_path)
        with patch("bauer.models_dev.catalog_models", side_effect=RuntimeError("fail")):
            price = t._get_price("unknown", "unknown")
        assert price["cost_in"] == 0.0

    def test_price_cached(self, tmp_path):
        t = _tracker(tmp_path)
        with patch("bauer.models_dev.catalog_models") as mock_catalog:
            mock_catalog.return_value = [{"id": "m", "cost_in": 5.0, "cost_out": 10.0}]
            t._get_price("m", "p")
            t._get_price("m", "p")
        mock_catalog.assert_called_once()


# ---------------------------------------------------------------------------
# TestSingleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def setup_method(self, method):
        reset_cost_trackers()

    def teardown_method(self, method):
        reset_cost_trackers()

    def test_same_session_same_instance(self):
        t1 = get_cost_tracker("sess-x")
        t2 = get_cost_tracker("sess-x")
        assert t1 is t2

    def test_different_sessions_different_instances(self):
        t1 = get_cost_tracker("sess-a")
        t2 = get_cost_tracker("sess-b")
        assert t1 is not t2

    def test_reset_clears(self):
        t1 = get_cost_tracker("s")
        reset_cost_trackers()
        t2 = get_cost_tracker("s")
        assert t1 is not t2
