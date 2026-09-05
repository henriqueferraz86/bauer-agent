"""Transcrição de áudio (STT) — voice notes do gateway viram texto.

Provider padrão do /listen:
  OpenRouter (``openai/whisper-large-v3-turbo``) — OPENROUTER_API_KEY

Alternativas explícitas via STT_PROVIDER:
  ``groq`` — Groq Whisper (``whisper-large-v3-turbo``)
  ``openai`` — OpenAI Whisper (``whisper-1``)
  ``local`` — faster-whisper (``large-v3-turbo``), offline

Selecione explicitamente com a env ``STT_PROVIDER`` = openrouter | auto | local |
groq | openai. Sem a variável, o padrão é ``openrouter``. O idioma padrão é
português (``STT_LANGUAGE=pt``); deixe vazio para detecção automática.
Para rodar o modelo open-source 100% offline::

    pip install faster-whisper          # ou: uv sync --extra voice
    export STT_PROVIDER=local           # Windows: set STT_PROVIDER=local
    # opcionais: STT_LOCAL_MODEL (large-v3-turbo), STT_LOCAL_DEVICE (auto|cpu|cuda),
    #            STT_LOCAL_COMPUTE (int8|float16)

Uso::

    from bauer.transcription import transcribe_audio
    result = transcribe_audio("workspace/.bauer_gateway/media/voice_1.ogg")
    if result["success"]:
        print(result["transcript"])

OpenRouter usa o endpoint STT JSON/base64; os demais clouds usam multipart.
O local é lazy-import: faster-whisper só é exigido quando STT_PROVIDER=local
ou como fallback em auto se instalado.
"""

from __future__ import annotations

import base64
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
OPENROUTER_STT_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
OPENROUTER_STT_MODEL = os.environ.get(
    "STT_OPENROUTER_MODEL", "openai/whisper-large-v3-turbo"
)

# US$ 0,000003 por segundo = US$ 0,0108 por hora.
OPENROUTER_STT_PRICE_PER_SECOND_USD = 0.000003

# Local (faster-whisper) — roda os pesos open-source OFFLINE na máquina, sem API.
# whisper-large-v3-turbo é o mesmo modelo do Groq, mas aqui sem nuvem.
LOCAL_STT_MODEL = os.environ.get("STT_LOCAL_MODEL", "large-v3-turbo")
LOCAL_STT_DEVICE = os.environ.get("STT_LOCAL_DEVICE", "auto")  # auto | cpu | cuda
LOCAL_STT_COMPUTE = os.environ.get("STT_LOCAL_COMPUTE", "int8")  # int8 (CPU) | float16 (GPU)

_TIMEOUT_S = 120.0


def _stt_language() -> str:
    """Retorna o idioma STT configurado; português é o padrão do Bauer."""
    return os.environ.get("STT_LANGUAGE", "pt").strip().lower()


def _stt_provider_pref() -> str:
    """Preferência de provider, lida em call-time; OpenRouter é o padrão."""
    return os.environ.get("STT_PROVIDER", "openrouter").strip().lower()


def openrouter_stt_setup_message() -> str:
    """Mensagem exibida quando o /listen ainda não tem a chave do OpenRouter."""
    hourly = OPENROUTER_STT_PRICE_PER_SECOND_USD * 3600
    return (
        "O /listen usa o OpenRouter por padrão e precisa de uma API key.\n"
        "Crie sua chave em https://openrouter.ai/keys e configure no Windows:\n"
        "  [Environment]::SetEnvironmentVariable(\"OPENROUTER_API_KEY\", "
        "\"sk-or-v1-...\", \"User\")\n"
        "Depois feche e reabra o terminal.\n\n"
        "Tabela de preço do openai/whisper-large-v3-turbo:\n"
        f"  1 hora:      US$ {hourly:.4f}\n"
        f"  100 horas:   US$ {hourly * 100:.2f}\n"
        f"  1.000 horas: US$ {hourly * 1000:.2f}\n"
        "Preço calculado a US$ 0,000003 por segundo; o valor final depende do "
        "uso medido pelo OpenRouter."
    )


