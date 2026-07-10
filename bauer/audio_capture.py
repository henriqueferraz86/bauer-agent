"""Captura de áudio do microfone — integração com transcrição Whisper local.

Fluxo: gravar → detectar silêncio → transcrever → retornar texto.

Uso::

    from bauer.audio_capture import capture_voice_input
    text = capture_voice_input(duration_max_s=30, silence_threshold_db=-40)
    if text:
        print(f"Você disse: {text}")

Dependências opcionais::

    pip install sounddevice numpy  # captura de áudio
    pip install faster-whisper     # transcrição local offline
"""

from __future__ import annotations

import logging
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger("bauer.audio_capture")

try:
    import numpy as np  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001 - optional voice dependency
    np = None  # type: ignore[assignment]

try:
    import sounddevice as sd  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001 - optional voice dependency
    sd = None  # type: ignore[assignment]

try:
    from .transcription import transcribe_audio
except Exception:  # noqa: BLE001 - optional transcription path
    transcribe_audio = None  # type: ignore[assignment]


def _has_sounddevice() -> bool:
    """True se sounddevice está disponível."""
    import importlib.util
    try:
        return importlib.util.find_spec("sounddevice") is not None
    except (ImportError, ValueError):
        return False


def _has_numpy() -> bool:
    """True se numpy está disponível."""
    import importlib.util
    try:
        return importlib.util.find_spec("numpy") is not None
    except (ImportError, ValueError):
        return False


def capture_voice_input(
    duration_max_s: int = 120,
    silence_threshold_db: float = -40.0,
    silence_duration_s: float = 5.0,
    sample_rate: int = 16000,
    console: Any = None,
) -> str | None:
    """Grava áudio do microfone até silêncio ou timeout, transcreve com Whisper.

    Args:
        duration_max_s: tempo máximo de gravação (segundos)
        silence_threshold_db: nível de amplitude em dB para considerar silêncio
        silence_duration_s: quantos segundos de silêncio para parar a gravação
        sample_rate: Hz
        console: console Rich (para output amigável)

    Retorna o texto transcrito ou None se falhar/cancelar.

    Levanta ImportError se sounddevice/numpy não estão instalados.
    """
    if not _has_sounddevice():
        raise ImportError(
            "sounddevice não instalado. Para capturar áudio:\n"
            "  pip install sounddevice numpy\n"
            "  (ou: uv sync --extra voice)"
        )
    if not _has_numpy():
        raise ImportError(
            "numpy não instalado. Para capturar áudio:\n"
            "  pip install numpy sounddevice\n"
            "  (ou: uv sync --extra voice)"
        )

    if np is None:
        raise ImportError(
            "numpy nÃ£o instalado. Para capturar Ã¡udio:\n"
            "  pip install numpy sounddevice\n"
            "  (ou: uv sync --extra voice)"
        )
    if sd is None:
        raise ImportError(
            "sounddevice nÃ£o instalado. Para capturar Ã¡udio:\n"
            "  pip install sounddevice numpy\n"
            "  (ou: uv sync --extra voice)"
        )
    if transcribe_audio is None:
        raise ImportError("transcription indisponivel para captura de audio.")

    if console is not None:
        console.print(
            "[cyan]🎤 Gravando áudio... Fale agora "
            f"(silêncio de {silence_duration_s:g}s para parar ou max {duration_max_s}s).[/cyan]"
        )

    try:
        # Grava em chunks, monitora amplitude em tempo real
        chunk_size = int(sample_rate * 0.1)  # 100ms chunks
        silence_frames = int(silence_duration_s * sample_rate / chunk_size)
        max_frames = int(duration_max_s * sample_rate / chunk_size)

        audio_frames: list[np.ndarray] = []
        silent_count = 0
        frame_count = 0

        with sd.InputStream(samplerate=sample_rate, channels=1, blocksize=chunk_size, dtype="float32"):
            while frame_count < max_frames:
                chunk = sd.rec(chunk_size, samplerate=sample_rate, channels=1, dtype="float32")
                sd.wait()

                audio_frames.append(chunk)
                frame_count += 1

                # Calcula RMS (root mean square) — proxy de amplitude
                rms = float(np.sqrt(np.mean(chunk**2)))
                db = 20 * np.log10(rms + 1e-9)  # evita log(0)

                if db < silence_threshold_db:
                    silent_count += 1
                    if silent_count >= silence_frames:
                        if console is not None:
                            console.print("[dim]Silêncio detectado, finalizando.[/dim]")
                        break
                else:
                    silent_count = 0  # reset se houver som novamente

        if not audio_frames:
            if console is not None:
                console.print("[red]✗ Nenhum áudio capturado.[/red]")
            return None

        # Salva em WAV temporário
        audio_data = np.concatenate(audio_frames, axis=0)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name

        import soundfile  # ou scipy.io.wavfile, soundfile é mais simples

        try:
            soundfile.write(wav_path, audio_data, sample_rate)
        except Exception:
            # Fallback: usar scipy se soundfile não disponível
            try:
                from scipy.io import wavfile

                wavfile.write(wav_path, sample_rate, (audio_data * 32767).astype(np.int16))
            except ImportError as e:
                raise ImportError(
                    "Nenhuma lib para salvar WAV. Rode: pip install soundfile (ou scipy)"
                ) from e

        if console is not None:
            console.print(f"[dim]Áudio salvo em {wav_path}[/dim]")

        # Transcreve
        if console is not None:
            console.print("[cyan]📝 Transcrevendo...[/cyan]")

        result = transcribe_audio(wav_path)

        # Limpa arquivo temporário
        with suppress(OSError):
            Path(wav_path).unlink()

        if result.get("success"):
            text = result.get("transcript", "").strip()
            provider = result.get("provider", "?")
            if console is not None:
                console.print(f"[green]✓ Transcrito via {provider}:[/green] {text}")
            logger.info("Voice input transcrito: %s chars, provider=%s", len(text), provider)
            return text
        else:
            error = result.get("error", "Erro desconhecido")
            if console is not None:
                console.print(f"[red]✗ Transcrição falhou: {error}[/red]")
            logger.error("Voice input falhou: %s", error)
            return None

    except KeyboardInterrupt:
        if console is not None:
            console.print("[yellow]⊘ Gravação cancelada.[/yellow]")
        return None
    except Exception as exc:
        if console is not None:
            console.print(f"[red]✗ Erro ao capturar áudio: {exc}[/red]")
        logger.exception("Audio capture error")
        return None
