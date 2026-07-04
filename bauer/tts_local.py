"""TTS local offline via Piper — fala a resposta do agente sem depender de nuvem.

Modelo de voz é baixado uma vez (~50-70MB) e cacheado em
``$BAUER_HOME/tts_voices``. Voz padrão em português (pt_BR-faber-medium);
outras vozes: https://huggingface.co/rhasspy/piper-voices (formato
``<lang>_<REGION>-<nome>-<qualidade>``, ex.: en_US-lessac-medium).

Uso::

    from bauer.tts_local import speak_text
    speak_text("Olá, como posso ajudar?")
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("bauer.tts_local")

DEFAULT_VOICE = os.environ.get("TTS_LOCAL_VOICE", "pt_BR-faber-medium")

# Cache do modelo carregado — carregar o ONNX é caro; reusa entre falas.
_VOICE_CACHE: dict[str, Any] = {}

# Faixas Unicode de emoji/símbolos pictográficos — Piper lê "*" e emoji como
# fala literal ("asterisco", nome do glifo), o que soa mal. Removidos antes
# de sintetizar; o texto impresso no terminal continua com a formatação.
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # símbolos & pictogramas diversos, emoticons, transporte, suplementares
    "\U00002600-\U000027BF"  # símbolos diversos + dingbats (☀ ✓ ➜ etc.)
    "\U0001F1E6-\U0001F1FF"  # bandeiras (pares regionais)
    "\U00002190-\U000021FF"  # setas
    "\U0000FE0F"             # variation selector (emoji presentation)
    "]+",
    flags=re.UNICODE,
)
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_MD_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BOLD_ITALIC_RE = re.compile(r"(\*\*\*|\*\*|\*|___|__|_)(.+?)\1")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)


def clean_for_speech(text: str) -> str:
    """Remove markdown e emoji antes de sintetizar — Piper lê tudo ao pé da
    letra ('asterisco', nome do emoji), o que não faz sentido em voz alta.

    O texto exibido no terminal (Markdown renderizado) não passa por aqui;
    isso afeta só o que é enviado ao TTS.
    """
    t = text
    t = _CODE_BLOCK_RE.sub(" ", t)
    t = _INLINE_CODE_RE.sub(r"\1", t)
    t = _MD_HEADER_RE.sub("", t)
    t = _MD_LINK_RE.sub(r"\1", t)
    t = _MD_BULLET_RE.sub("", t)
    # Bold/itálico pode aninhar (**_x_**) — aplica algumas vezes até estabilizar.
    for _ in range(3):
        new_t = _MD_BOLD_ITALIC_RE.sub(r"\2", t)
        if new_t == t:
            break
        t = new_t
    t = _EMOJI_RE.sub("", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"[ \t]*\n[ \t]*", "\n", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


def _has_piper() -> bool:
    """True se piper-tts está instalado (sem importar de fato)."""
    import importlib.util

    try:
        return importlib.util.find_spec("piper") is not None
    except (ImportError, ValueError):
        return False


def _voice_paths(voice: str) -> tuple[Path, Path]:
    from .paths import tts_voices_dir

    d = tts_voices_dir()
    return d / f"{voice}.onnx", d / f"{voice}.onnx.json"


def _ensure_voice_downloaded(voice: str) -> tuple[Path, Path]:
    """Garante que o modelo da voz existe localmente, baixando se preciso."""
    model_path, config_path = _voice_paths(voice)
    if model_path.exists() and config_path.exists():
        return model_path, config_path

    from piper.download_voices import download_voice

    from .paths import tts_voices_dir

    download_voice(voice, tts_voices_dir())
    return model_path, config_path


def _load_voice(voice: str):
    """Carrega (ou reusa do cache) a voz Piper. Baixa os pesos na 1ª vez."""
    if voice in _VOICE_CACHE:
        return _VOICE_CACHE[voice]

    from piper import PiperVoice

    model_path, config_path = _ensure_voice_downloaded(voice)
    pv = PiperVoice.load(str(model_path), config_path=str(config_path))
    _VOICE_CACHE[voice] = pv
    return pv


def synthesize_to_file(text: str, output_path: str | Path, voice: str | None = None) -> Path:
    """Sintetiza texto em um arquivo WAV local. Retorna o Path do arquivo.

    Levanta ImportError se piper-tts não está instalado.
    """
    if not _has_piper():
        raise ImportError(
            "piper-tts não instalado. Para falar a resposta:\n"
            "  pip install piper-tts\n"
            "  (ou: uv sync --extra voice)"
        )

    import numpy as np
    import soundfile as sf

    pv = _load_voice(voice or DEFAULT_VOICE)
    chunks = list(pv.synthesize(clean_for_speech(text)))
    if not chunks:
        raise RuntimeError("Piper não gerou áudio para o texto fornecido.")
    audio = np.concatenate([c.audio_int16_array for c in chunks])

    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dest), audio, pv.config.sample_rate)
    return dest


def speak_text(text: str, voice: str | None = None, console: Any = None) -> bool:
    """Sintetiza e TOCA o texto nas caixas de som. Retorna True se tocou.

    Não bloqueia o chat por erro — falha vira aviso no console (se fornecido)
    e retorna False, nunca levanta.
    """
    text = clean_for_speech(text or "")
    if not text:
        return False

    try:
        import sounddevice as sd

        pv = _load_voice(voice or DEFAULT_VOICE)
        import numpy as np

        chunks = list(pv.synthesize(text))
        if not chunks:
            return False
        audio = np.concatenate([c.audio_int16_array for c in chunks])
        sd.play(audio, samplerate=pv.config.sample_rate)
        sd.wait()
        return True
    except ImportError as exc:
        if console is not None:
            console.print(f"[yellow]TTS local indisponível: {exc}[/yellow]")
        logger.warning("speak_text: dependência faltando: %s", exc)
        return False
    except Exception as exc:  # noqa: BLE001 — TTS é acessório, nunca bloqueia o turno
        if console is not None:
            console.print(f"[yellow]Falha ao falar a resposta: {exc}[/yellow]")
        logger.warning("speak_text falhou: %s", exc)
        return False
