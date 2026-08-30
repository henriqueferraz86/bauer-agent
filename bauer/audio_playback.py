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
import threading
from pathlib import Path

logger = logging.getLogger("bauer.audio_playback")

# Achado testando de verdade numa máquina sem placa de som (servidor headless
# — `aplay -l`/`arecord -l` reportam "no soundcards found"): `sd.play()` e até
# `sd.query_devices()` podem TRAVAR indefinidamente em vez de levantar — o
# PortAudio fica esperando um dispositivo que nunca aparece, e nem
# `timeout 15` na shell interrompe a chamada C por baixo. Um `except Exception`
# não protege contra isso porque não é uma exceção, é um bloqueio. Por isso a
# chamada real roda numa thread daemon com prazo — se estourar, devolve False
# e a thread (presa no PortAudio) é abandonada em vez de travar o chamador.
_PLAYBACK_TIMEOUT_S = 15.0

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


def play_audio_file(
    path: str | Path, *, blocking: bool = True, timeout: float = _PLAYBACK_TIMEOUT_S
) -> bool:
    """Toca um arquivo de áudio (wav/flac/ogg — o que soundfile suportar).

    Retorna True se tocou, False se as dependências faltam, o arquivo não
    existe/não abre, ou o playback não terminou dentro de `timeout` segundos
    (sem dispositivo de saída, o PortAudio pode travar em vez de levantar —
    ver o comentário no topo do módulo). Nunca levanta — playback é
    best-effort: uma falha aqui não deve derrubar o turno de voz, só deixar
    de reproduzir o áudio já sintetizado.
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

    outcome: dict[str, object] = {}

    def _do_play() -> None:
        try:
            data, samplerate = sf.read(str(p), dtype="float32")
            sd.play(data, samplerate)
            if blocking:
                sd.wait()
            outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001 — reportado pela thread principal
            outcome["error"] = exc

    # Thread daemon: se travar no PortAudio, o join abaixo estoura o prazo e
    # devolve o controle mesmo assim — a thread presa não impede o processo
    # de continuar (nem de sair, por ser daemon).
    worker = threading.Thread(target=_do_play, name="bauer-audio-playback", daemon=True)
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        logger.warning(
            "Playback de %s não terminou em %.0fs — sem dispositivo de áudio? "
            "Abandonando (a thread trava presa, mas não bloqueia o turno).",
            p, timeout,
        )
        return False

    if "error" in outcome:
        logger.warning("Playback de %s falhou: %s", p, outcome["error"])
        return False

    return bool(outcome.get("ok"))
