"""Entrada e saída de voz do REPL do Bauer.

Estas funções são deliberadamente best-effort: falhas de áudio não podem
interromper uma conversa cujo texto continua disponível no terminal.
"""

from __future__ import annotations

import re
from contextlib import suppress
from pathlib import Path
from typing import Any

from rich.console import Console

_LISTEN_LOOP_STOP_WORDS = {
    "parar", "para", "cancelar", "sair", "encerrar", "stop", "cancel", "exit",
}


def capture_listen_input(console: Console, *, metrics: Any = None, capture: Any = None) -> str | None:
    """Captura e transcreve uma fala que pode ser usada em um turno do chat."""
    if metrics is not None:
        metrics.mark("stt_start")
    try:
        if capture is None:
            from .voice_stt_stream import capture_voice_input_streaming

            capture = capture_voice_input_streaming
        text = capture(console=console)
    except KeyboardInterrupt:
        console.print("[yellow]Audio cancelado.[/yellow]")
        return None
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        return None
    except Exception as exc:  # noqa: BLE001 - voz não encerra o REPL
        console.print(f"[red]Erro ao ouvir: {exc}[/red]")
        return None
    finally:
        if metrics is not None:
            metrics.mark("stt_end")

    text = (text or "").strip()
    if not is_meaningful_voice_text(text):
        console.print("[yellow]Nada util foi transcrito; tente falar novamente.[/yellow]")
        return None
    return text


def is_meaningful_voice_text(text: str) -> bool:
    compact = text.strip()
    return len(compact) >= 2 and bool(re.search(r"[\wÀ-ÿ]", compact, flags=re.UNICODE))


def is_listen_loop_stop(text: str) -> bool:
    normalized = re.sub(r"[^\wÀ-ÿ\s-]", "", text.strip().lower(), flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized in _LISTEN_LOOP_STOP_WORDS


def speak_voice_reply(console: Console, text: str, client: Any = None) -> None:
    """Sintetiza e toca a resposta, sem deixar TTS interromper o REPL."""
    text = (text or "").strip()
    if not text:
        return

    def fallback_to_voice_session() -> bool:
        if client is None:
            return False
        try:
            from .voice_session import speak_response

            speak_response(text, client)
            return True
        except Exception as exc:  # noqa: BLE001
            from .logging_config import log_suppressed

            log_suppressed("agent.voice_reply.session_fallback", exc)
            return False

    try:
        from .tts import synthesize_speech

        result = synthesize_speech(text)
    except Exception as exc:  # noqa: BLE001
        from .logging_config import log_suppressed

        log_suppressed("agent.voice_reply.synthesize", exc)
        fallback_to_voice_session()
        return

    if not result.get("success"):
        if not fallback_to_voice_session():
            console.print(f"[dim yellow](voz indisponível: {result.get('error')})[/dim yellow]")
        return

    path = result["path"]
    try:
        from .audio_playback import play_audio_file

        played = play_audio_file(path)
    except Exception as exc:  # noqa: BLE001
        from .logging_config import log_suppressed

        log_suppressed("agent.voice_reply.playback", exc)
        played = False
    if not played:
        console.print(f"[dim yellow](não foi possível tocar o áudio: {path})[/dim yellow]")

    audio_path = Path(path)
    if "bauer-tts-" in audio_path.name:
        with suppress(OSError):
            audio_path.unlink()
