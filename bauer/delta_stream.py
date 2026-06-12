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

from contextvars import ContextVar
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
