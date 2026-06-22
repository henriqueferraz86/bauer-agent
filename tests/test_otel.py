"""Testes de bauer/otel.py — spans, tracer, exporter, session replay."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.otel import (
    Span,
    SpanContext,
    SpanExporter,
    Tracer,
    _new_id,
    get_tracer,
    list_traces,
    load_spans,
    reset_tracer,
)


# ---------------------------------------------------------------------------
# TestSpan
# ---------------------------------------------------------------------------

class TestSpan:
    def test_span_has_ids(self):
        s = Span(trace_id="t1", span_id="s1", name="test")
        assert s.trace_id == "t1" and s.span_id == "s1"

    def test_elapsed_none_before_end(self):
        s = Span(trace_id="t", span_id="s", name="x")
        assert s.duration_ms() is None

    def test_end_sets_end_time(self):
        s = Span(trace_id="t", span_id="s", name="x")
        s.end()
        assert s.end_time_ns is not None

    def test_end_calculates_duration(self):
        s = Span(trace_id="t", span_id="s", name="x", start_time_ns=0)
        s.end_time_ns = 1_000_000  # 1ms
        assert s.duration_ms() == pytest.approx(1.0, abs=0.01)

    def test_end_with_error_sets_status(self):
        s = Span(trace_id="t", span_id="s", name="x")
        s.end(error="boom")
        assert s.status == "error"
        assert s.error_message == "boom"

    def test_end_ok_no_error(self):
        s = Span(trace_id="t", span_id="s", name="x")
        s.end()
        assert s.status == "ok"
        assert s.error_message == ""

    def test_set_attribute(self):
        s = Span(trace_id="t", span_id="s", name="x")
        s.set_attribute("model", "gpt-4")
        assert s.attributes["model"] == "gpt-4"

    def test_add_event(self):
        s = Span(trace_id="t", span_id="s", name="x")
        s.add_event("tool_call", {"tool": "read_file"})
        assert s.events[0]["name"] == "tool_call"

    def test_to_dict_has_required_keys(self):
        s = Span(trace_id="t", span_id="s", name="x")
        s.end()
        d = s.to_dict()
        assert all(k in d for k in ("trace_id", "span_id", "name", "kind", "duration_ms", "status"))

    def test_start_time_auto_set(self):
        t0 = time.time_ns()
        s = Span(trace_id="t", span_id="s", name="x")
        assert s.start_time_ns >= t0

    def test_default_kind(self):
        s = Span(trace_id="t", span_id="s", name="x")
        assert s.kind == "internal"


# ---------------------------------------------------------------------------
# TestTracer
# ---------------------------------------------------------------------------

class TestTracer:
    def setup_method(self, method):
        reset_tracer()

    def test_start_span_returns_span(self, tmp_path):
        t = Tracer(service_name="test")
        t._exporter = _mock_exporter()
        s = t.start_span("my_op")
        assert isinstance(s, Span)

    def test_start_span_sets_service(self, tmp_path):
        t = Tracer(service_name="my_svc")
        t._exporter = _mock_exporter()
        s = t.start_span("op")
        assert s.attributes.get("service") == "my_svc"

    def test_start_span_in_active(self, tmp_path):
        t = Tracer("svc")
        t._exporter = _mock_exporter()
        s = t.start_span("op")
        assert s.span_id in {sp.span_id for sp in t.active_spans()}

    def test_end_span_removes_from_active(self, tmp_path):
        t = Tracer("svc")
        t._exporter = _mock_exporter()
        s = t.start_span("op")
        t.end_span(s)
        assert s.span_id not in {sp.span_id for sp in t.active_spans()}

    def test_end_span_calls_exporter(self, tmp_path):
        t = Tracer("svc")
        exp = _mock_exporter()
        t._exporter = exp
        s = t.start_span("op")
        t.end_span(s)
        exp.export.assert_called_once()

    def test_parent_span_id_propagated(self, tmp_path):
        t = Tracer("svc")
        t._exporter = _mock_exporter()
        parent = t.start_span("parent")
        child = t.start_span("child", parent_span_id=parent.span_id)
        assert child.parent_span_id == parent.span_id

    def test_kind_propagated(self, tmp_path):
        t = Tracer("svc")
        t._exporter = _mock_exporter()
        s = t.start_span("llm_call", kind="llm")
        assert s.kind == "llm"

    def test_attributes_propagated(self, tmp_path):
        t = Tracer("svc")
        t._exporter = _mock_exporter()
        s = t.start_span("op", attributes={"model": "gpt-4"})
        assert s.attributes.get("model") == "gpt-4"


# ---------------------------------------------------------------------------
# TestSpanContext
# ---------------------------------------------------------------------------

class TestSpanContext:
    def setup_method(self, method):
        reset_tracer()

    def test_context_manager_starts_span(self):
        t = Tracer("svc")
        t._exporter = _mock_exporter()
        with SpanContext(t, "op") as ctx:
            assert ctx.span is not None
            assert isinstance(ctx.span, Span)

    def test_context_manager_ends_span(self):
        t = Tracer("svc")
        exp = _mock_exporter()
        t._exporter = exp
        with SpanContext(t, "op") as ctx:
            pass
        exp.export.assert_called_once()

    def test_exception_sets_error_status(self):
        t = Tracer("svc")
        exp = _mock_exporter()
        t._exporter = exp
        try:
            with SpanContext(t, "op") as ctx:
                raise ValueError("test error")
        except ValueError:
            pass
        span = exp.export.call_args[0][0]
        assert span.status == "error"
        assert "test error" in span.error_message

    def test_disabled_no_span(self):
        with patch.dict(os.environ, {"BAUER_OTEL_DISABLED": "true"}):
            t = Tracer("svc")
            t._exporter = _mock_exporter()
            with SpanContext(t, "op") as ctx:
                assert ctx.span is None


# ---------------------------------------------------------------------------
# TestSpanExporter
# ---------------------------------------------------------------------------

class TestSpanExporter:
    def test_writes_jsonl(self, tmp_path):
        fp = tmp_path / "spans.jsonl"
        exp = SpanExporter(file_path=fp)
        s = Span(trace_id="t", span_id="s", name="x")
        s.end()
        exp.export(s)
        lines = fp.read_text().strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["name"] == "x"

    def test_multiple_spans_append(self, tmp_path):
        fp = tmp_path / "spans.jsonl"
        exp = SpanExporter(file_path=fp)
        for i in range(3):
            s = Span(trace_id="t", span_id=f"s{i}", name=f"op{i}")
            s.end()
            exp.export(s)
        assert len(fp.read_text().strip().splitlines()) == 3

    def test_disabled_no_write(self, tmp_path):
        with patch.dict(os.environ, {"BAUER_OTEL_DISABLED": "true"}):
            fp = tmp_path / "spans.jsonl"
            exp = SpanExporter(file_path=fp)
            s = Span(trace_id="t", span_id="s", name="x")
            s.end()
            exp.export(s)
            assert not fp.exists()

    def test_creates_parent_dirs(self, tmp_path):
        fp = tmp_path / "a" / "b" / "spans.jsonl"
        exp = SpanExporter(file_path=fp)
        s = Span(trace_id="t", span_id="s", name="x")
        s.end()
        exp.export(s)
        assert fp.exists()


# ---------------------------------------------------------------------------
# TestLoadSpans
# ---------------------------------------------------------------------------

class TestLoadSpans:
    def _write_spans(self, path: Path, spans: list) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for s in spans:
                f.write(json.dumps(s) + "\n")

    def test_loads_all_spans(self, tmp_path):
        fp = tmp_path / "s.jsonl"
        self._write_spans(fp, [
            {"trace_id": "t1", "name": "a", "start_ns": 1},
            {"trace_id": "t2", "name": "b", "start_ns": 2},
        ])
        result = load_spans(file_path=fp)
        assert len(result) == 2

    def test_filter_by_trace_id(self, tmp_path):
        fp = tmp_path / "s.jsonl"
        self._write_spans(fp, [
            {"trace_id": "t1", "name": "a"},
            {"trace_id": "t2", "name": "b"},
        ])
        result = load_spans(file_path=fp, trace_id="t1")
        assert all(s["trace_id"] == "t1" for s in result)

    def test_filter_by_session_id(self, tmp_path):
        fp = tmp_path / "s.jsonl"
        self._write_spans(fp, [
            {"trace_id": "t1", "attributes": {"session_id": "sess-a"}, "name": "a"},
            {"trace_id": "t2", "attributes": {"session_id": "sess-b"}, "name": "b"},
        ])
        result = load_spans(file_path=fp, session_id="sess-a")
        assert len(result) == 1 and result[0]["name"] == "a"

    def test_respects_limit(self, tmp_path):
        fp = tmp_path / "s.jsonl"
        self._write_spans(fp, [{"trace_id": "t", "name": str(i)} for i in range(20)])
        result = load_spans(file_path=fp, limit=5)
        assert len(result) == 5

    def test_missing_file_returns_empty(self, tmp_path):
        result = load_spans(file_path=tmp_path / "nonexistent.jsonl")
        assert result == []

    def test_invalid_json_lines_skipped(self, tmp_path):
        fp = tmp_path / "s.jsonl"
        with open(fp, "w") as f:
            f.write('{"trace_id": "t1", "name": "ok"}\n')
            f.write("not json\n")
            f.write('{"trace_id": "t2", "name": "ok2"}\n')
        result = load_spans(file_path=fp)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestListTraces
# ---------------------------------------------------------------------------

class TestListTraces:
    def _write_spans(self, path: Path, spans: list) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for s in spans:
                f.write(json.dumps(s) + "\n")

    def test_deduplicates_by_trace_id(self, tmp_path):
        fp = tmp_path / "s.jsonl"
        self._write_spans(fp, [
            {"trace_id": "t1", "name": "a", "start_ns": 1},
            {"trace_id": "t1", "name": "b", "start_ns": 2},
            {"trace_id": "t2", "name": "c", "start_ns": 3},
        ])
        result = list_traces(file_path=fp)
        assert len(result) == 2
        tids = {t["trace_id"] for t in result}
        assert tids == {"t1", "t2"}

    def test_sorted_newest_first(self, tmp_path):
        fp = tmp_path / "s.jsonl"
        self._write_spans(fp, [
            {"trace_id": "t1", "name": "a", "start_ns": 100},
            {"trace_id": "t2", "name": "b", "start_ns": 200},
        ])
        result = list_traces(file_path=fp)
        assert result[0]["trace_id"] == "t2"

    def test_empty_file_returns_empty(self, tmp_path):
        result = list_traces(file_path=tmp_path / "x.jsonl")
        assert result == []


# ---------------------------------------------------------------------------
# TestGetTracer
# ---------------------------------------------------------------------------

class TestGetTracer:
    def setup_method(self, method):
        reset_tracer()

    def test_singleton(self):
        reset_tracer()
        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2

    def test_reset_clears(self):
        reset_tracer()
        t1 = get_tracer()
        reset_tracer()
        t2 = get_tracer()
        assert t1 is not t2


# ---------------------------------------------------------------------------
# TestNewId
# ---------------------------------------------------------------------------

class TestNewId:
    def test_unique(self):
        ids = {_new_id() for _ in range(100)}
        assert len(ids) == 100

    def test_hex_chars(self):
        i = _new_id()
        assert all(c in "0123456789abcdef" for c in i)

    def test_length(self):
        assert len(_new_id()) == 16


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_exporter():
    e = MagicMock()
    e.export = MagicMock()
    return e
