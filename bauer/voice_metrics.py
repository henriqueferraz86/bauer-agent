"""Métricas de latência e ciclo de vida de um turno de voz."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any


class VoiceTurnMetrics:
    """Relógio thread-safe para as etapas do pipeline de voz.

    As marcações são relativas ao início do turno e preservam apenas a
    primeira ocorrência de cada etapa. O payload é deliberadamente simples
    para poder ser enviado ao EventBus, a hooks ou apenas ao log.
    """

    _DURATION_PAIRS = {
        "stt_ms": ("stt_start", "stt_end"),
        "llm_to_first_delta_ms": ("llm_start", "llm_first_delta"),
        "llm_ms": ("llm_start", "llm_end"),
        "tts_synthesis_ms": ("tts_synthesis_start", "tts_synthesis_end"),
        "tts_playback_ms": ("tts_playback_start", "tts_playback_end"),
    }

    def __init__(self, *, turn_id: str | None = None) -> None:
        self.turn_id = turn_id or uuid.uuid4().hex[:12]
        self.started_at = time.perf_counter()
        self._marks: dict[str, float] = {}
        self._finished = False
        self._lock = threading.Lock()

    def mark(self, stage: str) -> None:
        """Registra a primeira ocorrência de uma etapa."""
        name = str(stage).strip()
        if not name:
            return
        with self._lock:
            self._marks.setdefault(name, time.perf_counter())

    def snapshot(self, *, status: str = "running", error: str | None = None) -> dict[str, Any]:
        """Retorna métricas serializáveis sem encerrar o turno."""
        with self._lock:
            marks = dict(self._marks)
            finished = self._finished
        now = time.perf_counter()
        relative_marks = {
            name: round((stamp - self.started_at) * 1000, 3)
            for name, stamp in marks.items()
        }
        durations: dict[str, float] = {}
        for label, (start, end) in self._DURATION_PAIRS.items():
            if start in marks and end in marks:
                durations[label] = round((marks[end] - marks[start]) * 1000, 3)
        payload: dict[str, Any] = {
            "turn_id": self.turn_id,
            "status": status,
            "finished": finished,
            "total_ms": round((now - self.started_at) * 1000, 3),
            "marks_ms": relative_marks,
            "durations_ms": durations,
        }
        if error:
            payload["error"] = str(error)
        return payload

    def finish(
        self,
        *,
        status: str = "completed",
        error: str | None = None,
        publish: Callable[[dict[str, Any]], Any] | None = None,
    ) -> dict[str, Any]:
        """Fecha o turno e opcionalmente publica seu payload."""
        self.mark("turn_end")
        with self._lock:
            self._finished = True
        payload = self.snapshot(status=status, error=error)
        if publish is not None:
            try:
                publish(payload)
            except Exception as exc:
                # Telemetria nunca pode quebrar a resposta de voz.
                from .logging_config import log_suppressed

                log_suppressed("voice.metrics.publish", exc)
        return payload
