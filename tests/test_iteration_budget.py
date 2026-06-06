"""Tests for `bauer/iteration_budget.py`."""

from __future__ import annotations

import threading

import pytest

from bauer.iteration_budget import IterationBudget


def test_initial_state():
    b = IterationBudget(max_total=10)
    assert b.remaining == 10
    assert b.consumed == 0
    assert not b.exhausted


def test_consume_decrements_remaining():
    b = IterationBudget(max_total=10)
    b.consume(3)
    assert b.remaining == 7
    assert b.consumed == 3


def test_consume_default_is_one():
    b = IterationBudget(max_total=5)
    b.consume()
    assert b.remaining == 4


def test_consume_caps_at_max():
    b = IterationBudget(max_total=5)
    consumed = b.consume(10)
    assert consumed == 5
    assert b.remaining == 0
    assert b.exhausted


def test_consume_already_exhausted():
    b = IterationBudget(max_total=3)
    b.consume(3)
    assert b.exhausted
    consumed = b.consume(1)
    assert consumed == 0  # nothing more to consume


def test_refund_returns_iterations():
    b = IterationBudget(max_total=10)
    b.consume(5)
    b.refund(2)
    assert b.remaining == 7
    assert b.consumed == 3


def test_refund_cannot_exceed_consumed():
    b = IterationBudget(max_total=10)
    b.consume(3)
    refunded = b.refund(10)
    assert refunded == 3  # cap at consumed
    assert b.consumed == 0
    assert b.remaining == 10


def test_reset_zeroes_consumed():
    b = IterationBudget(max_total=5)
    b.consume(4)
    b.reset()
    assert b.consumed == 0
    assert b.remaining == 5


def test_zero_max_total_is_immediately_exhausted():
    b = IterationBudget(max_total=0)
    assert b.exhausted
    assert b.consume() == 0


def test_negative_max_total_rejected():
    with pytest.raises(ValueError):
        IterationBudget(max_total=-1)


def test_negative_consumed_rejected():
    with pytest.raises(ValueError):
        IterationBudget(max_total=5, consumed=-1)


def test_negative_consume_rejected():
    b = IterationBudget(max_total=10)
    with pytest.raises(ValueError):
        b.consume(-1)


def test_negative_refund_rejected():
    b = IterationBudget(max_total=10)
    with pytest.raises(ValueError):
        b.refund(-1)


def test_thread_safety_concurrent_consume():
    """100 threads each consuming once must respect max_total cap."""
    b = IterationBudget(max_total=50)

    def worker():
        b.consume(1)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert b.consumed == 50  # exactly the cap
    assert b.exhausted


def test_thread_safety_consume_and_refund():
    """Mixed consume/refund from many threads is consistent."""
    b = IterationBudget(max_total=100)

    def consumer():
        for _ in range(20):
            b.consume(1)

    def refunder():
        for _ in range(20):
            b.refund(1)

    threads = (
        [threading.Thread(target=consumer) for _ in range(5)]
        + [threading.Thread(target=refunder) for _ in range(5)]
    )
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 5×20 = 100 consume attempts (capped at 100), 5×20 = 100 refunds (capped at consumed)
    # Final consumed in [0, 100]; what matters is consistency, not exact value.
    assert 0 <= b.consumed <= 100
    assert b.remaining == 100 - b.consumed


def test_repr_includes_state():
    b = IterationBudget(max_total=10)
    b.consume(3)
    rep = repr(b)
    assert "3/10" in rep
    assert "remaining=7" in rep
