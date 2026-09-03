"""Backend opcional de wake word acústica para a sessão Jarvis.

O módulo define um contrato pequeno para modelos como openWakeWord sem tornar
essa dependência obrigatória no extra de voz. O backend só é ativado quando o
usuário configura explicitamente ``BAUER_WAKE_BACKEND=acoustic``.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
import tempfile
from typing import Any

logger = logging.getLogger("bauer.voice_acoustic_wake")


class AcousticWakeWordUnavailable(RuntimeError):
    """Modelo, microfone ou dependência acústica indisponível."""


class OpenWakeWordBackend:
    """Adaptador lazy para um modelo openWakeWord instalado pelo usuário."""

    def __init__(self, model_name: str) -> None:
        try:
            from openwakeword.model import Model
        except ImportError as exc:
            raise AcousticWakeWordUnavailable(
                "backend acústico requer openwakeword; instale-o separadamente"
            ) from exc
        try:
            self._model = Model(wakeword_models=[model_name])
        except Exception as exc:
            raise AcousticWakeWordUnavailable(
                f"não foi possível carregar o modelo acústico '{model_name}': {exc}"
            ) from exc

    def score(self, samples: Any) -> float:
        predictions = self._model.predict(samples)
        if isinstance(predictions, Mapping):
            values: list[float] = []
            for value in predictions.values():
                try:
                    values.append(float(value))
                except (TypeError, ValueError):
                    try:
                        values.append(float(max(value)))
                    except (TypeError, ValueError):
                        continue
            return max(values, default=0.0)
        try:
            return float(predictions)
        except (TypeError, ValueError):
            return 0.0


def acoustic_backend_configured() -> bool:
    """Indica se o modo acústico foi solicitado explicitamente."""
    return os.environ.get("BAUER_WAKE_BACKEND", "").strip().lower() in {
        "acoustic",
        "openwakeword",
    }


def build_acoustic_backend() -> OpenWakeWordBackend:
    """Carrega o backend configurado sem importar o modelo no boot."""
    model_name = os.environ.get("BAUER_WAKE_MODEL", "").strip()
    if not model_name:
        raise AcousticWakeWordUnavailable(
            "defina BAUER_WAKE_MODEL com o nome ou caminho do modelo acústico"
        )
    return OpenWakeWordBackend(model_name)


class MicrophoneWakeWordMonitor:
    """Monitora frames de microfone até detectar uma pontuação acima do limiar."""

    def __init__(
        self,
        backend: Any,
        *,
        sample_rate: int = 16000,
        frame_duration_s: float = 0.08,
        threshold: float = 0.5,
        cooldown_s: float = 1.0,
        on_detected: Callable[[], None] | None = None,
    ) -> None:
        self.backend = backend
        self.sample_rate = sample_rate
        self.frame_duration_s = frame_duration_s
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.on_detected = on_detected
        self._stream: Any = None
        self._numpy: Any = None
        self._detected = threading.Event()
        self._lock = threading.Lock()
        self._last_detected = 0.0

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
            raise AcousticWakeWordUnavailable(
                "wake word acústica requer sounddevice e numpy"
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
                dtype="int16",
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise AcousticWakeWordUnavailable(
                f"não foi possível iniciar o microfone acústico: {exc}"
            ) from exc

    def wait(self, timeout_s: float | None = None) -> bool:
        """Aguarda o gatilho; retorna false em timeout ou parada."""
        return self._detected.wait(timeout_s)

    def stop(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.debug("falha ao fechar monitor acústico", exc_info=True)

    def _on_audio(self, indata: Any, _frames: int, _time_info: Any, _status: Any) -> None:
        if self._detected.is_set():
            return
        try:
            samples = self._numpy.asarray(indata, dtype="int16").reshape(-1)
            score = float(self.backend.score(samples))
            now = time.monotonic()
            if score < self.threshold or now - self._last_detected < self.cooldown_s:
                return
            self._last_detected = now
            self._detected.set()
            if self.on_detected is not None:
                self.on_detected()
        except Exception:
            logger.debug("erro no callback de wake word acústica", exc_info=True)


def capture_acoustic_command(
    backend: Any,
    *,
    duration_max_s: int = 120,
    silence_duration_s: float = 0.8,
    threshold: float = 0.5,
    threshold_db: float = -40.0,
    sample_rate: int = 16000,
    console: Any = None,
    metrics: Any = None,
) -> str | None:
    """Detecta wake word e transcreve o comando no mesmo stream de áudio.

    Antes do gatilho, os frames são apenas avaliados pelo backend acústico.
    Depois do gatilho, a gravação continua até silêncio e é enviada ao mesmo
    transcritor usado pelo Bauer. Assim, uma frase única como ``Bauer, abra o
    navegador`` não perde o comando durante a troca de ``InputStream``.
    """
    if not MicrophoneWakeWordMonitor.available():
        raise AcousticWakeWordUnavailable(
            "captura acústica requer sounddevice e numpy"
        )
    try:
        import numpy as np
        import sounddevice as sd
        import soundfile
        from .transcription import transcribe_audio
        from .voice_vad import EnergyVAD, VOICE_FINISHED
    except ImportError as exc:
        raise AcousticWakeWordUnavailable(
            "captura acústica requer numpy, sounddevice, soundfile e transcrição"
        ) from exc

    blocksize = max(1, int(sample_rate * 0.08))
    vad = EnergyVAD(
        threshold_db=threshold_db,
        min_voice_duration_s=0.15,
        silence_duration_s=silence_duration_s,
    )
    finished = threading.Event()
    frames: list[Any] = []
    triggered = False

    def on_audio(indata: Any, frame_count: int, _time_info: Any, _status: Any) -> None:
        nonlocal triggered
        try:
            samples = np.asarray(indata, dtype="int16").reshape(-1).copy()
            if not triggered:
                score = float(backend.score(samples))
                if score < threshold:
                    return
                triggered = True
                if metrics is not None:
                    metrics.mark("wake_detected")
                if console is not None:
                    console.print("[dim]Wake word detectada; grave seu comando.[/dim]")
            frames.append(samples)
            rms = float(np.sqrt(np.mean((samples.astype(np.float32) / 32768.0) ** 2)))
            level_db = 20.0 * np.log10(rms + 1e-9)
            events = vad.process_level(
                float(level_db),
                frame_count / sample_rate if frame_count else 0.08,
            )
            if VOICE_FINISHED in events:
                finished.set()
        except Exception:
            logger.debug("erro ao capturar comando acústico", exc_info=True)

    if console is not None:
        console.print("[dim]Aguardando wake word acústica...[/dim]")
    try:
        with sd.InputStream(
            samplerate=sample_rate,
            channels=1,
            blocksize=blocksize,
            dtype="int16",
            callback=on_audio,
        ):
            deadline = time.monotonic() + max(1, duration_max_s)
            while time.monotonic() < deadline and not finished.wait(0.05):
                pass
    except KeyboardInterrupt:
        return None
    except Exception as exc:
        raise AcousticWakeWordUnavailable(
            f"não foi possível capturar comando acústico: {exc}"
        ) from exc

    if not triggered or not frames:
        return None
    fd, raw_path = tempfile.mkstemp(prefix="bauer-acoustic-", suffix=".wav")
    os.close(fd)
    Path(raw_path).unlink(missing_ok=True)
    path = Path(raw_path)
    try:
        audio = np.concatenate(frames)
        soundfile.write(str(path), audio, sample_rate, subtype="PCM_16")
        result = transcribe_audio(path)
        if not result.get("success"):
            logger.warning("transcrição do comando acústico falhou: %s", result.get("error"))
            return None
        if metrics is not None:
            metrics.mark("stt_end")
        return str(result.get("transcript", "")).strip() or None
    finally:
        with suppress(OSError):
            path.unlink()