def stt_unavailable_message() -> str:
    """Retorna uma instrução específica para o provider STT selecionado."""
    pref = _stt_provider_pref()
    if pref in ("openrouter", "openrouter-stt"):
        return openrouter_stt_setup_message()
    if pref == "groq":
        return "GROQ_API_KEY não configurada. Configure a chave ou use STT_PROVIDER=local."
    if pref == "openai":
        return "OPENAI_API_KEY não configurada. Configure a chave ou use STT_PROVIDER=local."
    if pref in ("local", "faster-whisper", "faster_whisper"):
        return (
            "faster-whisper não instalado. Rode `uv sync --extra voice` "
            "ou use STT_PROVIDER=openrouter com OPENROUTER_API_KEY."
        )
    return (
        "Nenhum provider STT disponível. Configure OPENROUTER_API_KEY, "
        "GROQ_API_KEY/OPENAI_API_KEY, ou instale faster-whisper para uso local."
    )

# Cache do modelo local — carregar os pesos é caro; reusa entre transcrições.
_LOCAL_MODEL_CACHE: dict[tuple, Any] = {}


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


def _post_whisper(
    url: str,
    api_key: str,
    model: str,
    path: Path,
    language: str = "",
) -> dict[str, Any]:
    """POST multipart num endpoint /audio/transcriptions OpenAI-compat."""
    import httpx

    with path.open("rb") as fh:
        data = {"model": model, "response_format": "json"}
        if language:
            data["language"] = language
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (path.name, fh)},
            data=data,
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
        except Exception:  # noqa: BLE001 — corpo de erro pode não ser JSON
            logger.debug("resposta de erro do provider não era JSON", exc_info=True)
        raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
    text = (resp.json().get("text") or "").strip()
    if not text:
        raise RuntimeError("transcrição vazia")
    return {"success": True, "transcript": text}


def _post_openrouter_whisper(
    api_key: str,
    model: str,
    path: Path,
    language: str = "",
) -> dict[str, Any]:
    """Transcreve pelo endpoint STT nativo do OpenRouter.

    O OpenRouter recebe o áudio em JSON/base64 no campo ``input_audio``.
    """
    import httpx

    suffix = path.suffix.lower().lstrip(".")
    audio_format = {
        "oga": "ogg",
        "opus": "ogg",
        "mpga": "mp3",
        "mpeg": "mp3",
    }.get(suffix, suffix)
    payload = {
        "model": model,
        "input_audio": {
            "data": base64.b64encode(path.read_bytes()).decode("ascii"),
            "format": audio_format,
        },
    }
    if language:
        payload["language"] = language
    resp = httpx.post(
        OPENROUTER_STT_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/bauer-agent",
            "X-Title": "Bauer Agent",
        },
        json=payload,
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
        except Exception:  # noqa: BLE001 — corpo de erro pode não ser JSON
            logger.debug("resposta de erro do OpenRouter não era JSON", exc_info=True)
        raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
    text = (resp.json().get("text") or "").strip()
    if not text:
        raise RuntimeError("transcrição vazia")
    return {"success": True, "transcript": text}


def _faster_whisper_available() -> bool:
    """True se o pacote faster-whisper estiver instalado (sem importá-lo de fato)."""
    import importlib.util
    try:
        return importlib.util.find_spec("faster_whisper") is not None
    except (ImportError, ValueError):
        return False


