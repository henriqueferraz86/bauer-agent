"""Backend local opcional do Kokoro-82M via ONNX Runtime.

Os pesos nao ficam no repositorio: sao baixados para ``$BAUER_HOME/models``
por demanda. O modelo nao clona a voz de uma pessoa; o perfil Jarvis apenas
seleciona uma voz masculina britanica disponivel no voicepack.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .http_shared import shared_ssl_context
from .paths import get_bauer_home

logger = logging.getLogger("bauer.voice_kokoro")

MODEL_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/kokoro-v1.0.onnx"
)
VOICES_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/voices-v1.0.bin"
)
MODEL_FILENAME = "kokoro-v1.0.onnx"
VOICES_FILENAME = "voices-v1.0.bin"
DEFAULT_VOICE = "pm_alex"
_DOWNLOAD_TIMEOUT = httpx.Timeout(60.0, read=600.0)
_ENGINE_CACHE: dict[tuple[str, str], Any] = {}
_ENGINE_LOCK = threading.RLock()


class KokoroUnavailable(RuntimeError):
    """Runtime, pesos ou dependencias do Kokoro nao estao disponiveis."""


def kokoro_model_dir(root: Path | None = None) -> Path:
    """Retorna o diretorio de cache dos pesos do Kokoro."""
    directory = (root or get_bauer_home()) / "models" / "kokoro"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def kokoro_model_paths(root: Path | None = None) -> tuple[Path, Path]:
    directory = kokoro_model_dir(root)
    return directory / MODEL_FILENAME, directory / VOICES_FILENAME


def kokoro_models_ready(root: Path | None = None) -> bool:
    """Indica se os dois arquivos necessarios ja foram baixados."""
    model, voices = kokoro_model_paths(root)
    return model.is_file() and model.stat().st_size > 1_000_000 and voices.is_file() and voices.stat().st_size > 1_000


def _download_one(
    url: str,
    destination: Path,
    *,
    progress: Callable[[Path, int, int | None], None] | None = None,
) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with httpx.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=_DOWNLOAD_TIMEOUT,
            verify=shared_ssl_context(),
        ) as response:
            response.raise_for_status()
            total = int(response.headers["content-length"]) if response.headers.get("content-length") else None
            received = 0
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    output.write(chunk)
                    received += len(chunk)
                    if progress is not None:
                        progress(destination, received, total)
        if received == 0:
            raise KokoroUnavailable(f"download vazio: {url}")
        temporary.replace(destination)
    except Exception:
        try:
            temporary.unlink()
        except OSError as exc:
            logger.debug("falha ao remover download parcial do Kokoro: %s", exc)
        raise


def download_kokoro_models(
    root: Path | None = None,
    *,
    progress: Callable[[Path, int, int | None], None] | None = None,
) -> tuple[Path, Path]:
    """Baixa o modelo e o voicepack de fontes publicas conhecidas."""
    model, voices = kokoro_model_paths(root)
    if not model.is_file() or model.stat().st_size <= 1_000_000:
        _download_one(MODEL_URL, model, progress=progress)
    if not voices.is_file() or voices.stat().st_size <= 1_000:
        _download_one(VOICES_URL, voices, progress=progress)
    return model, voices


def configured_kokoro_voice(default: str | None = None) -> str:
    """Resolve a voz Kokoro sem aceitar nomes de voz remota por engano."""
    configured = os.environ.get("BAUER_TTS_KOKORO_VOICE", "").strip()
    if configured:
        return configured
    configured = os.environ.get("BAUER_TTS_VOICE", "").strip()
    if configured.startswith(("af_", "am_", "bf_", "bm_", "pf_", "pm_")):
        return configured
    if default is not None:
        return default
    language = os.environ.get("BAUER_TTS_LANGUAGE", "pt-br").strip().lower()
    return "pm_alex" if language.startswith("pt") else "bm_george"


def _kokoro_language(voice: str) -> str:
    configured = os.environ.get("BAUER_TTS_KOKORO_LANGUAGE", "").strip().lower()
    if configured:
        return configured
    if voice.startswith(("pf_", "pm_")):
        return "pt-br"
    if voice.startswith(("bf_", "bm_")):
        return "en-gb"
    return "en-us"


def _engine(model: Path, voices: Path) -> Any:
    try:
        from kokoro_onnx import Kokoro
    except ImportError as exc:
        raise KokoroUnavailable(
            "Kokoro nao instalado; rode `uv sync --extra voice-kokoro`"
        ) from exc
    key = (str(model), str(voices))
    with _ENGINE_LOCK:
        instance = _ENGINE_CACHE.get(key)
        if instance is None:
            instance = Kokoro(str(model), str(voices))
            _ENGINE_CACHE[key] = instance
        return instance


def synthesize_kokoro_speech(
    text: str,
    output_file: str | Path,
    *,
    voice: str | None = None,
    speed: float = 0.95,
    root: Path | None = None,
) -> Path:
    """Gera WAV local com Kokoro, baixando os pesos se necessario."""
    try:
        import soundfile as sf
    except ImportError as exc:
        raise KokoroUnavailable(
            "soundfile nao instalado; rode `uv sync --extra voice`"
        ) from exc
    model, voices = download_kokoro_models(root)
    selected_voice = voice or configured_kokoro_voice()
    language = _kokoro_language(selected_voice)
    destination = Path(output_file)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _ENGINE_LOCK:
        engine = _engine(model, voices)
        audio, sample_rate = engine.create(
            text,
            selected_voice,
            speed=float(speed),
            lang=language,
        )
    sf.write(str(destination), audio, sample_rate)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise KokoroUnavailable("Kokoro nao produziu audio")
    return destination
