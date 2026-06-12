"""Transcrição de áudio (STT) — voice notes do gateway viram texto.

Providers cloud OpenAI-compat, em ordem de preferência:
  1. Groq Whisper (``whisper-large-v3-turbo``) — free tier, GROQ_API_KEY
  2. OpenAI Whisper (``whisper-1``) — OPENAI_API_KEY

Uso::

    from bauer.transcription import transcribe_audio
    result = transcribe_audio("workspace/.bauer_gateway/media/voice_1.ogg")
    if result["success"]:
        print(result["transcript"])

Sem dependências novas: httpx multipart (já é core). Arquiteturalmente igual
ao Hermes (tools/transcription_tools.py) mas só o caminho cloud — o local
faster-whisper fica para quando houver demanda.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("bauer.transcription")

# Formatos aceitos pelos endpoints Whisper (Telegram voice = .ogg/opus)
AUDIO_EXTENSIONS = {
    ".ogg", ".oga", ".opus", ".mp3", ".mp4", ".m4a", ".wav", ".webm", ".flac", ".mpga", ".mpeg",
}
MAX_AUDIO_BYTES = 25 * 1024 * 1024  # limite documentado dos dois providers

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_STT_MODEL = os.environ.get("STT_GROQ_MODEL", "whisper-large-v3-turbo")
OPENAI_STT_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_STT_MODEL = os.environ.get("STT_OPENAI_MODEL", "whisper-1")

_TIMEOUT_S = 120.0


def _validate(path: str | Path) -> str | None:
    """Retorna mensagem de erro ou None se o arquivo é transcrevível."""
    p = Path(path)
    if not p.is_file():
        return f"Arquivo não encontrado: {p}"
    if p.suffix.lower() not in AUDIO_EXTENSIONS:
        return (
            f"Extensão {p.suffix!r} não suportada. "
            f"Aceitas: {', '.join(sorted(AUDIO_EXTENSIONS))}"
        )
    size = p.stat().st_size
    if size == 0:
        return f"Arquivo vazio: {p}"
    if size > MAX_AUDIO_BYTES:
        return f"Arquivo de {size / 1e6:.1f}MB excede o limite de 25MB do Whisper."
    return None


def _post_whisper(url: str, api_key: str, model: str, path: Path) -> dict[str, Any]:
    """POST multipart num endpoint /audio/transcriptions OpenAI-compat."""
    import httpx

    with path.open("rb") as fh:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (path.name, fh)},
            data={"model": model, "response_format": "json"},
            timeout=_TIMEOUT_S,
        )
    if resp.status_code != 200:
        detail = resp.text[:300]
        try:
            body = resp.json()
            detail = str(
                (body.get("error") or {}).get("message")
                if isinstance(body.get("error"), dict)
                else body.get("error") or detail
            )
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
    text = (resp.json().get("text") or "").strip()
    if not text:
        raise RuntimeError("transcrição vazia")
    return {"success": True, "transcript": text}


def available_stt_provider() -> str | None:
    """Qual provider STT está configurado agora ('groq', 'openai' ou None)."""
    if os.environ.get("GROQ_API_KEY", "").strip():
        return "groq"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    return None


def transcribe_audio(file_path: str | Path, model: str | None = None) -> dict[str, Any]:
    """Transcreve um arquivo de áudio. Groq primeiro (free), depois OpenAI.

    Retorna ``{"success": bool, "transcript": str, "provider": str}`` ou
    ``{"success": False, "transcript": "", "error": str}``. Nunca levanta —
    o gateway precisa degradar com mensagem amigável, não crashar o turno.
    """
    err = _validate(file_path)
    if err:
        return {"success": False, "transcript": "", "error": err}
    path = Path(file_path)

    attempts: list[tuple[str, str, str, str]] = []  # (provider, url, key, model)
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    if groq_key:
        attempts.append(("groq", GROQ_STT_URL, groq_key, model or GROQ_STT_MODEL))
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_key:
        attempts.append(("openai", OPENAI_STT_URL, openai_key, model or OPENAI_STT_MODEL))

    if not attempts:
        return {
            "success": False,
            "transcript": "",
            "error": (
                "Nenhum provider STT configurado. Defina GROQ_API_KEY "
                "(gratuito — console.groq.com) ou OPENAI_API_KEY no .env."
            ),
        }

    errors: list[str] = []
    for provider, url, key, mdl in attempts:
        try:
            result = _post_whisper(url, key, mdl, path)
            result["provider"] = provider
            logger.info(
                "Transcrito %s via %s (%d chars)", path.name, provider,
                len(result["transcript"]),
            )
            return result
        except Exception as exc:  # noqa: BLE001 — tenta o próximo provider
            errors.append(f"{provider}: {exc}")
            logger.warning("STT %s falhou: %s", provider, exc)

    return {
        "success": False,
        "transcript": "",
        "error": "Transcrição falhou — " + "; ".join(errors),
    }
