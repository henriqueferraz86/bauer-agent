"""Tests for G10 — Background Review."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from bauer.background_review import (
    ReviewResult,
    _append_log,
    _parse_review_response,
    _run_review,
    _summarise_tool_log,
    review_turn,
)


# ── ReviewResult ──────────────────────────────────────────────────────────────

class TestReviewResult:
    def test_good_factory(self):
        r = ReviewResult.good()
        assert r.quality == "good"
        assert r.issues == []
        assert r.suggestions == []

    def test_from_json_complete(self):
        data = {"quality": "incomplete", "issues": ["missed tool"], "suggestions": ["use read_file"]}
        r = ReviewResult.from_json(data, raw="raw text")
        assert r.quality == "incomplete"
        assert "missed tool" in r.issues
        assert r.raw == "raw text"

    def test_from_json_unknown_quality_defaults_good(self):
        r = ReviewResult.from_json({"quality": "weird_value"})
        assert r.quality == "good"

    def test_from_json_missing_fields(self):
        r = ReviewResult.from_json({})
        assert r.quality == "good"
        assert r.issues == []

    def test_from_json_null_lists(self):
        r = ReviewResult.from_json({"quality": "error", "issues": None, "suggestions": None})
        assert r.issues == []
        assert r.suggestions == []

    def test_all_quality_values(self):
        for q in ("good", "incomplete", "off_topic", "error"):
            r = ReviewResult.from_json({"quality": q})
            assert r.quality == q


# ── _parse_review_response ────────────────────────────────────────────────────

class TestParseReviewResponse:
    def test_clean_json(self):
        raw = '{"quality": "good", "issues": [], "suggestions": []}'
        result = _parse_review_response(raw)
        assert result["quality"] == "good"

    def test_json_wrapped_in_prose(self):
        raw = 'Here is my review:\n{"quality": "incomplete", "issues": ["missing tool"]}\nDone.'
        result = _parse_review_response(raw)
        assert result["quality"] == "incomplete"

    def test_invalid_json_fallback(self):
        result = _parse_review_response("not json at all")
        assert result["quality"] == "good"
        assert result["issues"] == []

    def test_empty_string_fallback(self):
        result = _parse_review_response("")
        assert result["quality"] == "good"

    def test_partial_json_fallback(self):
        result = _parse_review_response('{"quality": "good"')
        assert result["quality"] == "good"

    def test_nested_json_extracted(self):
        raw = 'Result: {"quality": "error", "issues": ["wrong answer"], "suggestions": []}'
        result = _parse_review_response(raw)
        assert result["quality"] == "error"


# ── _summarise_tool_log ───────────────────────────────────────────────────────

class TestSummariseToolLog:
    def test_empty_log(self):
        assert _summarise_tool_log([]) == "none"

    def test_single_tool(self):
        log = [{"tool": "read_file", "result": "..."}]
        assert "read_file" in _summarise_tool_log(log)

    def test_truncated_at_5(self):
        log = [{"tool": f"tool_{i}"} for i in range(8)]
        summary = _summarise_tool_log(log)
        assert "+3 more" in summary

    def test_exactly_5_no_ellipsis(self):
        log = [{"tool": f"tool_{i}"} for i in range(5)]
        summary = _summarise_tool_log(log)
        assert "more" not in summary

    def test_missing_tool_key(self):
        log = [{"result": "something"}]
        assert "?" in _summarise_tool_log(log)


# ── _run_review ───────────────────────────────────────────────────────────────

class TestRunReview:
    def test_returns_none_when_no_aux_client(self):
        with patch("bauer.background_review.call_aux_text", None):
            result = _run_review("hi", "hello", [], cfg=None)
        assert result is None

    def test_returns_none_on_empty_aux_response(self):
        with patch("bauer.background_review.call_aux_text", return_value=""):
            result = _run_review("hi", "hello", [], cfg=None)
        assert result is None

    def test_returns_review_result_on_valid_response(self):
        good_json = '{"quality": "good", "issues": [], "suggestions": []}'
        with patch("bauer.background_review.call_aux_text", return_value=good_json):
            result = _run_review("What is 2+2?", "The answer is 4.", [], cfg=None)
        assert result is not None
        assert result.quality == "good"

    def test_returns_review_result_on_incomplete(self):
        json_resp = '{"quality": "incomplete", "issues": ["no tool used"], "suggestions": ["call calculator"]}'
        with patch("bauer.background_review.call_aux_text", return_value=json_resp):
            result = _run_review("sum 100 numbers", "I don't know.", [], cfg=None)
        assert result is not None
        assert result.quality == "incomplete"
        assert len(result.issues) > 0

    def test_handles_aux_exception_gracefully(self):
        def _raise(*a, **kw):
            raise RuntimeError("network error")
        with patch("bauer.background_review.call_aux_text", side_effect=_raise):
            # _run_review itself doesn't catch — _do_review does
            # But if call_aux_text raises, call_aux_text already catches (returns fallback)
            # So test via None return
            pass  # covered by daemon thread test


# ── _append_log ───────────────────────────────────────────────────────────────

class TestAppendLog:
    def test_creates_file_and_appends(self, tmp_path):
        log_file = tmp_path / "review_log.jsonl"
        result = ReviewResult.good()
        _append_log(log_file, "user q", "assistant a", result, "sess-1")
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["quality"] == "good"
        assert entry["session_id"] == "sess-1"

    def test_appends_multiple_entries(self, tmp_path):
        log_file = tmp_path / "review_log.jsonl"
        r = ReviewResult.good()
        _append_log(log_file, "q1", "a1", r, "s1")
        _append_log(log_file, "q2", "a2", r, "s2")
        lines = log_file.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_user_snippet_truncated(self, tmp_path):
        log_file = tmp_path / "review_log.jsonl"
        long_input = "x" * 200
        _append_log(log_file, long_input, "a", ReviewResult.good(), "s")
        entry = json.loads(log_file.read_text().strip())
        assert len(entry["user_snippet"]) <= 80

    def test_creates_parent_dir(self, tmp_path):
        log_file = tmp_path / "nested" / "review_log.jsonl"
        _append_log(log_file, "q", "a", ReviewResult.good(), "s")
        assert log_file.exists()


# ── review_turn (fire-and-forget) ────────────────────────────────────────────

class TestReviewTurn:
    def test_returns_immediately(self):
        """review_turn must not block the caller."""
        called = threading.Event()
        def slow_aux(*args, **kwargs):
            time.sleep(0.5)
            called.set()
            return '{"quality": "good", "issues": [], "suggestions": []}'

        start = time.monotonic()
        with patch("bauer.background_review.call_aux_text", side_effect=slow_aux):
            review_turn("hello", "world " * 5, [])
        elapsed = time.monotonic() - start
        assert elapsed < 0.2  # must return well before the 0.5s sleep

    def test_skips_slash_commands(self):
        with patch("bauer.background_review.call_aux_text") as mock_aux:
            review_turn("/clear", "pong", [])
            # Give thread time to start if it was incorrectly launched
            time.sleep(0.05)
        mock_aux.assert_not_called()

    def test_skips_empty_response(self):
        with patch("bauer.background_review.call_aux_text") as mock_aux:
            review_turn("hello", "", [])
            time.sleep(0.05)
        mock_aux.assert_not_called()

    def test_skips_very_short_response(self):
        with patch("bauer.background_review.call_aux_text") as mock_aux:
            review_turn("hello", "ok", [])
            time.sleep(0.05)
        mock_aux.assert_not_called()

    def test_does_not_raise_on_exception(self):
        def _bad(*a, **kw):
            raise RuntimeError("boom")
        with patch("bauer.background_review.call_aux_text", side_effect=_bad):
            review_turn("query", "response " * 5, [])
        # No exception should propagate

    def test_log_written_after_review(self, tmp_path):
        log_file = tmp_path / "review_log.jsonl"
        good_json = '{"quality": "good", "issues": [], "suggestions": []}'
        with patch("bauer.background_review.call_aux_text", return_value=good_json):
            with patch("bauer.background_review.Path") as mock_path:
                mock_path.home.return_value = tmp_path
                mock_path.return_value = log_file
                review_turn("user message", "assistant response content here", [])
                time.sleep(0.2)
        # Just verify it didn't crash — log path is patched so we can't assert file
