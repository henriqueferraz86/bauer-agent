"""Voice Activity Detection para a sessão Bauer Jarvis.

O detector é independente do backend de STT e recebe níveis de energia por
frame. O monitor de microfone usa ``sounddevice`` apenas quando solicitado,
mantendo o import opcional e não bloqueando o loop principal.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("bauer.voice_vad")

VOICE_STARTED = "VOICE_STARTED"
VOICE_ACTIVE = "VOICE_ACTIVE"
VOICE_FINISHED = "VOICE_FINISHED"


class VoiceVADUnavailable(RuntimeError):
    """Dependência de captura não disponível para monitoramento de VAD."""


class EnergyVAD:
    """VAD simples por energia, adequado como fallback sem modelo neural."""

    def __init__(
        self,
        *,
        threshold_db: float = -35.0,
        min_voice_duration_s: float = 0.25,
        silence_duration_s: float = 0.35,
    ) -> None:
        self.threshold_db = threshold_db
        self.min_voice_duration_s = min_voice_duration_s
        self.silence_duration_s = silence_duration_s
        self._voice_duration_s = 0.0
        self._silence_duration_s = 0.0
        self._speaking = False

    @property
    def speaking(self) -> bool:
        return self._speaking

    def process_level(self, level_db: float, frame_duration_s: float) -> list[str]:
        """Processa um frame e retorna eventos de atividade vocal."""
        events: list[str] = []
        if level_db >= self.threshold_db:
            self._voice_duration_s += max(frame_duration_s, 0.0)
            self._silence_duration_s = 0.0
            if not self._speaking and self._voice_duration_s >= self.min_voice_duration_s:
                self._speaking = True
                events.append(VOICE_STARTED)
            elif self._speaking:
                events.append(VOICE_ACTIVE)
            return events

        self._voice_duration_s = 0.0
        if not self._speaking:
            return events
        self._silence_duration_s += max(frame_duration_s, 0.0)
        if self._silence_duration_s >= self.silence_duration_s:
            self._speaking = False
            self._silence_duration_s = 0.0
            events.append(VOICE_FINISHED)
        else:
            events.append(VOICE_ACTIVE)
        return events

    def flush(self) -> list[str]:
        """Fecha uma fala pendente ao encerrar o monitor."""
        if not self._speaking:
            return []
        self._speaking = False
        self._voice_duration_s = 0.0
        self._silence_duration_s = 0.0
        return [VOICE_FINISHED]


class MicrophoneVADMonitor:
    """Observa o microfone e dispara callback quando uma fala começa."""

    def __init__(
        self,
        on_event: Callable[[str], None],
        *,
        sample_rate: int = 16000,
        frame_duration_s: float = 0.02,
        threshold_db: float = -35.0,
        reference_provider: Callable[[int], Any] | None = None,
        echo_canceller: Any = None,
    ) -> None:
        self.on_event = on_event
        self.sample_rate = sample_rate
        self.frame_duration_s = frame_duration_s
        self._vad = EnergyVAD(
            threshold_db=threshold_db,
            min_voice_duration_s=0.25,
            silence_duration_s=0.35,
        )
        self._reference_provider = reference_provider
        self._echo_canceller = echo_canceller
        self._stream: Any = None
        self._numpy: Any = None
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        try:
            return (
                importlib.util.find_spec("sounddevice") is not None
                and importlib.util.find_spec("numpy") is not None
            )
        except (ImportError, ValueError):
            return False

    def start(self) -> None:
        if not self.available():
            raise VoiceVADUnavailable(
                "VAD de microfone requer sounddevice e numpy; use `uv sync --extra voice`"
            )
        import numpy as np
        import sounddevice as sd

        self._numpy = np
        blocksize = max(1, int(self.sample_rate * self.frame_duration_s))
        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                blocksize=blocksize,
                dtype="float32",
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise VoiceVADUnavailable(f"não foi possível iniciar o microfone: {exc}") from exc

    def stop(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.debug("falha ao fechar monitor VAD", exc_info=True)
        for event in self._vad.flush():
            self._emit(event)

    def _on_audio(self, indata: Any, frames: int, _time_info: Any, _status: Any) -> None:
        try:
            samples = self._numpy.asarray(indata, dtype="float32").reshape(-1)
            if self._reference_provider is not None and self._echo_canceller is not None:
                try:
                    reference = self._reference_provider(len(samples))
                    samples = self._echo_canceller.process(samples, reference)
                except Exception:
                    logger.debug("AEC falhou; usando sinal bruto para o VAD", exc_info=True)
            rms = float(self._numpy.sqrt(self._numpy.mean(samples**2)))
            level_db = 20.0 * self._numpy.log10(rms + 1e-9)
            duration = frames / self.sample_rate if frames else self.frame_duration_s
            for event in self._vad.process_level(float(level_db), duration):
                self._emit(event)
        except Exception:
            logger.debug("erro no callback VAD", exc_info=True)

    def _emit(self, event: str) -> None:
        try:
            self.on_event(event)
        except Exception:
            logger.debug("callback VAD falhou", exc_info=True)
