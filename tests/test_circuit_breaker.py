"""Tests for bauer/circuit_breaker.py — CLOSED/OPEN/HALF_OPEN state machine."""

from __future__ import annotations

import time
import threading
import pytest

from bauer.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CBState,
    global_cb,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cb():
    """Fresh circuit breaker with low thresholds for fast tests."""
    return CircuitBreaker(
        failure_threshold=3,
        reset_timeout=0.1,   # 100ms — allows fast HALF_OPEN tests
        success_threshold=1,
        excluded_exceptions=(KeyboardInterrupt, SystemExit),
    )


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def test_initial_state_is_closed(cb):
    assert cb.state("prov_a") == CBState.CLOSED


def test_is_open_false_initially(cb):
    assert cb.is_open("prov_a") is False


# ---------------------------------------------------------------------------
# Failure recording → OPEN
# ---------------------------------------------------------------------------


def test_open_after_failure_threshold(cb):
    for _ in range(3):
        cb.record_failure("prov_b", RuntimeError("oops"))
    assert cb.state("prov_b") == CBState.OPEN
    assert cb.is_open("prov_b") is True


def test_not_open_below_threshold(cb):
    cb.record_failure("prov_c", RuntimeError())
    cb.record_failure("prov_c", RuntimeError())
    assert cb.state("prov_c") == CBState.CLOSED
    assert cb.is_open("prov_c") is False


def test_success_resets_failure_count(cb):
    cb.record_failure("prov_d", RuntimeError())
    cb.record_failure("prov_d", RuntimeError())
    cb.record_success("prov_d")
    # One more failure should NOT open (count was reset)
    cb.record_failure("prov_d", RuntimeError())
    assert cb.state("prov_d") == CBState.CLOSED


# ---------------------------------------------------------------------------
# OPEN → HALF_OPEN → CLOSED
# ---------------------------------------------------------------------------


def test_half_open_after_reset_timeout(cb):
    for _ in range(3):
        cb.record_failure("prov_e", RuntimeError())
    assert cb.state("prov_e") == CBState.OPEN

    time.sleep(0.15)  # wait for reset_timeout
    assert cb.state("prov_e") == CBState.HALF_OPEN


def test_closed_after_success_in_half_open(cb):
    for _ in range(3):
        cb.record_failure("prov_f", RuntimeError())
    time.sleep(0.15)
    assert cb.state("prov_f") == CBState.HALF_OPEN
    cb.record_success("prov_f")
    assert cb.state("prov_f") == CBState.CLOSED


def test_back_to_open_after_failure_in_half_open(cb):
    for _ in range(3):
        cb.record_failure("prov_g", RuntimeError())
    time.sleep(0.15)
    assert cb.state("prov_g") == CBState.HALF_OPEN
    cb.record_failure("prov_g", RuntimeError())
    assert cb.state("prov_g") == CBState.OPEN


# ---------------------------------------------------------------------------
# Reset
# ---------------------------------------------------------------------------


def test_manual_reset(cb):
    for _ in range(3):
        cb.record_failure("prov_h", RuntimeError())
    assert cb.is_open("prov_h")
    cb.reset("prov_h")
    assert cb.state("prov_h") == CBState.CLOSED


def test_reset_unknown_provider(cb):
    # Should not raise
    cb.reset("never_seen_provider")
    assert cb.state("never_seen_provider") == CBState.CLOSED


# ---------------------------------------------------------------------------
# Excluded exceptions
# ---------------------------------------------------------------------------


def test_excluded_exceptions_do_not_count(cb):
    for _ in range(5):
        cb.record_failure("prov_i", KeyboardInterrupt())
    # Should still be CLOSED
    assert cb.state("prov_i") == CBState.CLOSED


def test_non_excluded_exception_counts(cb):
    for _ in range(3):
        cb.record_failure("prov_j", ValueError("bad"))
    assert cb.state("prov_j") == CBState.OPEN


# ---------------------------------------------------------------------------
# CircuitOpenError
# ---------------------------------------------------------------------------


def test_circuit_open_error_attrs():
    err = CircuitOpenError("my_provider", time.time() + 60)
    assert "my_provider" in str(err)
    assert err.provider == "my_provider"
    assert err.opens_at > time.time()


# ---------------------------------------------------------------------------
# Context manager .call()
# ---------------------------------------------------------------------------


def test_call_context_manager_success(cb):
    with cb.call("prov_k"):
        pass  # no exception → records success


def test_call_context_manager_failure(cb):
    for _ in range(3):
        try:
            with cb.call("prov_l"):
                raise RuntimeError("simulated failure")
        except RuntimeError:
            pass
    assert cb.is_open("prov_l")


def test_call_raises_circuit_open_error_when_open(cb):
    for _ in range(3):
        try:
            with cb.call("prov_m"):
                raise RuntimeError()
        except RuntimeError:
            pass
    with pytest.raises(CircuitOpenError):
        with cb.call("prov_m"):
            pass  # should never reach here


# ---------------------------------------------------------------------------
# status_all()
# ---------------------------------------------------------------------------


def test_status_all_empty():
    fresh = CircuitBreaker(failure_threshold=3, reset_timeout=60)
    status = fresh.status_all()
    assert isinstance(status, dict)
    assert len(status) == 0  # no providers touched yet


def test_status_all_includes_touched_providers(cb):
    cb.record_failure("prov_n", RuntimeError())
    cb.record_failure("prov_n", RuntimeError())
    status = cb.status_all()
    assert "prov_n" in status
    assert "state" in status["prov_n"]
    assert "failure_count" in status["prov_n"]


def test_status_all_reports_open_state(cb):
    for _ in range(3):
        cb.record_failure("prov_o", RuntimeError())
    status = cb.status_all()
    assert status["prov_o"]["state"] == "open"


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_concurrent_failures_safe(cb):
    """Multiple threads failing the same provider — state must stay consistent."""
    barrier = threading.Barrier(5)
    errors: list[Exception] = []

    def _worker():
        try:
            barrier.wait()
            for _ in range(2):
                cb.record_failure("prov_p", RuntimeError("thread failure"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"Thread errors: {errors}"
    # 5 threads × 2 failures = 10 failures > threshold(3) → must be OPEN
    assert cb.is_open("prov_p")


# ---------------------------------------------------------------------------
# global_cb singleton
# ---------------------------------------------------------------------------


def test_global_cb_is_circuit_breaker():
    assert isinstance(global_cb, CircuitBreaker)


def test_global_cb_separate_from_test_fixtures(cb):
    # They should be different instances
    assert global_cb is not cb
