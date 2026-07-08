"""OpenTelemetry-style tracing para o Bauer Agent.

Implementação leve e zero-dep que:
1. Rastreia spans LLM / tool / session com timestamps e atributos
2. Exporta para OTLP HTTP (quando configurado) ou JSONL local
3. Fica totalmente desligado (zero overhead) quando OTEL_DISABLED=true

Compatível com o formato OTLP JSON para integração futura com Jaeger/Tempo/Grafana.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DISABLED_ENV = "BAUER_OTEL_DISABLED"
_OTLP_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
_DEFAULT_SPANS_FILE = Path.home() / ".bauer" / "traces" / "spans.jsonl"


def _otel_disabled() -> bool:
    return os.environ.get(_DISABLED_ENV, "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Span / Trace data structures
# ---------------------------------------------------------------------------

@dataclass
class Span:
    """Representa um span de rastreamento (uma operação temporizada)."""
    trace_id: str
    span_id: str
    name: str
    kind: str = "internal"  # internal / llm / tool / session
    parent_span_id: Optional[str] = None
    start_time_ns: int = field(default_factory=lambda: time.time_ns())
    end_time_ns: Optional[int] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "ok"
    error_message: str = ""

    def end(self, error: Optional[str] = None) -> "Span":
        self.end_time_ns = time.time_ns()
        if error:
            self.status = "error"
            self.error_message = error
        return self

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        self.events.append({"name": name, "ts_ns": time.time_ns(), "attributes": attributes or {}})

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def duration_ms(self) -> Optional[float]:
        if self.end_time_ns is None:
            return None
        return (self.end_time_ns - self.start_time_ns) / 1_000_000

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": self.kind,
            "start_ns": self.start_time_ns,
            "end_ns": self.end_time_ns,
            "duration_ms": self.duration_ms(),
            "attributes": self.attributes,
            "events": self.events,
            "status": self.status,
            "error": self.error_message,
        }


# ---------------------------------------------------------------------------
# Tracer
# ---------------------------------------------------------------------------

class Tracer:
    """Cria e gerencia spans. Um tracer por serviço/componente."""

    def __init__(self, service_name: str = "bauer") -> None:
        self._service = service_name
        self._active: Dict[str, Span] = {}
        self._lock = threading.Lock()
        self._exporter = SpanExporter.default()

    def start_span(
        self,
        name: str,
        kind: str = "internal",
        parent_span_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Span:
        span = Span(
            trace_id=trace_id or _new_id(),
            span_id=_new_id(),
            name=name,
            kind=kind,
            parent_span_id=parent_span_id,
            attributes={"service": self._service, **(attributes or {})},
        )
        with self._lock:
            self._active[span.span_id] = span
        return span

    def end_span(self, span: Span, error: Optional[str] = None) -> None:
        span.end(error=error)
        with self._lock:
            self._active.pop(span.span_id, None)
        self._exporter.export(span)

    def active_spans(self) -> List[Span]:
        with self._lock:
            return list(self._active.values())


# ---------------------------------------------------------------------------
# Context manager helpers
# ---------------------------------------------------------------------------

class SpanContext:
    """Context manager que inicia e termina um span automaticamente."""

    def __init__(self, tracer: Tracer, name: str, kind: str = "internal", **attrs) -> None:
        self._tracer = tracer
        self._name = name
        self._kind = kind
        self._attrs = attrs
        self.span: Optional[Span] = None

    def __enter__(self) -> "SpanContext":
        if not _otel_disabled():
            self.span = self._tracer.start_span(self._name, kind=self._kind, attributes=self._attrs)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.span is not None:
            error = str(exc_val) if exc_val else None
            self._tracer.end_span(self.span, error=error)
        return False  # não suprime exceções


# ---------------------------------------------------------------------------
# Exporter
# ---------------------------------------------------------------------------

class SpanExporter:
    """Exporta spans para JSONL local ou OTLP HTTP."""

    def __init__(
        self,
        file_path: Optional[Path] = None,
        otlp_endpoint: Optional[str] = None,
    ) -> None:
        self._file = file_path or _DEFAULT_SPANS_FILE
        self._otlp = otlp_endpoint or os.environ.get(_OTLP_ENDPOINT_ENV)
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @classmethod
    def default(cls) -> "SpanExporter":
        return cls()

    def export(self, span: Span) -> None:
        if _otel_disabled():
            return
        self._write_jsonl(span)
        if self._otlp:
            self._send_otlp(span)

    def _write_jsonl(self, span: Span) -> None:
        try:
            with self._lock:
                with open(self._file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(span.to_dict()) + "\n")
        except Exception as exc:
            logger.debug("otel: write error: %s", exc)

    def _send_otlp(self, span: Span) -> None:
        """Envia span para endpoint OTLP HTTP (fire-and-forget em thread)."""
        endpoint = self._otlp
        payload = span.to_dict()

        def _send() -> None:
            try:
                import urllib.request
                data = json.dumps({"spans": [payload]}).encode()
                req = urllib.request.Request(
                    f"{endpoint.rstrip('/')}/v1/traces",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=2)
            except Exception as exc:
                logger.debug("otel: otlp export error: %s", exc)

        threading.Thread(target=_send, daemon=True).start()


# ---------------------------------------------------------------------------
# Session replay
# ---------------------------------------------------------------------------

def load_spans(
    file_path: Optional[Path] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Carrega spans do arquivo JSONL para replay/análise."""
    fp = file_path or _DEFAULT_SPANS_FILE
    if not fp.exists():
        return []

    results = []
    try:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    span_data = json.loads(line)
                    if trace_id and span_data.get("trace_id") != trace_id:
                        continue
                    if session_id:
                        if span_data.get("attributes", {}).get("session_id") != session_id:
                            continue
                    results.append(span_data)
                    if len(results) >= limit:
                        break
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        logger.debug("otel: load error: %s", exc)

    return results


def list_traces(file_path: Optional[Path] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """Lista traces únicos (por trace_id) do arquivo JSONL."""
    spans = load_spans(file_path, limit=limit * 10)
    seen: Dict[str, Dict[str, Any]] = {}
    for s in spans:
        tid = s.get("trace_id", "")
        if tid not in seen:
            seen[tid] = {
                "trace_id": tid,
                "root_name": s.get("name"),
                "session_id": s.get("attributes", {}).get("session_id"),
                "start_ns": s.get("start_ns"),
            }
        if len(seen) >= limit:
            break
    return sorted(seen.values(), key=lambda x: x.get("start_ns") or 0, reverse=True)


# ---------------------------------------------------------------------------
# Global tracer singleton
# ---------------------------------------------------------------------------

_tracer: Optional[Tracer] = None


def get_tracer(service_name: str = "bauer") -> Tracer:
    global _tracer
    if _tracer is None:
        _tracer = Tracer(service_name=service_name)
    return _tracer


def reset_tracer() -> None:
    global _tracer
    _tracer = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_id() -> str:
    return uuid.uuid4().hex[:16]
