"""Rastreamento distribuído opcional via Langfuse.

Quando langfuse não está instalado (ou está desabilitado na config), todos os
objetos de trace/span são no-ops — sem overhead, sem erros de import.

Uso típico::

    from bauer.tracing import get_tracer
    tracer = get_tracer(cfg)
    trace = tracer.trace("run_one_turn", session_id=session_id)
    with trace.span("tool:web_search", input={"q": query}) as span:
        result = router.execute(action)
        span.end(output=result[:200])
    tracer.flush()
"""

from __future__ import annotations

import os
import time
from typing import Any


# ---------------------------------------------------------------------------
# No-op stubs (sem langfuse ou tracing desabilitado)
# ---------------------------------------------------------------------------

class _NoopSpan:
    def end(self, *, output: Any = None, level: str | None = None) -> None:
        pass

    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class _NoopTrace:
    def span(self, name: str, *, input: Any = None) -> _NoopSpan:
        return _NoopSpan()

    def generation(
        self,
        name: str,
        *,
        model: str | None = None,
        input: Any = None,
    ) -> _NoopSpan:
        return _NoopSpan()

    def update(self, **kw: Any) -> None:
        pass

    def flush(self) -> None:
        pass


class _NoopTracer:
    def trace(
        self,
        name: str,
        *,
        session_id: str | None = None,
        **kw: Any,
    ) -> _NoopTrace:
        return _NoopTrace()

    def flush(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Wrappers reais (quando langfuse está instalado)
# ---------------------------------------------------------------------------

class _LangfuseSpan:
    def __init__(self, span: Any) -> None:
        self._s = span
        self._t0 = time.monotonic()

    def end(self, *, output: Any = None, level: str | None = None) -> None:
        kw: dict[str, Any] = {}
        if output is not None:
            kw["output"] = str(output)[:2000]
        if level is not None:
            kw["level"] = level
        try:
            self._s.end(**kw)
        except Exception:
            pass

    def __enter__(self) -> "_LangfuseSpan":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, *_: Any) -> None:
        level = "ERROR" if exc_type is not None else None
        output = str(exc_val)[:200] if exc_val is not None else None
        self.end(output=output, level=level)


class _LangfuseTrace:
    def __init__(self, lf_trace: Any) -> None:
        self._t = lf_trace

    def span(self, name: str, *, input: Any = None) -> _LangfuseSpan:
        try:
            s = self._t.span(name=name, input=input)
            return _LangfuseSpan(s)
        except Exception:
            return _LangfuseSpan(_NoopSpan())  # type: ignore[arg-type]

    def generation(
        self,
        name: str,
        *,
        model: str | None = None,
        input: Any = None,
    ) -> _LangfuseSpan:
        try:
            kw: dict[str, Any] = {"name": name, "input": input}
            if model:
                kw["model"] = model
            g = self._t.generation(**kw)
            return _LangfuseSpan(g)
        except Exception:
            return _LangfuseSpan(_NoopSpan())  # type: ignore[arg-type]

    def update(self, **kw: Any) -> None:
        try:
            self._t.update(**kw)
        except Exception:
            pass

    def flush(self) -> None:
        pass


# ---------------------------------------------------------------------------
# BauerTracer — ponto de entrada público
# ---------------------------------------------------------------------------

class BauerTracer:
    """Wrapper fino sobre Langfuse com fallback no-op.

    Instanciar uma vez no startup e compartilhar — o objeto Langfuse interno
    gerencia o flush assíncrono automaticamente.
    """

    def __init__(self, cfg: Any = None) -> None:
        self._lf: Any = None
        self._enabled = False

        if cfg is None:
            return

        obs = getattr(cfg, "observability", None)
        if obs is None or not getattr(obs, "langfuse_enabled", False):
            return

        public_key = getattr(obs, "langfuse_public_key", "") or os.environ.get(
            "LANGFUSE_PUBLIC_KEY", ""
        )
        secret_key = getattr(obs, "langfuse_secret_key", "") or os.environ.get(
            "LANGFUSE_SECRET_KEY", ""
        )
        host = getattr(obs, "langfuse_host", "https://cloud.langfuse.com")

        if not public_key or not secret_key:
            return

        try:
            import langfuse  # noqa: PLC0415

            self._lf = langfuse.Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host,
            )
            self._enabled = True
        except ImportError:
            pass
        except Exception:
            pass

    @property
    def enabled(self) -> bool:
        return self._enabled

    def trace(
        self,
        name: str,
        *,
        session_id: str | None = None,
        **kw: Any,
    ) -> "_LangfuseTrace | _NoopTrace":
        if not self._enabled or self._lf is None:
            return _NoopTrace()
        try:
            t = self._lf.trace(name=name, session_id=session_id, **kw)
            return _LangfuseTrace(t)
        except Exception:
            return _NoopTrace()

    def flush(self) -> None:
        if self._enabled and self._lf is not None:
            try:
                self._lf.flush()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_default_tracer: BauerTracer | None = None


def get_tracer(cfg: Any = None) -> BauerTracer:
    """Retorna o BauerTracer singleton.

    Na primeira chamada inicializa com `cfg`. Chamadas posteriores retornam
    o mesmo objeto (ignorando cfg) — chame reset_tracer() em testes.
    """
    global _default_tracer
    if _default_tracer is None:
        _default_tracer = BauerTracer(cfg)
    return _default_tracer


def reset_tracer() -> None:
    """Reseta o singleton — usado em testes."""
    global _default_tracer
    _default_tracer = None
