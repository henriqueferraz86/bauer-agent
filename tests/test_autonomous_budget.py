"""Tests for bauer/autonomous_budget.py — hard spend limits for autonomous agents."""

from __future__ import annotations

import time

import pytest

from bauer.autonomous_budget import (
    AutonomousBudget,
    BudgetExhaustedError,
    BudgetSnapshot,
    BudgetStatus,
    make_budget,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _budget(**kw) -> AutonomousBudget:
    """Factory with sensible defaults for testing."""
    defaults = dict(
        max_cost_usd=1.0,
        max_wall_seconds=3600,
        max_llm_calls=10,
        max_tool_calls=20,
        max_output_tokens=10_000,
        warn_at_cost_pct=0.8,
        warn_at_time_pct=0.8,
    )
    defaults.update(kw)
    return AutonomousBudget(**defaults)


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def test_initial_status_ok():
    b = _budget()
    assert b._compute_status() == BudgetStatus.OK
    assert not b.is_exhausted
    assert not b.is_warning


def test_initial_counters_zero():
    b = _budget()
    assert b._cost_usd == 0.0
    assert b._llm_calls == 0
    assert b._tool_calls == 0
    assert b._output_tokens == 0


# ---------------------------------------------------------------------------
# consume_llm_call
# ---------------------------------------------------------------------------


def test_consume_llm_increments_counters():
    b = _budget()
    b.consume_llm_call(cost_usd=0.01, output_tokens=100)
    assert b._llm_calls == 1
    assert b._cost_usd == pytest.approx(0.01)
    assert b._output_tokens == 100


def test_consume_llm_negative_values_ignored():
    b = _budget()
    b.consume_llm_call(cost_usd=-5.0, output_tokens=-999)
    assert b._cost_usd == 0.0
    assert b._output_tokens == 0


def test_consume_llm_multiple_calls_accumulate():
    b = _budget()
    for _ in range(5):
        b.consume_llm_call(cost_usd=0.10, output_tokens=50)
    assert b._llm_calls == 5
    assert b._cost_usd == pytest.approx(0.50)
    assert b._output_tokens == 250


# ---------------------------------------------------------------------------
# consume_tool_call
# ---------------------------------------------------------------------------


def test_consume_tool_increments_counter():
    b = _budget()
    b.consume_tool_call()
    b.consume_tool_call()
    assert b._tool_calls == 2


# ---------------------------------------------------------------------------
# consume_cost
# ---------------------------------------------------------------------------


def test_consume_cost_adds_to_cost():
    b = _budget()
    b.consume_cost(0.25)
    assert b._cost_usd == pytest.approx(0.25)
    assert b._llm_calls == 0  # not an LLM call


# ---------------------------------------------------------------------------
# Hard limit: cost
# ---------------------------------------------------------------------------


def test_cost_limit_exhausted():
    b = _budget(max_cost_usd=0.10)
    b.consume_llm_call(cost_usd=0.10)  # exactly at limit
    assert b.is_exhausted
    assert b._compute_status() == BudgetStatus.EXHAUSTED


def test_cost_limit_over_limit():
    b = _budget(max_cost_usd=0.10)
    b.consume_llm_call(cost_usd=0.11)
    assert b.is_exhausted


def test_cost_below_limit_ok():
    b = _budget(max_cost_usd=1.0)
    b.consume_llm_call(cost_usd=0.09)
    assert not b.is_exhausted


# ---------------------------------------------------------------------------
# Hard limit: llm_calls
# ---------------------------------------------------------------------------


def test_llm_calls_limit_exhausted():
    b = _budget(max_llm_calls=3)
    for _ in range(3):
        b.consume_llm_call()
    assert b.is_exhausted


def test_llm_calls_below_limit_ok():
    b = _budget(max_llm_calls=5)
    b.consume_llm_call()
    assert not b.is_exhausted


# ---------------------------------------------------------------------------
# Hard limit: tool_calls
# ---------------------------------------------------------------------------


def test_tool_calls_limit_exhausted():
    b = _budget(max_tool_calls=5)
    for _ in range(5):
        b.consume_tool_call()
    assert b.is_exhausted


# ---------------------------------------------------------------------------
# Hard limit: output_tokens
# ---------------------------------------------------------------------------


def test_output_tokens_limit_exhausted():
    b = _budget(max_output_tokens=100)
    b.consume_llm_call(output_tokens=100)
    assert b.is_exhausted


def test_output_tokens_accumulate_across_calls():
    b = _budget(max_output_tokens=300)
    b.consume_llm_call(output_tokens=100)
    b.consume_llm_call(output_tokens=100)
    b.consume_llm_call(output_tokens=100)
    assert b.is_exhausted


# ---------------------------------------------------------------------------
# Hard limit: wall time
# ---------------------------------------------------------------------------


def test_wall_time_limit_not_exhausted_immediately():
    b = _budget(max_wall_seconds=3600)
    assert not b.is_exhausted  # just created, well under 1h


def test_wall_time_exhausted_when_exceeded():
    """Simulate time passing by backdating _start_time."""
    b = AutonomousBudget(max_wall_seconds=3600)
    # Rewind the start clock by 7201 seconds — simulates 7201s elapsed.
    b._start_time = time.monotonic() - 7201
    assert b.is_exhausted  # 7201s > 3600s


# ---------------------------------------------------------------------------
# Soft warning thresholds
# ---------------------------------------------------------------------------


def test_warning_on_cost_threshold():
    b = _budget(max_cost_usd=1.0, warn_at_cost_pct=0.8)
    b.consume_llm_call(cost_usd=0.80)  # exactly at 80%
    assert b.is_warning
    assert not b.is_exhausted


def test_warning_resolves_to_ok_below_threshold():
    b = _budget(max_cost_usd=1.0, warn_at_cost_pct=0.8)
    b.consume_llm_call(cost_usd=0.79)
    assert not b.is_warning


def test_warning_time_threshold():
    """Simulate time passing by backdating _start_time."""
    b = AutonomousBudget(max_wall_seconds=3600, warn_at_time_pct=0.80)
    # 3000/3600 ≈ 83% — above 80% warn threshold
    b._start_time = time.monotonic() - 3000
    assert b.is_warning


# ---------------------------------------------------------------------------
# BudgetExhaustedError raised on further consumption
# ---------------------------------------------------------------------------


def test_consume_llm_raises_if_already_exhausted():
    b = _budget(max_llm_calls=1)
    b.consume_llm_call()
    assert b.is_exhausted
    with pytest.raises(BudgetExhaustedError):
        b.consume_llm_call()


def test_consume_tool_raises_if_already_exhausted():
    b = _budget(max_tool_calls=1)
    b.consume_tool_call()
    with pytest.raises(BudgetExhaustedError):
        b.consume_tool_call()


def test_consume_cost_raises_if_already_exhausted():
    b = _budget(max_cost_usd=0.01)
    b.consume_cost(0.01)
    with pytest.raises(BudgetExhaustedError):
        b.consume_cost(0.001)


# ---------------------------------------------------------------------------
# Remaining helpers
# ---------------------------------------------------------------------------


def test_remaining_cost_usd():
    b = _budget(max_cost_usd=1.0)
    b.consume_llm_call(cost_usd=0.30)
    assert b.remaining_cost_usd() == pytest.approx(0.70)


def test_remaining_llm_calls():
    b = _budget(max_llm_calls=10)
    b.consume_llm_call()
    b.consume_llm_call()
    assert b.remaining_llm_calls() == 8


def test_remaining_tool_calls():
    b = _budget(max_tool_calls=20)
    for _ in range(5):
        b.consume_tool_call()
    assert b.remaining_tool_calls() == 15


def test_remaining_zero_when_at_limit():
    b = _budget(max_cost_usd=0.10)
    b.consume_llm_call(cost_usd=0.10)
    assert b.remaining_cost_usd() == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# snapshot and summary
# ---------------------------------------------------------------------------


def test_snapshot_returns_budget_snapshot():
    b = _budget()
    b.consume_llm_call(cost_usd=0.05, output_tokens=200)
    snap = b.snapshot()
    assert isinstance(snap, BudgetSnapshot)
    assert snap.cost_usd == pytest.approx(0.05)
    assert snap.llm_calls == 1
    assert snap.output_tokens == 200


def test_snapshot_pct_computed():
    b = _budget(max_cost_usd=1.0)
    b.consume_llm_call(cost_usd=0.50)
    snap = b.snapshot()
    assert snap.cost_pct == pytest.approx(50.0)


def test_summary_returns_string():
    b = _budget()
    s = b.summary()
    assert isinstance(s, str)
    assert "Budget" in s


def test_to_dict_structure():
    b = _budget()
    b.consume_llm_call(cost_usd=0.02, output_tokens=100)
    d = b.to_dict()
    assert d["status"] == "ok"
    assert d["cost_usd"] == pytest.approx(0.02)
    assert d["llm_calls"] == 1
    assert "max_cost_usd" in d


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


def test_reset_clears_all_counters():
    b = _budget(max_llm_calls=2)
    b.consume_llm_call(cost_usd=0.50)
    b.consume_llm_call(cost_usd=0.50)
    assert b.is_exhausted
    b.reset()
    assert not b.is_exhausted
    assert b._cost_usd == 0.0
    assert b._llm_calls == 0
    assert b._tool_calls == 0
    assert b._output_tokens == 0


# ---------------------------------------------------------------------------
# make_budget factory
# ---------------------------------------------------------------------------


def test_make_budget_defaults():
    b = make_budget()
    assert b.max_cost_usd == 5.0
    assert b.max_wall_seconds == 3600
    assert b.max_llm_calls == 200
    assert b.max_tool_calls == 500
    assert b.max_output_tokens == 500_000


def test_make_budget_custom():
    b = make_budget(cost_usd=2.0, wall_seconds=600, llm_calls=50)
    assert b.max_cost_usd == 2.0
    assert b.max_wall_seconds == 600
    assert b.max_llm_calls == 50
