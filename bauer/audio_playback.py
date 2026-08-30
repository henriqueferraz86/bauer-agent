"""Playback de áudio — toca a resposta falada do Bauer no alto-falante.

Fecha o loop de voz iniciado por bauer/audio_capture.py (mic → texto) com o
lado de saída (texto → voz, via bauer/tts.py → alto-falante). Usa as mesmas
dependências já exigidas pela captura (sounddevice) mais soundfile para ler
o WAV — nenhuma lib de áudio nova para quem já tem `--extra voice`.

Uso::

    from bauer.audio_playback import play_audio_file
    play_audio_file("resposta.wav")
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("bauer.audio_playback")

try:
    import sounddevice as sd  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001 - optional voice dependency
    sd = None  # type: ignore[assignment]

try:
    import soundfile as sf  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001 - optional voice dependency
    sf = None  # type: ignore[assignment]


def _has_sounddevice() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("sounddevice") is not None
    except (ImportError, ValueError):
        return False


def _has_soundfile() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("soundfile") is not None
    except (ImportError, ValueError):
        return False


def play_audio_file(path: str | Path, *, blocking: bool = True) -> bool:
    """Toca um arquivo de áudio (wav/flac/ogg — o que soundfile suportar).

    Retorna True se tocou, False se as dependências faltam ou o arquivo não
    existe/não abre. Nunca levanta — playback é best-effort: uma falha aqui
    (sem alto-falante, dispositivo ocupado) não deve derrubar o turno de voz,
    só deixar de reproduzir o áudio já sintetizado.
    """
    if not _has_sounddevice() or not _has_soundfile():
        logger.warning(
            "Playback indisponível: instale sounddevice + soundfile "
            "(`pip install sounddevice soundfile` ou `uv sync --extra voice`)."
        )
        return False

    p = Path(path)
    if not p.is_file():
        logger.warning("Arquivo de áudio não encontrado: %s", p)
        return False

    try:
        data, samplerate = sf.read(str(p), dtype="float32")
        sd.play(data, samplerate)
        if blocking:
            sd.wait()
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca propaga
        logger.warning("Playback de %s falhou: %s", p, exc)
        return False
