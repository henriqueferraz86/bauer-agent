"""Tests for bauer.tracing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bauer.tracing import (
    BauerTracer,
    _NoopSpan,
    _NoopTrace,
    _NoopTracer,
    get_tracer,
    reset_tracer,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Garante que o singleton é limpo antes/depois de cada teste."""
    reset_tracer()
    yield
    reset_tracer()


# ---------------------------------------------------------------------------
# _NoopSpan
# ---------------------------------------------------------------------------

class TestNoopSpan:
    def test_end_does_nothing(self):
        span = _NoopSpan()
        span.end()  # must not raise

    def test_end_accepts_output_and_level(self):
        span = _NoopSpan()
        span.end(output="result", level="ERROR")  # must not raise

    def test_context_manager(self):
        with _NoopSpan() as span:
            assert isinstance(span, _NoopSpan)

    def test_context_manager_on_exception(self):
        try:
            with _NoopSpan():
                raise ValueError("test")
        except ValueError:
            pass  # exception propagated but end() was called


# ---------------------------------------------------------------------------
# _NoopTrace
# ---------------------------------------------------------------------------

class TestNoopTrace:
    def test_span_returns_noop_span(self):
        trace = _NoopTrace()
        span = trace.span("tool:read_file", input={"path": "/tmp/x"})
        assert isinstance(span, _NoopSpan)

    def test_generation_returns_noop_span(self):
        trace = _NoopTrace()
        gen = trace.generation("llm_call", model="gpt-4o", input=[])
        assert isinstance(gen, _NoopSpan)

    def test_update_does_nothing(self):
        trace = _NoopTrace()
        trace.update(output="final answer")  # must not raise

    def test_flush_does_nothing(self):
        trace = _NoopTrace()
        trace.flush()  # must not raise


# ---------------------------------------------------------------------------
# _NoopTracer
# ---------------------------------------------------------------------------

class TestNoopTracer:
    def test_trace_returns_noop_trace(self):
        tracer = _NoopTracer()
        trace = tracer.trace("run_one_turn", session_id="abc")
        assert isinstance(trace, _NoopTrace)

    def test_flush_does_nothing(self):
        tracer = _NoopTracer()
        tracer.flush()  # must not raise

    def test_trace_extra_kwargs_accepted(self):
        tracer = _NoopTracer()
        trace = tracer.trace("x", session_id=None, metadata={"model": "gpt-4o"})
        assert isinstance(trace, _NoopTrace)


# ---------------------------------------------------------------------------
# BauerTracer — no config
# ---------------------------------------------------------------------------

class TestBauerTracerNoConfig:
    def test_no_cfg_creates_disabled_tracer(self):
        tracer = BauerTracer(cfg=None)
        assert tracer.enabled is False

    def test_no_cfg_trace_returns_noop(self):
        tracer = BauerTracer(cfg=None)
        trace = tracer.trace("run_one_turn")
        assert isinstance(trace, _NoopTrace)

    def test_flush_noop_when_disabled(self):
        tracer = BauerTracer(cfg=None)
        tracer.flush()  # must not raise


# ---------------------------------------------------------------------------
# BauerTracer — config with langfuse disabled
# ---------------------------------------------------------------------------

class TestBauerTracerDisabled:
    def _make_cfg(self, enabled: bool = False, pk: str = "", sk: str = ""):
        cfg = MagicMock()
        cfg.observability.langfuse_enabled = enabled
        cfg.observability.langfuse_public_key = pk
        cfg.observability.langfuse_secret_key = sk
        cfg.observability.langfuse_host = "https://cloud.langfuse.com"
        return cfg

    def test_disabled_in_config(self):
        cfg = self._make_cfg(enabled=False)
        tracer = BauerTracer(cfg=cfg)
        assert tracer.enabled is False

    def test_enabled_but_no_keys(self):
        cfg = self._make_cfg(enabled=True, pk="", sk="")
        tracer = BauerTracer(cfg=cfg)
        assert tracer.enabled is False

    def test_disabled_trace_returns_noop(self):
        cfg = self._make_cfg(enabled=False)
        tracer = BauerTracer(cfg=cfg)
        trace = tracer.trace("x", session_id="s1")
        assert isinstance(trace, _NoopTrace)

    def test_cfg_without_observability_attr(self):
        cfg = MagicMock()
        del cfg.observability  # attribute missing
        cfg = MagicMock(spec=[])
        tracer = BauerTracer(cfg=cfg)
        assert tracer.enabled is False


