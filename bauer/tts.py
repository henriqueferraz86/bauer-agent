"""Síntese de voz (TTS) — devolve as respostas do Bauer faladas.

Providers (ordem padrão em TTS_PROVIDER=auto):
  1. Coqui XTTS-v2 local (``pip install coqui-tts``) — OFFLINE, sem API key,
     fala nativamente em pt/en/es/... Pesos (~1.9GB) baixam do Hugging Face
     na primeira execução.
  2. OpenAI tts-1 (cloud, OPENAI_API_KEY)

Selecione explicitamente com ``TTS_PROVIDER`` ou ``BAUER_TTS_PROVIDER`` =
auto | local | openai | kokoro.
Para voz 100% offline::

    pip install coqui-tts               # ou: uv sync --extra voice-tts
    export TTS_PROVIDER=local           # Windows: set TTS_PROVIDER=local
    # opcionais: TTS_LANGUAGE (pt), TTS_VOICE (nome de speaker embutido),
    #            BAUER_TTS_SPEAKER_WAV (WAV de referência para clonagem),
    #            TTS_LOCAL_DEVICE (auto|cpu|cuda)

Uso::

    from bauer.tts import synthesize_speech
    result = synthesize_speech("Olá, tudo bem?")
    if result["success"]:
        print(result["path"])

Espelha o design de ``bauer/transcription.py`` (mesmo padrão de provider com
fallback em cascata, mesmo estilo de retorno ``{"success", ...}`` que nunca
levanta) para quem já conhece um dos dois módulos reconhecer o outro de cara.

Aviso de licença: os PESOS do XTTS-v2 são distribuídos sob a Coqui Public
Model License (CPML) — uso não-comercial apenas (o código da lib em si é
MPL-2.0, sem essa restrição). ``synthesize_speech`` aceita os termos
automaticamente (``COQUI_TOS_AGREED=1``) para não travar num prompt
interativo em processo headless, e loga o aviso uma vez por processo em vez
de escondê-lo.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import warnings
from contextlib import suppress
from pathlib import Path
from typing import Any

from .voice_text import strip_emoji_for_speech

logger = logging.getLogger("bauer.tts")

# Mesmo limite documentado da API OpenAI TTS — aplicado a todos os providers
# para manter o contrato de erro previsível independente de qual foi usado.
MAX_TEXT_CHARS = 4096

OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
OPENAI_TTS_MODEL = os.environ.get("TTS_OPENAI_MODEL", "tts-1")
OPENAI_TTS_VOICE = os.environ.get("TTS_OPENAI_VOICE", "alloy")
# Mesmas listas validadas de bauer/tools/media.py (tool text_to_speech) — não
# duplicar a decisão de quais voices/models são aceitos, só o valor.
_OPENAI_VALID_VOICES = ("alloy", "echo", "fable", "onyx", "nova", "shimmer")
_OPENAI_VALID_MODELS = ("tts-1", "tts-1-hd")

# Local (Coqui XTTS-v2) — roda os pesos open-source OFFLINE na máquina.
# pip: coqui-tts (fork idiap mantido; o pacote original coqui-ai/TTS está sem
# desenvolvimento desde o fechamento da Coqui Inc em 2024). Import: `TTS.api`
# — o fork preserva o caminho de import do pacote original.
LOCAL_TTS_MODEL = os.environ.get(
    "TTS_LOCAL_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2"
)
LOCAL_TTS_DEVICE = os.environ.get("TTS_LOCAL_DEVICE", "auto")  # auto | cpu | cuda
LOCAL_TTS_LANGUAGE = os.environ.get("TTS_LANGUAGE", "pt")
# Vazio = usa o primeiro speaker embutido do modelo (zero-config: XTTS-v2 tem
# dezenas de speakers pré-treinados, não precisa de áudio de referência).
LOCAL_TTS_SPEAKER = os.environ.get("TTS_VOICE", "").strip()

_TIMEOUT_S = 120.0

# Cache do modelo local — carregar os pesos é caro; reusa entre sínteses.
_LOCAL_MODEL_CACHE: dict[tuple, Any] = {}
_DLL_DIRECTORY_HANDLES: list[Any] = []
_FFMPEG_RUNTIME_READY = False

_TOS_NOTICE_SHOWN = False
_TOS_LOCK = threading.Lock()


def _tts_provider_pref() -> str:
    """Preferência de provider, lida em call-time."""
    configured = os.environ.get("BAUER_TTS_PROVIDER", os.environ.get("TTS_PROVIDER", ""))
    if configured.strip():
        return configured.strip().lower()
    # Quando há uma referência XTTS configurada, ela tem precedência sobre o
    # Kokoro: a referência é uma escolha explícita da voz clonada do usuário.
    if _coqui_tts_available() and local_tts_speaker_wav() is not None:
        return "local"
    # A instalação padrão do Bauer inclui Kokoro. Se a preferência não foi
    # explicitamente configurada, escolha-o automaticamente em vez de exigir
    # que cada sessão exporte BAUER_TTS_PROVIDER manualmente.
    return "kokoro" if _kokoro_available() else "auto"


def local_tts_speaker_wav() -> Path | None:
    """Retorna o WAV de referência XTTS configurado, se existir.

    O caminho padrão fica nos dados do usuário para sobreviver a updates do
    código e não depender de um arquivo distribuído pelo repositório. Um
    caminho explícito é retornado mesmo quando está ausente, permitindo que
    a síntese produza um erro claro em vez de trocar silenciosamente de voz.
    """
    configured = os.environ.get(
        "BAUER_TTS_SPEAKER_WAV", os.environ.get("TTS_SPEAKER_WAV", "")
    ).strip()
    if configured:
        return Path(configured).expanduser()
    try:
        from .paths import get_bauer_home

        default = get_bauer_home() / "voices" / "jarvis18s-reference.wav"
        return default if default.is_file() else None
    except (OSError, RuntimeError):
        return None


def _configure_ffmpeg_runtime() -> None:
    """Torna DLLs do FFmpeg compartilhado visíveis ao TorchCodec no Windows.

    O ``coqui-tts`` usa o TorchCodec para ler o WAV de referência. Em Windows
    é comum o usuário ter apenas o build estático do FFmpeg no PATH; esse
    build não fornece as DLLs que o TorchCodec carrega. Se o build compartilhado
    do WinGet estiver instalado, adicioná-lo aqui torna o Bauer autocontido
    mesmo quando foi aberto antes de um novo terminal.
    """
    global _FFMPEG_RUNTIME_READY
    if _FFMPEG_RUNTIME_READY or os.name != "nt":
        return
    localappdata = os.environ.get("LOCALAPPDATA", "").strip()
    if not localappdata:
        return
    packages = Path(localappdata) / "Microsoft" / "WinGet" / "Packages"
    try:
        dll = next(packages.rglob("avcodec-*.dll"), None)
    except OSError:
        return
    if dll is None:
        return
    bin_dir = dll.parent
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is not None:
        try:
            _DLL_DIRECTORY_HANDLES.append(add_dll_directory(str(bin_dir)))
        except OSError:
            pass
    _FFMPEG_RUNTIME_READY = True


def _kokoro_available() -> bool:
    """Indica se o runtime opcional do Kokoro está instalado neste Python."""
    try:
        import importlib.util

        return importlib.util.find_spec("kokoro_onnx") is not None
    except (ImportError, ValueError):
        return False


def _validate_text(text: str) -> str | None:
    """Retorna mensagem de erro ou None se o texto é sintetizável."""
    if not text or not text.strip():
        return "Texto vazio."
    if len(text) > MAX_TEXT_CHARS:
        return f"Texto de {len(text)} chars excede o limite de {MAX_TEXT_CHARS}."
    return None


def _post_openai_tts(text: str, dest: Path) -> None:
    """POST em /v1/audio/speech — resposta é o áudio bruto (não JSON)."""
    import httpx

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if OPENAI_TTS_VOICE not in _OPENAI_VALID_VOICES:
        raise RuntimeError(f"TTS_OPENAI_VOICE inválida: {OPENAI_TTS_VOICE!r}")
    if OPENAI_TTS_MODEL not in _OPENAI_VALID_MODELS:
        raise RuntimeError(f"TTS_OPENAI_MODEL inválido: {OPENAI_TTS_MODEL!r}")
    resp = httpx.post(
        OPENAI_TTS_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": OPENAI_TTS_MODEL,
            "voice": OPENAI_TTS_VOICE,
            "input": text,
            "response_format": "wav",
        },
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
    if not resp.content:
        raise RuntimeError("resposta de áudio vazia")
    dest.write_bytes(resp.content)


def _coqui_tts_available() -> bool:
    """True se o pacote coqui-tts estiver instalado (sem importá-lo de fato)."""
    import importlib.util
    try:
        return importlib.util.find_spec("TTS") is not None
    except (ImportError, ValueError):
        return False


def _torch_stack_available() -> bool:
    """True se PyTorch, Torchaudio E Torchcodec estiverem instalados (sem
    importar nenhum dos três).

    XTTS-v2 (TTS/tts/models/xtts.py) importa `torchaudio` além de `torch` —
    ter só o primeiro ainda quebra o import com o mesmo ModuleNotFoundError
    genérico, então os três entram na mesma checagem em vez de checar torch
    sozinho e dar um diagnóstico incompleto. `torchcodec` é exigido a partir
    do torch 2.9 (torchaudio trocou de backend de I/O de áudio) — como o
    PyPI serve a versão mais recente do torch por padrão, quem instalar hoje
    sempre bate nesse requisito, então ele entra na mesma checagem em vez de
    virar um quarto ramo de erro separado (achado testando de ponta a ponta,
    não só lendo a doc — ver pyproject.toml).
    """
    import importlib.util
    try:
        return (
            importlib.util.find_spec("torch") is not None
            and importlib.util.find_spec("torchaudio") is not None
            and importlib.util.find_spec("torchcodec") is not None
        )
    except (ImportError, ValueError):
        return False


def _resolve_local_device() -> str:
    if LOCAL_TTS_DEVICE in ("cpu", "cuda"):
        return LOCAL_TTS_DEVICE
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:  # noqa: BLE001 — sem torch importável, assume CPU
        return "cpu"


def _show_cpml_notice_once() -> None:
    """Loga o aviso de licença do XTTS-v2 uma vez por processo.

    Os PESOS (não o código da lib) são CPML — uso não-comercial. Preferimos
    logar isso de forma visível a esconder o limite: quem for redistribuir
    Bauer com síntese local ativada por padrão precisa saber disso.
    """
    global _TOS_NOTICE_SHOWN
    with _TOS_LOCK:
        if _TOS_NOTICE_SHOWN:
            return
        _TOS_NOTICE_SHOWN = True
    logger.info(
        "XTTS-v2 (voz local) é distribuído sob a Coqui Public Model License "
        "(CPML) — uso não-comercial apenas. Detalhes: "
        "https://coqui.ai/cpml.txt"
    )


def _load_local_model(model: str | None = None):
    """Carrega (ou reusa do cache) o TTS local. Baixa os pesos na 1ª vez.

    Separado de _synthesize_local para o preload no boot poder aquecer o
    cache sem sintetizar nada — mesmo padrão de transcription.py.
    """
    try:
        _configure_ffmpeg_runtime()
        # Aceita os termos ANTES do import: a lib pergunta y/n na 1ª carga do
        # modelo, e isso travaria para sempre num processo headless (serve,
        # daemon, CI) sem esta env setada antes.
        os.environ.setdefault("COQUI_TOS_AGREED", "1")
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"torch\.jit\.script is deprecated\.*",
                category=FutureWarning,
            )
            from TTS.api import TTS
    except ImportError as exc:
        if not _coqui_tts_available():
            raise RuntimeError(
                "coqui-tts não instalado. Rode `pip install coqui-tts` (ou "
                "`uv sync --extra voice-tts`) para voz local offline. Pesos "
                "do XTTS-v2 (~1.9GB) baixam do Hugging Face na 1ª execução."
            ) from exc
        # coqui-tts está instalado, mas o import falhou por outro motivo —
        # o pacote está no venv, o find_spec acima achou. Distinguir isso da
        # falta do pacote importa: dizer "não instalado" quando ele ESTÁ
        # manda quem lê pra reinstalar algo que já tem, sem tocar a causa
        # real. O caso comum é PyTorch/Torchaudio/Torchcodec ausentes:
        # TTS/__init__.py exige os três mas não os declara como dependência
        # pip DE PROPÓSITO — a lib deixa o usuário escolher a build certa
        # (CPU ou CUDA) em vez de o resolver genérico puxar a errada.
        if not _torch_stack_available():
            raise RuntimeError(
                "coqui-tts está instalado, mas falta PyTorch/Torchaudio/"
                "Torchcodec (a lib não os instala junto de propósito — você "
                "escolhe a build certa). CPU: `pip install torch torchaudio "
                "torchcodec --index-url https://download.pytorch.org/whl/cpu"
                "`. GPU NVIDIA/CUDA: veja https://pytorch.org/get-started/"
                "locally/"
            ) from exc
        raise RuntimeError(
            f"coqui-tts está instalado, mas falhou ao carregar: {exc}"
        ) from exc

    _show_cpml_notice_once()
    mdl_name = model or LOCAL_TTS_MODEL
    device = _resolve_local_device()
    key = (mdl_name, device)
    tts = _LOCAL_MODEL_CACHE.get(key)
    if tts is None:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"torch\.jit\.script is deprecated\.*",
                category=FutureWarning,
            )
            tts = TTS(mdl_name).to(device)
        _LOCAL_MODEL_CACHE[key] = tts
    return tts


def _synthesize_local(text: str, dest: Path, model: str | None = None) -> None:
    """Sintetiza localmente com XTTS-v2 (offline, open-source, pt nativo).

    Com ``BAUER_TTS_SPEAKER_WAV``, usa o áudio como referência para a voz
    clonada. Sem referência, mantém o fallback para o primeiro speaker
    embutido do modelo.
    """
    tts = _load_local_model(model)
    reference = local_tts_speaker_wav()
    if reference is not None:
        if not reference.is_file():
            raise RuntimeError(f"WAV de referência não encontrado: {reference}")
        _configure_ffmpeg_runtime()
        tts.tts_to_file(
            text=text,
            file_path=str(dest),
            language=LOCAL_TTS_LANGUAGE,
            speaker_wav=str(reference),
        )
        if not dest.exists() or dest.stat().st_size == 0:
            raise RuntimeError("síntese local não produziu áudio")
        return
    speaker = LOCAL_TTS_SPEAKER or None
    if not speaker:
        speakers = list(getattr(tts, "speakers", None) or [])
        speaker = speakers[0] if speakers else None
    if not speaker:
        raise RuntimeError(
            "modelo local carregado sem speakers embutidos — defina TTS_VOICE "
            "com o nome de um speaker do modelo."
        )
    tts.tts_to_file(
        text=text,
        file_path=str(dest),
        language=LOCAL_TTS_LANGUAGE,
        speaker=speaker,
    )
    if not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError("síntese local não produziu áudio")


def preload_local_tts_model() -> bool:
    """Aquece o modelo XTTS-v2 local em background, se o provider ativo for local.

    Mesma razão do preload em transcription.py: carregar o modelo custa
    segundos a dezenas de segundos; melhor pagar isso no boot do que na 1ª
    resposta falada do turno.
    """
    if available_tts_provider() != "local":
        return False

    def _warm() -> None:
        try:
            _load_local_model(LOCAL_TTS_MODEL)
            logger.info("XTTS-v2 local pré-carregado — respostas faladas prontas.")
        except Exception as exc:  # noqa: BLE001 — preload é best-effort
            logger.warning("Preload do XTTS-v2 falhou (%s); carrega on-demand.", exc)

    threading.Thread(target=_warm, name="xtts-preload", daemon=True).start()
    logger.info("Aquecendo modelo XTTS-v2 local em background…")
    return True


def available_tts_provider() -> str | None:
    """Qual provider TTS está disponível agora.

    Respeita TTS_PROVIDER; em 'auto' prioriza local (funciona sem API key,
    combina com o objetivo de "baixar o Bauer e já conseguir usar") e cai
    para OpenAI se coqui-tts não estiver instalado.
    """
    pref = _tts_provider_pref()
    if pref == "kokoro":
        return "kokoro" if _kokoro_available() else None
    if pref in ("local", "coqui", "xtts"):
        return "local" if _coqui_tts_available() else None
    if pref == "openai":
        return "openai" if os.environ.get("OPENAI_API_KEY", "").strip() else None
    # auto: local primeiro (offline, sem key), cloud como fallback
    if _coqui_tts_available():
        return "local"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    return None


def synthesize_speech(
    text: str,
    output_path: str | Path | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Sintetiza `text` em áudio (.wav).

    Retorna ``{"success": bool, "path": str, "provider": str}`` ou
    ``{"success": False, "path": "", "error": str}``. Nunca levanta — quem
    chama (CLI, /listen do agent interativo) precisa degradar com mensagem
    amigável, não crashar o turno.

    Sem `output_path`, escreve num arquivo temporário (chamador decide se
    apaga depois de tocar).
    """
    text = strip_emoji_for_speech(text)
    err = _validate_text(text)
    if err:
        return {"success": False, "path": "", "error": err}

    if output_path is not None:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        fd, tmp_name = tempfile.mkstemp(suffix=".wav", prefix="bauer-tts-")
        os.close(fd)
        dest = Path(tmp_name)

    attempts: list[str] = []
    pref = _tts_provider_pref()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if pref == "kokoro":
        attempts.append("kokoro")
    elif pref in ("local", "coqui", "xtts"):
        attempts.append("local")
    elif pref == "openai":
        if openai_key:
            attempts.append("openai")
    else:  # auto: local primeiro (sem key), cloud como fallback
        if _coqui_tts_available():
            attempts.append("local")
        if openai_key:
            attempts.append("openai")

    if not attempts:
        return {
            "success": False,
            "path": "",
            "error": (
                "Nenhum provider TTS disponível. Opções: (1) local offline — "
                "`pip install coqui-tts` (ou `uv sync --extra voice-tts`), ou "
                "(2) OPENAI_API_KEY."
            ),
        }

    errors: list[str] = []
    for provider in attempts:
        try:
            if provider == "local":
                _synthesize_local(text, dest, model)
            elif provider == "kokoro":
                from .voice_kokoro import synthesize_kokoro_speech

                synthesize_kokoro_speech(text, dest)
            else:
                _post_openai_tts(text, dest)
            logger.info(
                "Sintetizado via %s (%d chars) -> %s", provider, len(text), dest
            )
            return {"success": True, "path": str(dest), "provider": provider}
        except Exception as exc:  # noqa: BLE001 — tenta o próximo provider
            errors.append(f"{provider}: {exc}")
            logger.warning("TTS %s falhou: %s", provider, exc)

    with suppress(OSError):
        if dest.exists():
            dest.unlink()

    return {
        "success": False,
        "path": "",
        "error": "Síntese falhou — " + "; ".join(errors),
    }
