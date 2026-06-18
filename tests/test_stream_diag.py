"""Tests for G6 — StreamDiag per-token streaming diagnostics."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from bauer.delta_stream import (
    StreamDiag,
    TokenEvent,
    emit_delta,
    get_sink,
    reset_sink,
    set_sink,
)


# ─── TokenEvent ───────────────────────────────────────────────────────────────

class TestTokenEvent:
    def test_basic_construction(self):
        ev = TokenEvent(text="hello", token_index=0, elapsed_ms=12.5, is_first=True)
        assert ev.text == "hello"
        assert ev.token_index == 0
        assert ev.elapsed_ms == 12.5
        assert ev.is_first is True

    def test_is_first_defaults_false(self):
        ev = TokenEvent(text="x", token_index=1, elapsed_ms=5.0)
        assert ev.is_first is False


# ─── StreamDiag ───────────────────────────────────────────────────────────────

class TestStreamDiag:
    def test_initial_state(self):
        d = StreamDiag()
        assert d.token_count == 0
        assert d.ttft_ms is None
        assert d.elapsed_total_ms == 0.0
        assert d.tokens_per_second == 0.0
        assert d.has_data() is False

    def test_start_returns_self(self):
        d = StreamDiag()
        assert d.start() is d

    def test_on_delta_increments_count(self):
        d = StreamDiag().start()
        d.on_delta("hello")
        d.on_delta(" world")
        assert d.token_count == 2

    def test_on_delta_empty_string_ignored(self):
        d = StreamDiag().start()
        d.on_delta("")
        d.on_delta("   ")  # whitespace-only — still counts (non-empty string)
        assert d.token_count == 1

    def test_ttft_set_on_first_nonempty_chunk(self):
        d = StreamDiag().start()
        d.on_delta("  ")   # whitespace only — no TTFT yet (strip check)
        assert d.ttft_ms is None
        d.on_delta("hello")
        assert d.ttft_ms is not None
        assert d.ttft_ms >= 0

    def test_ttft_not_overwritten_on_subsequent_tokens(self):
        d = StreamDiag().start()
        d.on_delta("first")
        first_ttft = d.ttft_ms
        d.on_delta("second")
        assert d.ttft_ms == first_ttft

    def test_tokens_per_second_positive(self):
        d = StreamDiag()
        d.token_count = 10
        d.elapsed_total_ms = 1000.0  # 10 tok/s
        assert d.tokens_per_second == pytest.approx(10.0)

    def test_has_data_after_tokens(self):
        d = StreamDiag().start()
        assert d.has_data() is False
        d.on_delta("x")
        assert d.has_data() is True

    def test_summary_line_contains_tok_count(self):
        d = StreamDiag().start()
        for _ in range(5):
            d.on_delta("word")
        line = d.summary_line()
        assert "5 tok" in line

    def test_summary_line_contains_tps(self):
        d = StreamDiag()
        d.token_count = 3
        d.elapsed_total_ms = 500.0  # 6 tok/s
        line = d.summary_line()
        assert "tok/s" in line

    def test_summary_line_contains_ttft(self):
        d = StreamDiag().start()
        d.on_delta("hello world")
        line = d.summary_line()
        assert "TTFT" in line

    def test_summary_line_prefix(self):
        d = StreamDiag().start()
        d.on_delta("x")
        assert d.summary_line().startswith("  ↳")

    def test_on_round_resets_state(self):
        d = StreamDiag().start()
        d.on_delta("hello")
        assert d.token_count == 1
        d.on_round()
        assert d.token_count == 0
        assert d.ttft_ms is None

    def test_on_delta_without_explicit_start_still_works(self):
        d = StreamDiag()
        d.on_delta("late start")
        assert d.token_count == 1

    def test_extra_meta_kwargs_ignored(self):
        d = StreamDiag().start()
        d.on_delta("chunk", token_index=0, elapsed_ms=5.0, extra="ignored")
        assert d.token_count == 1

    def test_on_tool_does_not_raise(self):
        d = StreamDiag()
        d.on_tool("some_tool")  # should be a no-op


# ─── StreamDiag as delta sink ────────────────────────────────────────────────

class TestStreamDiagAsSink:
    def test_diag_works_as_delta_sink(self):
        d = StreamDiag().start()
        token = set_sink(d)
        try:
            emit_delta("hello")
            emit_delta(" world")
        finally:
            reset_sink(token)
        assert d.token_count == 2

    def test_diag_composable_with_another_sink(self):
        collected: list[str] = []

        class CollectSink:
            def on_delta(self, chunk: str) -> None:
                collected.append(chunk)

        class ComposedSink:
            def __init__(self, *sinks):
                self._sinks = sinks

            def on_delta(self, chunk: str) -> None:
                for s in self._sinks:
                    s.on_delta(chunk)

        diag = StreamDiag().start()
        composed = ComposedSink(diag, CollectSink())
        token = set_sink(composed)
        try:
            emit_delta("A")
            emit_delta("B")
        finally:
            reset_sink(token)

        assert diag.token_count == 2
        assert collected == ["A", "B"]


# ─── Summary line format validation ──────────────────────────────────────────

class TestSummaryLineFormat:
    def test_zero_tokens_still_returns_string(self):
        d = StreamDiag()
        line = d.summary_line()
        assert isinstance(line, str)
        assert "0 tok" in line

    def test_single_token_no_tps(self):
        d = StreamDiag()
        d._t0 = time.monotonic()
        d.token_count = 1
        d.elapsed_total_ms = 0.0  # avoid division
        line = d.summary_line()
        assert "1 tok" in line

    def test_ttft_missing_not_in_line(self):
        d = StreamDiag()
        d.token_count = 3
        line = d.summary_line()
        assert "TTFT" not in line
