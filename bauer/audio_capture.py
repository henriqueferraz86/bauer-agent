"""Captura de áudio do microfone — integração com transcrição Whisper local.

Fluxo: grava em background → você aperta ENTER quando terminar de falar →
transcreve → retorna texto. Parada manual (não detecção automática de
silêncio) — mais confiável, já que não depende de calibrar threshold de dB
para o microfone/ambiente de cada um.

Uso::

    from bauer.audio_capture import capture_voice_input
    text = capture_voice_input(duration_max_s=60)
    if text:
        print(f"Você disse: {text}")

Dependências opcionais::

    pip install sounddevice numpy  # captura de áudio
    pip install faster-whisper     # transcrição local offline
"""

from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("bauer.audio_capture")


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


def _trim_silence(audio_data, chunk_size: int, threshold_db: float = -50.0):
    """Apara silêncio das bordas do áudio (início/fim) antes de transcrever.

    O Whisper tende a "alucinar" frases comuns (ex.: "Thank you.") quando
    recebe silêncio puro — muito comum no início da gravação, no tempo de
    reação entre ver "Gravando..." e começar a falar de fato. Isso é só
    limpeza do áudio enviado ao modelo; não decide quando PARAR de gravar
    (isso continua sendo o ENTER, manual).

    Mantém 1 chunk (100ms) de margem antes/depois da fala detectada para não
    cortar consoantes suaves no começo/fim da palavra. Se nenhum chunk passar
    do threshold (silêncio total), devolve o áudio original sem cortar — deixa
    a transcrição real decidir (evita mandar um array vazio para o Whisper).
    """
    import numpy as np

    n_chunks = len(audio_data) // chunk_size
    if n_chunks == 0:
        return audio_data

    def _chunk_db(i: int) -> float:
        seg = audio_data[i * chunk_size : (i + 1) * chunk_size]
        rms = float(np.sqrt(np.mean(seg**2)))
        return 20 * np.log10(rms + 1e-9)

    speech_idx = [i for i in range(n_chunks) if _chunk_db(i) >= threshold_db]
    if not speech_idx:
        return audio_data

    start = max(0, speech_idx[0] - 1) * chunk_size
    end = min(n_chunks, speech_idx[-1] + 2) * chunk_size
    return audio_data[start:end]


def capture_voice_input(
    duration_max_s: int = 60,
    sample_rate: int = 16000,
    console: Any = None,
) -> str | None:
    """Grava áudio do microfone até você apertar ENTER, transcreve com Whisper.

    Grava em uma thread de fundo em chunks de 100ms; o ENTER (bloqueante na
    thread principal) sinaliza o fim. ``duration_max_s`` é só uma trava de
    segurança caso você esqueça de apertar ENTER.

    Args:
        duration_max_s: teto de segurança (segundos) — para sozinho se você
            não apertar ENTER dentro desse tempo
        sample_rate: Hz
        console: console Rich (para output amigável)

    Retorna o texto transcrito ou None se falhar/cancelar/sem fala.

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

    import numpy as np
    import sounddevice as sd

    from .transcription import transcribe_audio

    if console is not None:
        console.print(
            f"[cyan]🎤 Gravando... fale e pressione ENTER quando terminar "
            f"(máx {duration_max_s}s).[/cyan]"
        )

    try:
        chunk_size = int(sample_rate * 0.1)  # 100ms chunks
        max_frames = int(duration_max_s * sample_rate / chunk_size)

        audio_frames: list[np.ndarray] = []
        stop_event = threading.Event()

        def _record() -> None:
            # sd.rec() abre/fecha sua própria stream por chamada — não
            # combinar com sd.InputStream() em paralelo (duas streams
            # disputando o mesmo microfone travava a gravação em alguns
            # drivers Windows).
            frame_count = 0
            while frame_count < max_frames and not stop_event.is_set():
                chunk = sd.rec(chunk_size, samplerate=sample_rate, channels=1, dtype="float32")
                sd.wait()
                audio_frames.append(chunk)
                frame_count += 1

        rec_thread = threading.Thread(target=_record, daemon=True)
        rec_thread.start()

        try:
            input()  # bloqueia até ENTER; conteúdo digitado é descartado
        except (EOFError, KeyboardInterrupt):
            pass
        stop_event.set()
        rec_thread.join(timeout=2.0)

        if not audio_frames:
            if console is not None:
                console.print("[red]✗ Nenhum áudio capturado.[/red]")
            return None

        # Apara silêncio das bordas (reduz alucinação do Whisper em trechos
        # de silêncio puro, ex.: "Thank you." — comum no início da gravação).
        audio_data = np.concatenate(audio_frames, axis=0)
        audio_data = _trim_silence(audio_data, chunk_size)

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

        # Limpa arquivo temporário — a menos que BAUER_VOICE_KEEP_WAV esteja
        # setado (debug: ouvir o áudio capturado de verdade, útil pra separar
        # "mic capturou errado" de "transcrição alucinou").
        import os

        if os.environ.get("BAUER_VOICE_KEEP_WAV", "").strip() not in ("", "0", "false", "False"):
            if console is not None:
                console.print(f"[yellow]Áudio mantido para debug: {wav_path}[/yellow]")
        else:
            try:
                Path(wav_path).unlink()
            except Exception:
                pass

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