def _load_local_model(model: str | None = None):
    """Carrega (ou reusa do cache) o WhisperModel local. Baixa os pesos na 1ª vez.

    Separado de _transcribe_local para o preload no boot do gateway poder aquecer
    o cache sem transcrever nada.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper não instalado. Rode `pip install faster-whisper` "
            "(ou `uv sync --extra voice`) para transcrição local offline."
        ) from exc

    mdl_name = model or LOCAL_STT_MODEL
    key = (mdl_name, LOCAL_STT_DEVICE, LOCAL_STT_COMPUTE)
    wm = _LOCAL_MODEL_CACHE.get(key)
    if wm is None:
        wm = WhisperModel(mdl_name, device=LOCAL_STT_DEVICE, compute_type=LOCAL_STT_COMPUTE)
        _LOCAL_MODEL_CACHE[key] = wm
    return wm


def _transcribe_local(
    path: Path,
    model: str | None = None,
    language: str = "",
) -> dict[str, Any]:
    """Transcreve localmente com faster-whisper (offline, open-source).

    Carrega o modelo uma vez por (modelo, device, compute) e mantém em cache.
    O primeiro uso baixa os pesos do HuggingFace (~1.5GB para large-v3-turbo);
    depois roda 100% offline.
    """
    wm = _load_local_model(model)
    kwargs = {"language": language} if language else {}
    segments, _info = wm.transcribe(str(path), **kwargs)
    text = "".join(seg.text for seg in segments).strip()
    if not text:
        raise RuntimeError("transcrição vazia")
    return {"success": True, "transcript": text}


def preload_local_model() -> bool:
    """Aquece o modelo Whisper local em background, se o provider ativo for local.

    Chamado no boot do gateway para a 1ª voice note não pagar os ~86s de carga
    no meio do turno (o que estourava o typing e travava a resposta). Não bloqueia:
    dispara uma thread daemon. Retorna True se disparou o preload.
    """
    if available_stt_provider() != "local":
        return False

    import threading

    def _warm() -> None:
        try:
            _load_local_model(LOCAL_STT_MODEL)
            logger.info("Whisper local (%s) pré-carregado — voice notes prontas.", LOCAL_STT_MODEL)
        except Exception as exc:  # noqa: BLE001 — preload é best-effort
            logger.warning("preload do Whisper local falhou (%s); carrega on-demand.", exc)

    threading.Thread(target=_warm, name="whisper-preload", daemon=True).start()
    logger.info("Aquecendo modelo Whisper local (%s) em background…", LOCAL_STT_MODEL)
    return True


def available_stt_provider() -> str | None:
    """Qual provider STT está disponível agora.

    Sem STT_PROVIDER, exige OpenRouter. ``auto`` preserva a cadeia de fallback.
    """
    pref = _stt_provider_pref()
    if pref in ("openrouter", "openrouter-stt"):
        return "openrouter" if os.environ.get("OPENROUTER_API_KEY", "").strip() else None
    if pref in ("local", "faster-whisper", "faster_whisper"):
        return "local" if _faster_whisper_available() else None
    if pref == "groq":
        return "groq" if os.environ.get("GROQ_API_KEY", "").strip() else None
    if pref == "openai":
        return "openai" if os.environ.get("OPENAI_API_KEY", "").strip() else None
    # auto: OpenRouter, outros clouds e local como fallback
    if os.environ.get("OPENROUTER_API_KEY", "").strip():
        return "openrouter"
    if os.environ.get("GROQ_API_KEY", "").strip():
        return "groq"
    if os.environ.get("OPENAI_API_KEY", "").strip():
        return "openai"
    if _faster_whisper_available():
        return "local"
    return None


def transcribe_audio(file_path: str | Path, model: str | None = None) -> dict[str, Any]:
    """Transcreve um arquivo de áudio pelo provider configurado.

    Retorna ``{"success": bool, "transcript": str, "provider": str}`` ou
    ``{"success": False, "transcript": "", "error": str}``. Nunca levanta —
    o gateway precisa degradar com mensagem amigável, não crashar o turno.
    """
    err = _validate(file_path)
    if err:
        return {"success": False, "transcript": "", "error": err}
    path = Path(file_path)
    language = _stt_language()

    # (provider, url, key, model) — url/key são None no provider local.
    attempts: list[tuple[str, str | None, str | None, str]] = []
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    def _add_openrouter():
        if openrouter_key:
            attempts.append(
                ("openrouter", OPENROUTER_STT_URL, openrouter_key,
                 model or OPENROUTER_STT_MODEL)
            )

    def _add_groq():
        if groq_key:
            attempts.append(("groq", GROQ_STT_URL, groq_key, model or GROQ_STT_MODEL))

    def _add_openai():
        if openai_key:
            attempts.append(("openai", OPENAI_STT_URL, openai_key, model or OPENAI_STT_MODEL))

    def _add_local():
        attempts.append(("local", None, None, model or LOCAL_STT_MODEL))

    pref = _stt_provider_pref()
    if pref in ("openrouter", "openrouter-stt"):
        _add_openrouter()
    elif pref in ("local", "faster-whisper", "faster_whisper"):
        _add_local()
    elif pref == "groq":
        _add_groq()
    elif pref == "openai":
        _add_openai()
    else:  # auto: OpenRouter, outros clouds e local como fallback
        _add_openrouter()
        _add_groq()
        _add_openai()
        if _faster_whisper_available():
            _add_local()

    if not attempts:
        return {
            "success": False,
            "transcript": "",
            "error": stt_unavailable_message(),
        }

    errors: list[str] = []
    for provider, url, key, mdl in attempts:
        try:
            if provider == "openrouter":
                result = _post_openrouter_whisper(key, mdl, path, language)
            elif provider == "local":
                result = _transcribe_local(path, mdl, language)
            else:
                result = _post_whisper(url, key, mdl, path, language)
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
