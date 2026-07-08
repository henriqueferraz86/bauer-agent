"""Delta sink — streaming de progresso do agent loop para os canais.

O agent loop é sync e profundo (run_one_turn → _collect_response /
_run_native_tool_turn). Em vez de passar callback por toda a cadeia, um
ContextVar carrega o sink da thread do turno; o gateway o instala antes de
chamar run_one_turn e remove no fim.

Protocolo do sink (todos os métodos opcionais):
  - ``on_delta(chunk: str)`` — token de texto (modo Tool Bridge / stream)
  - ``on_round()`` — nova chamada ao LLM começou (finalizar segmento)
  - ``on_tool(name: str)`` — tool prestes a executar (mostrar progresso)

Emissores nunca levantam: streaming é cosmético — um sink quebrado não pode
derrubar o turno.
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

_sink: ContextVar[Any] = ContextVar("bauer_delta_sink", default=None)


def set_sink(sink: Any):
    """Instala o sink na thread/contexto atual. Retorna token p/ reset."""
    return _sink.set(sink)


def reset_sink(token) -> None:
    try:
        _sink.reset(token)
    except Exception:  # noqa: BLE001
        _sink.set(None)


def get_sink() -> Any:
    return _sink.get()


def emit_delta(chunk: str) -> None:
    sink = _sink.get()
    if sink is None or not chunk:
        return
    try:
        handler = getattr(sink, "on_delta", None) or (sink if callable(sink) else None)
        if handler:
            handler(chunk)
    except Exception:  # noqa: BLE001 — sink quebrado não derruba o turno
        pass


def emit_round_start() -> None:
    sink = _sink.get()
    if sink is None:
        return
    try:
        handler = getattr(sink, "on_round", None)
        if handler:
            handler()
    except Exception:  # noqa: BLE001
        pass


# ── G6: per-token stream diagnostics ─────────────────────────────────────────

@dataclass
class TokenEvent:
    """Metadata emitted alongside each streamed token."""
    text: str
    token_index: int
    elapsed_ms: float
    is_first: bool = False


class StreamDiag:
    """Accumulates per-token timing metrics for a single streaming turn.

    Usage:
        diag = StreamDiag()
        diag.start()
        for chunk in client.chat_stream(...):
            diag.on_delta(chunk)
        print(diag.summary_line())
    """

    def __init__(self) -> None:
        self._t0: float | None = None
        self.token_count: int = 0
        self.ttft_ms: float | None = None
        self.elapsed_total_ms: float = 0.0

    def start(self) -> "StreamDiag":
        """Mark stream start. Call immediately before the first token arrives."""
        self._t0 = time.monotonic()
        return self

    def on_delta(self, chunk: str, **_meta: Any) -> None:
        """Record a text chunk. Safe to call with empty strings."""
        if not chunk:
            return
        now = time.monotonic()
        if self._t0 is None:
            self._t0 = now
        elapsed = (now - self._t0) * 1000.0
        if self.ttft_ms is None and chunk.strip():
            self.ttft_ms = elapsed
        self.elapsed_total_ms = elapsed
        self.token_count += 1

    def on_round(self) -> None:
        """Called when a new LLM round starts — reset timing."""
        self._t0 = time.monotonic()
        self.token_count = 0
        self.ttft_ms = None
        self.elapsed_total_ms = 0.0

    def on_tool(self, name: str) -> None:
        pass

    @property
    def tokens_per_second(self) -> float:
        if self.elapsed_total_ms <= 0 or self.token_count == 0:
            return 0.0
        return self.token_count / (self.elapsed_total_ms / 1000.0)

    def summary_line(self) -> str:
        """Return a one-line dim summary string suitable for terminal display.

        Example: "  ↳ 234 tok | 45.2 tok/s | TTFT 1.23s"
        """
        parts: list[str] = [f"{self.token_count} tok"]
        tps = self.tokens_per_second
        if tps > 0:
            parts.append(f"{tps:.1f} tok/s")
        if self.ttft_ms is not None:
            parts.append(f"TTFT {self.ttft_ms / 1000:.2f}s")
        return "  ↳ " + " | ".join(parts)

    def has_data(self) -> bool:
        return self.token_count > 0


def emit_tool(name: str) -> None:
    sink = _sink.get()
    if sink is None or not name:
        return
    try:
        handler = getattr(sink, "on_tool", None)
        if handler:
            handler(name)
    except Exception:  # noqa: BLE001
        pass
