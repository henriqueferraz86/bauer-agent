"""Tests for bauer.tool_timeout."""

from __future__ import annotations

import threading
import time

import pytest

from bauer.tool_timeout import call_with_timeout


# ---------------------------------------------------------------------------
# Basic functionality
# ---------------------------------------------------------------------------

class TestCallWithTimeout:
    def test_fast_fn_returns_result(self):
        result, timed_out = call_with_timeout(lambda: 42, timeout_s=5.0)
        assert result == 42
        assert timed_out is False

    def test_fast_fn_returns_string(self):
        result, timed_out = call_with_timeout(lambda: "hello", timeout_s=5.0)
        assert result == "hello"
        assert timed_out is False

    def test_fast_fn_returns_dict(self):
        data = {"key": "value", "n": 99}
        result, timed_out = call_with_timeout(lambda: data, timeout_s=5.0)
        assert result == data
        assert timed_out is False

    def test_timeout_zero_runs_without_limit(self):
        result, timed_out = call_with_timeout(lambda: "ok", timeout_s=0)
        assert result == "ok"
        assert timed_out is False

    def test_timeout_negative_runs_without_limit(self):
        result, timed_out = call_with_timeout(lambda: "ok", timeout_s=-1.0)
        assert result == "ok"
        assert timed_out is False

    def test_slow_fn_times_out(self):
        def slow():
            time.sleep(10)
            return "never"

        result, timed_out = call_with_timeout(slow, timeout_s=0.1, name="slow_tool")
        assert timed_out is True
        assert isinstance(result, str)
        assert "slow_tool" in result
        assert "0" in result  # contains the timeout value

    def test_timeout_message_contains_name(self):
        def hang():
            time.sleep(10)

        result, timed_out = call_with_timeout(hang, timeout_s=0.05, name="my_special_tool")
        assert timed_out is True
        assert "my_special_tool" in result

    def test_timeout_message_contains_duration(self):
        def hang():
            time.sleep(10)

        result, timed_out = call_with_timeout(hang, timeout_s=2.0, name="t")
        assert timed_out is True
        assert "2" in result

    def test_fn_exception_propagates(self):
        def boom():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            call_with_timeout(boom, timeout_s=5.0)

    def test_fn_exception_propagates_with_zero_timeout(self):
        def boom():
            raise RuntimeError("runtime!")

        with pytest.raises(RuntimeError, match="runtime!"):
            call_with_timeout(boom, timeout_s=0.0)

    def test_default_name_in_timeout_message(self):
        def hang():
            time.sleep(10)

        result, timed_out = call_with_timeout(hang, timeout_s=0.05)
        assert timed_out is True
        assert "tool" in result  # default name


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_parallel_calls_are_independent(self):
        """Multiple concurrent call_with_timeout calls don't interfere."""
        results: list[tuple] = [None] * 4  # type: ignore[list-item]

        def run(idx: int, sleep_s: float, timeout_s: float):
            def fn():
                time.sleep(sleep_s)
                return f"done_{idx}"
            results[idx] = call_with_timeout(fn, timeout_s=timeout_s, name=f"tool_{idx}")

        threads = [
            threading.Thread(target=run, args=(0, 0.01, 2.0)),
            threading.Thread(target=run, args=(1, 0.01, 2.0)),
            threading.Thread(target=run, args=(2, 5.0,  0.05)),  # times out
            threading.Thread(target=run, args=(3, 0.01, 2.0)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=3.0)

        assert results[0] == ("done_0", False)
        assert results[1] == ("done_1", False)
        assert results[2][1] is True          # timed out
        assert results[3] == ("done_3", False)

    def test_many_sequential_calls(self):
        count = 20
        for i in range(count):
            result, timed_out = call_with_timeout(lambda i=i: i * 2, timeout_s=1.0)
            assert result == i * 2
            assert timed_out is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_returns_none_is_valid(self):
        result, timed_out = call_with_timeout(lambda: None, timeout_s=1.0)
        assert result is None
        assert timed_out is False

    def test_returns_false_is_valid(self):
        result, timed_out = call_with_timeout(lambda: False, timeout_s=1.0)
        assert result is False
        assert timed_out is False

    def test_returns_empty_string_is_valid(self):
        result, timed_out = call_with_timeout(lambda: "", timeout_s=1.0)
        assert result == ""
        assert timed_out is False

    def test_returns_zero_is_valid(self):
        result, timed_out = call_with_timeout(lambda: 0, timeout_s=1.0)
        assert result == 0
        assert timed_out is False
