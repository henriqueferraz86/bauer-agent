"""STT incremental para a sessão de voz do Bauer.

O módulo transforma blocos de áudio em pequenos segmentos transcritos por um
worker dedicado. Cada segmento publica um ``STT_PARTIAL`` acumulado; ao fechar
a fala, o texto final é publicado como ``STT_FINAL``. O provider continua sendo
o mesmo ``transcribe_audio`` usado pelo fluxo legado, portanto cloud e
faster-whisper permanecem intercambiáveis.
"""

from __future__ import annotations

import logging
import os
import queue
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger("bauer.voice_stt_stream")

STT_PARTIAL = "STT_PARTIAL"
STT_FINAL = "STT_FINAL"

try:
    import numpy as np  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001 - dependência opcional de voz
    np = None  # type: ignore[assignment]


class StreamingSTTUnavailable(RuntimeError):
    """Dependência necessária para transcrição incremental não disponível."""


def _normalized_words(text: str) -> list[str]:
    return [word.strip(".,!?;:()[]{}\"'“”‘’").casefold() for word in text.split()]


def merge_transcript(previous: str, current: str) -> str:
    """Une segmentos sem repetir palavras na fronteira entre eles."""
    left = str(previous or "").strip()
    right = str(current or "").strip()
    if not left:
        return right
    if not right:
        return left

    left_words = left.split()
    right_words = right.split()
    left_normalized = _normalized_words(left)
    right_normalized = _normalized_words(right)
    if right_normalized[: len(left_normalized)] == left_normalized:
        return right
    if left_normalized[-len(right_normalized) :] == right_normalized:
        return left

    max_overlap = min(12, len(left_normalized), len(right_normalized))
    for size in range(max_overlap, 0, -1):
        if left_normalized[-size:] == right_normalized[:size]:
            return " ".join(left_words + right_words[size:])
    return f"{left} {right}"


def _write_audio(samples: Any, sample_rate: int) -> Path:
    if np is None:
        raise StreamingSTTUnavailable("numpy não instalado; rode `uv sync --extra voice`")
    try:
        import soundfile
    except ImportError as exc:
        raise StreamingSTTUnavailable(
            "soundfile não instalado; rode `uv sync --extra voice`"
        ) from exc

    fd, raw_path = tempfile.mkstemp(prefix="bauer-stt-", suffix=".wav")
    os.close(fd)
    path = Path(raw_path)
    try:
        soundfile.write(str(path), np.asarray(samples, dtype=np.float32), sample_rate)
    except Exception:
        try:
            path.unlink()
        except OSError as exc:
            logger.debug("temporary STT file cleanup failed: %s", exc)
        raise
    return path


