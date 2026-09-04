"""Diagnóstico local e não destrutivo do subsistema de voz."""

from __future__ import annotations

import importlib.util
import os
import shutil
from typing import Any


def _available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def collect_voice_status() -> list[dict[str, Any]]:
    """Retorna componentes de voz e o estado detectado localmente."""
    capture = _available("sounddevice") and _available("numpy")
    stt_local = _available("faster_whisper")
    wav = _available("soundfile")
    stt_stream = capture and wav
    acoustic = _available("openwakeword") and bool(
        os.environ.get("BAUER_WAKE_MODEL", "").strip()
    )
    tts_local = os.name == "nt" and bool(shutil.which("powershell") or shutil.which("pwsh"))
    barge_in = capture and wav
    return [
        {
            "name": "microfone / VAD",
            "ok": capture,
            "detail": "sounddevice + numpy" if capture else "requer sounddevice + numpy",
        },
        {
            "name": "STT local",
            "ok": stt_local,
            "detail": "faster-whisper disponível" if stt_local else "fallback cloud ou instale faster-whisper",
        },
        {
            "name": "STT streaming",
            "ok": stt_stream,
            "detail": (
                "ativo por padrão; VAD encerra após 0,8s de silêncio"
                if stt_stream
                else "requer sounddevice + numpy + soundfile"
            ),
        },
        {
            "name": "persistência WAV",
            "ok": wav,
            "detail": "soundfile disponível" if wav else "requer soundfile",
        },
        {
            "name": "TTS local",
            "ok": tts_local,
            "detail": "SAPI / PowerShell disponível" if tts_local else "indisponível neste sistema",
        },
        {
            "name": "wake word acústica",
            "ok": acoustic,
            "detail": (
                "modelo configurado"
                if acoustic
                else "opcional: openwakeword + BAUER_WAKE_MODEL"
            ),
        },
        {
            "name": "barge-in VAD + AEC",
            "ok": barge_in,
            "detail": "pronto quando BAUER_VOICE_BARGE_IN=1" if barge_in else "requer stack de captura + soundfile",
        },
    ]
