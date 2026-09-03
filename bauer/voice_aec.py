"""Cancelamento acústico de eco para o monitor de voz.

O backend inicial é um filtro NLMS adaptativo. Ele usa como referência o
mesmo PCM enviado ao playback e remove do microfone a componente correlata,
sem depender de um driver WASAPI específico. Backends nativos de WebRTC podem
ser adicionados depois através do mesmo contrato.
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from typing import Any


class AECUnavailable(RuntimeError):
    """A referência de áudio não pôde ser preparada para o AEC."""


class PlaybackReference:
    """Fornece frames do áudio reproduzido para o filtro adaptativo."""

    def __init__(self, audio_file: str | Path, *, sample_rate: int = 16000) -> None:
        try:
            import numpy as np
        except ImportError as exc:
            raise AECUnavailable("AEC requer numpy; use `uv sync --extra voice`") from exc

        path = Path(audio_file)
        try:
            with wave.open(str(path), "rb") as wav:
                channels = wav.getnchannels()
                width = wav.getsampwidth()
                source_rate = wav.getframerate()
                raw = wav.readframes(wav.getnframes())
        except (OSError, wave.Error) as exc:
            raise AECUnavailable(f"referência de playback inválida: {exc}") from exc
        if width != 2:
            raise AECUnavailable("AEC requer WAV PCM de 16 bits")
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        if source_rate != sample_rate and len(samples) > 1:
            source_times = np.arange(len(samples), dtype=np.float32) / source_rate
            target_length = max(1, int(len(samples) * sample_rate / source_rate))
            target_times = np.arange(target_length, dtype=np.float32) / sample_rate
            samples = np.interp(target_times, source_times, samples).astype(np.float32)
        self._samples = samples
        self.sample_rate = sample_rate
        self._position = 0
        self._started = False
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            self._position = 0
            self._started = True

    def next_frame(self, frame_count: int) -> Any:
        import numpy as np

        count = max(0, int(frame_count))
        with self._lock:
            if not self._started or count == 0:
                return np.zeros(count, dtype=np.float32)
            start = self._position
            end = start + count
            self._position = end
            frame = self._samples[start:end]
        if len(frame) < count:
            frame = np.pad(frame, (0, count - len(frame)))
        return frame


class NLMSEchoCanceller:
    """Filtro NLMS mono para remover a referência de playback do microfone."""

    def __init__(self, *, filter_length: int = 256, step_size: float = 0.12) -> None:
        self.filter_length = max(8, int(filter_length))
        self.step_size = max(0.001, min(float(step_size), 1.0))
        self._weights: Any = None
        self._history: Any = None

    def process(self, microphone: Any, reference: Any) -> Any:
        import numpy as np

        mic = np.asarray(microphone, dtype=np.float32).reshape(-1)
        ref = np.asarray(reference, dtype=np.float32).reshape(-1)
        if len(ref) < len(mic):
            ref = np.pad(ref, (0, len(mic) - len(ref)))
        elif len(ref) > len(mic):
            ref = ref[:len(mic)]
        if self._weights is None:
            self._weights = np.zeros(self.filter_length, dtype=np.float32)
            self._history = np.zeros(self.filter_length, dtype=np.float32)

        cleaned = np.empty_like(mic)
        for index, sample in enumerate(mic):
            self._history[1:] = self._history[:-1]
            self._history[0] = ref[index]
            estimate = float(np.dot(self._weights, self._history))
            error = float(sample) - estimate
            power = float(np.dot(self._history, self._history)) + 1e-6
            self._weights += self.step_size * error * self._history / power
            cleaned[index] = error
        return cleaned


class AECReferenceClock:
    """Pequeno relógio útil para diagnósticos de sincronização do AEC."""

    def __init__(self) -> None:
        self.started_at: float | None = None

    def start(self) -> None:
        self.started_at = time.monotonic()

    @property
    def elapsed_s(self) -> float:
        if self.started_at is None:
            return 0.0
        return time.monotonic() - self.started_at