class StreamingSTTSession:
    """Recebe frames sem bloquear o produtor e transcreve em segundo plano."""

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        segment_duration_s: float = 1.5,
        transcriber: Callable[[Path], dict[str, Any]] | None = None,
        on_partial: Callable[[str], None] | None = None,
        on_final: Callable[[str], None] | None = None,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        if np is None:
            raise StreamingSTTUnavailable(
                "numpy não instalado; rode `uv sync --extra voice`"
            )
        if sample_rate <= 0 or segment_duration_s <= 0:
            raise ValueError("sample_rate e segment_duration_s devem ser positivos")
        self.sample_rate = sample_rate
        self.segment_samples = max(1, int(sample_rate * segment_duration_s))
        self._transcriber = transcriber or self._default_transcriber
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_event = on_event
        self._queue: queue.Queue[tuple[Any, bool] | None] = queue.Queue()
        self._buffer = np.empty(0, dtype=np.float32)
        self._lock = threading.Lock()
        self._closed = False
        self._finished = False
        self._transcript = ""
        self._error: Exception | None = None
        self._worker = threading.Thread(
            target=self._consume,
            name="bauer-stt-stream",
            daemon=True,
        )
        self._worker.start()

    @staticmethod
    def _default_transcriber(path: Path) -> dict[str, Any]:
        from .transcription import transcribe_audio

        return transcribe_audio(path)

    @property
    def transcript(self) -> str:
        with self._lock:
            return self._transcript

    @property
    def error(self) -> Exception | None:
        return self._error

    def push_frame(self, frame: Any) -> None:
        """Adiciona um frame e enfileira segmentos completos quando possível."""
        if np is None:
            raise StreamingSTTUnavailable("numpy não disponível")
        samples = np.asarray(frame, dtype=np.float32).reshape(-1)
        if not len(samples):
            return
        with self._lock:
            if self._closed:
                raise RuntimeError("sessão STT já foi encerrada")
            self._buffer = np.concatenate((self._buffer, samples))
            while len(self._buffer) >= self.segment_samples:
                segment = self._buffer[: self.segment_samples].copy()
                self._buffer = self._buffer[self.segment_samples :]
                self._queue.put((segment, False))

    def finish(self) -> str:
        """Transcreve o restante, espera o worker e publica ``STT_FINAL``."""
        with self._lock:
            if self._finished:
                return self._transcript
            self._closed = True
            if len(self._buffer):
                self._queue.put((self._buffer.copy(), True))
                self._buffer = np.empty(0, dtype=np.float32)
            self._queue.put(None)
        self._worker.join(timeout=120)
        if self._worker.is_alive():
            self._error = StreamingSTTUnavailable("worker STT não terminou no tempo esperado")
        self._finished = True
        final_text = self.transcript
        self._emit_event(STT_FINAL, final_text)
        if self._on_final is not None:
            try:
                self._on_final(final_text)
            except Exception:
                logger.debug("callback STT final falhou", exc_info=True)
        return final_text

    def _consume(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            samples, _is_final = item
            path: Path | None = None
            try:
                path = _write_audio(samples, self.sample_rate)
                result = self._transcriber(path)
                if result.get("success"):
                    text = str(result.get("transcript") or "").strip()
                    if text:
                        with self._lock:
                            self._transcript = merge_transcript(self._transcript, text)
                            partial = self._transcript
                        if self._on_partial is not None:
                            try:
                                self._on_partial(partial)
                            except Exception:
                                logger.debug("callback STT parcial falhou", exc_info=True)
                        self._emit_event(STT_PARTIAL, partial)
                elif not self._error:
                    self._error = StreamingSTTUnavailable(
                        str(result.get("error") or "transcrição incremental falhou")
                    )
            except Exception as exc:  # noqa: BLE001 - STT é fallback acessório
                self._error = exc
                logger.debug("segmento STT falhou", exc_info=True)
            finally:
                if path is not None:
                    try:
                        path.unlink()
                    except OSError:
                        logger.debug("não foi possível remover segmento STT: %s", path)

    def _emit_event(self, event: str, text: str) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event, text)
        except Exception:
            logger.debug("callback de evento STT falhou", exc_info=True)


def capture_voice_input_streaming(
    *,
    duration_max_s: int = 120,
    silence_threshold_db: float = -40.0,
    silence_duration_s: float = 0.8,
    sample_rate: int = 16000,
    segment_duration_s: float = 1.5,
    console: Any = None,
    on_partial: Callable[[str], None] | None = None,
) -> str | None:
    """Captura microfone e publica transcrições parciais durante a fala."""
    if np is None:
        raise StreamingSTTUnavailable("numpy não instalado; rode `uv sync --extra voice`")
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise StreamingSTTUnavailable(
            "sounddevice não instalado; rode `uv sync --extra voice`"
        ) from exc

    from .voice_vad import EnergyVAD, VOICE_FINISHED

    if console is not None:
        console.print("[cyan]🎤 STT streaming ativo; fale agora.[/cyan]")
    vad = EnergyVAD(
        threshold_db=silence_threshold_db,
        silence_duration_s=silence_duration_s,
    )
    session = StreamingSTTSession(
        sample_rate=sample_rate,
        segment_duration_s=segment_duration_s,
        on_partial=on_partial,
    )
    block_size = max(1, int(sample_rate * 0.1))
    max_blocks = max(1, int(duration_max_s * sample_rate / block_size))
    saw_voice = False
    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            blocksize=block_size,
            dtype="float32",
        ) as stream:
            for _ in range(max_blocks):
                chunk, _overflowed = stream.read(block_size)
                samples = np.asarray(chunk, dtype=np.float32).reshape(-1)
                session.push_frame(samples)
                rms = float(np.sqrt(np.mean(samples**2))) if len(samples) else 0.0
                events = vad.process_level(
                    20.0 * np.log10(rms + 1e-9),
                    len(samples) / sample_rate if len(samples) else 0.1,
                )
                saw_voice = saw_voice or bool(vad.speaking)
                if VOICE_FINISHED in events and saw_voice:
                    break
    except KeyboardInterrupt:
        logger.info("captura STT streaming cancelada")
    finally:
        text = session.finish()
    if not saw_voice or not text:
        return None
    return text