# ---------------------------------------------------------------------------
# BauerTracer — langfuse installed and enabled
# ---------------------------------------------------------------------------

class TestBauerTracerEnabled:
    def _make_cfg(self):
        cfg = MagicMock()
        cfg.observability.langfuse_enabled = True
        cfg.observability.langfuse_public_key = "pk-test-abc"
        cfg.observability.langfuse_secret_key = "sk-test-xyz"
        cfg.observability.langfuse_host = "https://cloud.langfuse.com"
        return cfg

    def test_enabled_when_langfuse_installed(self):
        cfg = self._make_cfg()
        fake_lf_instance = MagicMock()

        with patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=MagicMock(return_value=fake_lf_instance))}):
            tracer = BauerTracer(cfg=cfg)
            assert tracer.enabled is True

    def test_trace_wraps_langfuse_trace(self):
        cfg = self._make_cfg()
        fake_trace = MagicMock()
        fake_lf = MagicMock()
        fake_lf.trace.return_value = fake_trace

        with patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=MagicMock(return_value=fake_lf))}):
            tracer = BauerTracer(cfg=cfg)
            trace = tracer.trace("run_one_turn", session_id="sess-1")

        # Must have called lf.trace() with right args
        fake_lf.trace.assert_called_once_with(name="run_one_turn", session_id="sess-1")
        # Returned object is not _NoopTrace
        assert not isinstance(trace, _NoopTrace)

    def test_span_on_real_trace(self):
        cfg = self._make_cfg()
        fake_span = MagicMock()
        fake_trace_obj = MagicMock()
        fake_trace_obj.span.return_value = fake_span
        fake_lf = MagicMock()
        fake_lf.trace.return_value = fake_trace_obj

        with patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=MagicMock(return_value=fake_lf))}):
            tracer = BauerTracer(cfg=cfg)
            trace = tracer.trace("t")
            span = trace.span("tool:web_search", input={"q": "hello"})
            span.end(output="result", level="DEFAULT")

        fake_trace_obj.span.assert_called_once_with(name="tool:web_search", input={"q": "hello"})
        fake_span.end.assert_called_once()

    def test_flush_calls_langfuse_flush(self):
        cfg = self._make_cfg()
        fake_lf = MagicMock()

        with patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=MagicMock(return_value=fake_lf))}):
            tracer = BauerTracer(cfg=cfg)
            tracer.flush()

        fake_lf.flush.assert_called_once()

    def test_langfuse_import_error_falls_back_to_disabled(self):
        cfg = self._make_cfg()

        with patch.dict("sys.modules", {"langfuse": None}):
            tracer = BauerTracer(cfg=cfg)

        assert tracer.enabled is False

    def test_langfuse_init_exception_falls_back_to_disabled(self):
        cfg = self._make_cfg()
        fake_module = MagicMock()
        fake_module.Langfuse.side_effect = RuntimeError("auth failed")

        with patch.dict("sys.modules", {"langfuse": fake_module}):
            tracer = BauerTracer(cfg=cfg)

        assert tracer.enabled is False


# ---------------------------------------------------------------------------
# Env var fallback for keys
# ---------------------------------------------------------------------------

class TestBauerTracerEnvKeys:
    def test_env_var_public_key_used_when_config_empty(self):
        cfg = MagicMock()
        cfg.observability.langfuse_enabled = True
        cfg.observability.langfuse_public_key = ""
        cfg.observability.langfuse_secret_key = ""
        cfg.observability.langfuse_host = "https://cloud.langfuse.com"
        fake_lf = MagicMock()

        env = {"LANGFUSE_PUBLIC_KEY": "env-pk", "LANGFUSE_SECRET_KEY": "env-sk"}
        with patch("os.environ", env):
            with patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=MagicMock(return_value=fake_lf))}):
                tracer = BauerTracer(cfg=cfg)

        assert tracer.enabled is True

    def test_env_vars_missing_and_config_empty_stays_disabled(self):
        cfg = MagicMock()
        cfg.observability.langfuse_enabled = True
        cfg.observability.langfuse_public_key = ""
        cfg.observability.langfuse_secret_key = ""
        cfg.observability.langfuse_host = "https://cloud.langfuse.com"

        with patch("os.environ", {}):
            tracer = BauerTracer(cfg=cfg)

        assert tracer.enabled is False


# ---------------------------------------------------------------------------
# get_tracer singleton
# ---------------------------------------------------------------------------

class TestGetTracer:
    def test_returns_bauer_tracer(self):
        tracer = get_tracer()
        assert isinstance(tracer, BauerTracer)

    def test_same_object_on_repeated_calls(self):
        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2

    def test_reset_tracer_clears_singleton(self):
        t1 = get_tracer()
        reset_tracer()
        t2 = get_tracer()
        assert t1 is not t2

    def test_cfg_passed_on_first_call_only(self):
        cfg = MagicMock()
        cfg.observability.langfuse_enabled = False
        get_tracer(cfg=cfg)
        # Second call with different cfg — must still return the first tracer
        t2 = get_tracer(cfg=MagicMock())
        # Both calls return the same singleton, so no AttributeError
        assert isinstance(t2, BauerTracer)


# ---------------------------------------------------------------------------
# Context manager on _LangfuseSpan via integration path
# ---------------------------------------------------------------------------

class TestLangfuseSpanContextManager:
    def test_span_context_manager_calls_end(self):
        cfg = MagicMock()
        cfg.observability.langfuse_enabled = True
        cfg.observability.langfuse_public_key = "pk"
        cfg.observability.langfuse_secret_key = "sk"
        cfg.observability.langfuse_host = "https://cloud.langfuse.com"

        fake_span = MagicMock()
        fake_trace_obj = MagicMock()
        fake_trace_obj.span.return_value = fake_span
        fake_lf = MagicMock()
        fake_lf.trace.return_value = fake_trace_obj

        with patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=MagicMock(return_value=fake_lf))}):
            tracer = BauerTracer(cfg=cfg)
            trace = tracer.trace("t")
            with trace.span("tool:x") as span:
                pass

        fake_span.end.assert_called_once()

    def test_span_context_manager_on_exception(self):
        cfg = MagicMock()
        cfg.observability.langfuse_enabled = True
        cfg.observability.langfuse_public_key = "pk"
        cfg.observability.langfuse_secret_key = "sk"
        cfg.observability.langfuse_host = "https://cloud.langfuse.com"

        fake_span = MagicMock()
        fake_trace_obj = MagicMock()
        fake_trace_obj.span.return_value = fake_span
        fake_lf = MagicMock()
        fake_lf.trace.return_value = fake_trace_obj

        with patch.dict("sys.modules", {"langfuse": MagicMock(Langfuse=MagicMock(return_value=fake_lf))}):
            tracer = BauerTracer(cfg=cfg)
            trace = tracer.trace("t")
            try:
                with trace.span("tool:boom"):
                    raise RuntimeError("oops")
            except RuntimeError:
                pass

        # end() called with level=ERROR
        call_kwargs = fake_span.end.call_args[1]
        assert call_kwargs.get("level") == "ERROR"
