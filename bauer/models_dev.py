"""Models.dev registry integration — catálogo comunitário de providers e modelos.

Busca de https://models.dev/api.json — banco comunitário com 4000+ modelos
em 109+ providers. Fornece:

- Metadados de provider: nome, base URL, env vars, doc link
- Metadados de modelo: context window, max output, custo/M tokens, capabilities
  (reasoning, tools, vision, PDF, audio), modalities, knowledge cutoff,
  open-weights flag, família, status de deprecação

Hierarquia de resolução (offline-first):
  1. Cache em disco (~/.bauer/models_dev_cache.json) se < 1h
  2. Rede (https://models.dev/api.json)
  3. Cache expirado em disco (fallback de emergência)

Outros módulos devem importar os dataclasses e funções de query daqui
em vez de parsear o JSON cru diretamente.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"
_OPENROUTER_API_URL = "https://openrouter.ai/api/v1/models"
_CACHE_TTL = 3600  # 1 hora

# In-memory cache — models.dev
_models_dev_cache: Dict[str, Any] = {}
_models_dev_cache_time: float = 0

# In-memory cache — OpenRouter live API
_openrouter_catalog_cache: List[Dict[str, Any]] = []
_openrouter_catalog_time: float = 0


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """Metadados completos de um modelo do models.dev."""

    id: str
    name: str
    family: str
    provider_id: str

    # Capabilities
    reasoning: bool = False
    tool_call: bool = False
    attachment: bool = False
    temperature: bool = False
    structured_output: bool = False
    open_weights: bool = False

    # Modalities
    input_modalities: Tuple[str, ...] = ()
    output_modalities: Tuple[str, ...] = ()

    # Limits
    context_window: int = 0
    max_output: int = 0
    max_input: Optional[int] = None

    # Cost (por milhão de tokens, USD)
    cost_input: float = 0.0
    cost_output: float = 0.0
    cost_cache_read: Optional[float] = None
    cost_cache_write: Optional[float] = None

    # Metadata
    knowledge_cutoff: str = ""
    release_date: str = ""
    status: str = ""

    def supports_vision(self) -> bool:
        return self.attachment or "image" in self.input_modalities

    def supports_pdf(self) -> bool:
        return "pdf" in self.input_modalities

    def supports_audio(self) -> bool:
        return "audio" in self.input_modalities

    def has_cost_data(self) -> bool:
        return self.cost_input > 0 or self.cost_output > 0

    def format_cost(self) -> str:
        if not self.has_cost_data():
            return "desconhecido"
        parts = [f"${self.cost_input:.2f}/M in", f"${self.cost_output:.2f}/M out"]
        if self.cost_cache_read is not None:
            parts.append(f"cache read ${self.cost_cache_read:.2f}/M")
        return ", ".join(parts)

    def format_capabilities(self) -> str:
        caps: list[str] = []
        if self.reasoning:
            caps.append("reasoning")
        if self.tool_call:
            caps.append("tools")
        if self.supports_vision():
            caps.append("vision")
        if self.supports_pdf():
            caps.append("PDF")
        if self.supports_audio():
            caps.append("audio")
        if self.structured_output:
            caps.append("structured output")
        if self.open_weights:
            caps.append("open weights")
        return ", ".join(caps) if caps else "básico"


@dataclass
class ProviderInfo:
    """Metadados completos de um provider do models.dev."""

    id: str
    name: str
    env: Tuple[str, ...]
    api: str
    doc: str = ""
    model_count: int = 0


# ---------------------------------------------------------------------------
# Mapeamento: Bauer provider ID → models.dev provider ID
# ---------------------------------------------------------------------------

PROVIDER_TO_MODELS_DEV: Dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "openai-api": "openai",
    "openrouter": "openrouter",
    "gemini": "google",
    "groq": "groq",
    "mistral": "mistral",
    "xai": "xai",
    "together": "togetherai",
    "deepseek": "deepseek",
    "github": "github-models",
    "copilot": "github-copilot",
    "opencode": "opencode",
    "ollama": "ollama",
    "cohere": "cohere",
    "perplexity": "perplexity",
    "fireworks": "fireworks-ai",
    "huggingface": "huggingface",
    "nvidia": "nvidia",
    "moonshot": "kimi-for-coding",
    "alibaba": "alibaba",
    "vertex": "google",
    "azure": "azure-openai",
    "lmstudio": "lmstudio",
    "databricks": "databricks",
    "sambanova": "sambanova",
    "cerebras": "cerebras",
    "custom": "",  # sem mapping — endpoint customizado
}


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _get_cache_path() -> Path:
    home = Path.home() / ".bauer"
    home.mkdir(exist_ok=True)
    return home / "models_dev_cache.json"


def _load_disk_cache() -> Dict[str, Any]:
    try:
        p = _get_cache_path()
        if p.exists():
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug("models_dev: falha ao carregar cache de disco: %s", e)
    return {}


def _disk_cache_age_seconds() -> Optional[float]:
    try:
        p = _get_cache_path()
        if not p.exists():
            return None
        age = time.time() - p.stat().st_mtime
        return None if age < 0 else age
    except Exception:
        return None


def _save_disk_cache(data: Dict[str, Any]) -> None:
    try:
        p = _get_cache_path()
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(p)
    except Exception as e:
        logger.debug("models_dev: falha ao salvar cache de disco: %s", e)


# ---------------------------------------------------------------------------
# Fetch principal
# ---------------------------------------------------------------------------

def fetch_models_dev(force_refresh: bool = False) -> Dict[str, Any]:
    """Busca catálogo models.dev. Hierarquia: in-mem → disco → rede → disco expirado.

    Retorna dict indexado por provider ID, ou {} em caso de falha total.
    """
    global _models_dev_cache, _models_dev_cache_time

    # Estágio 1: cache in-memory fresco
    if (
        not force_refresh
        and _models_dev_cache
        and (time.time() - _models_dev_cache_time) < _CACHE_TTL
    ):
        return _models_dev_cache

    # Estágio 2: cache em disco fresco (evita rede na maioria dos cold-starts)
    if not force_refresh:
        disk_age = _disk_cache_age_seconds()
        if disk_age is not None and disk_age < _CACHE_TTL:
            disk_data = _load_disk_cache()
            if disk_data:
                _models_dev_cache = disk_data
                _models_dev_cache_time = time.time() - disk_age
                logger.debug(
                    "models_dev: carregado do cache de disco (%d providers, age=%.0fs)",
                    len(disk_data), disk_age,
                )
                return _models_dev_cache

    # Estágio 3: rede
    try:
        import httpx
        resp = httpx.get(MODELS_DEV_URL, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data:
            _models_dev_cache = data
            _models_dev_cache_time = time.time()
            _save_disk_cache(data)
            total_models = sum(
                len(p.get("models", {})) for p in data.values() if isinstance(p, dict)
            )
            logger.debug(
                "models_dev: buscado da rede — %d providers, %d modelos",
                len(data), total_models,
            )
            return data
    except Exception as e:
        logger.debug("models_dev: falha na rede: %s", e)

    # Estágio 4: cache de disco expirado (emergência)
    if not _models_dev_cache:
        _models_dev_cache = _load_disk_cache()
        if _models_dev_cache:
            # TTL curto para tentar rede logo
            _models_dev_cache_time = time.time() - _CACHE_TTL + 300
            logger.debug(
                "models_dev: usando cache de disco expirado (%d providers)",
                len(_models_dev_cache),
            )

    return _models_dev_cache


# ---------------------------------------------------------------------------
# Auto-refresh daemon (background thread, 60-min TTL)
# ---------------------------------------------------------------------------

_refresh_thread: Optional[Any] = None
_refresh_stop = False


def start_background_refresh(interval_sec: int = 3600) -> None:
    """Inicia um daemon thread que re-fetcha models.dev a cada `interval_sec` segundos.

    Idempotent: só inicia um thread. Seguro chamar múltiplas vezes.
    """
    import threading

    global _refresh_thread, _refresh_stop

    if _refresh_thread is not None and _refresh_thread.is_alive():
        return

    _refresh_stop = False

    def _loop() -> None:
        import time as _time

        while not _refresh_stop:
            _time.sleep(interval_sec)
            if _refresh_stop:
                break
            try:
                old_len = len(_models_dev_cache)
                fresh = fetch_models_dev(force_refresh=True)
                new_len = len(fresh)
                if new_len != old_len:
                    logger.info(
                        "models_dev: catálogo atualizado — %d providers (era %d)",
                        new_len, old_len,
                    )
                else:
                    logger.debug("models_dev: refresh automático completo (%d providers)", new_len)
            except Exception as exc:
                logger.debug("models_dev: falha no refresh automático: %s", exc)

    _refresh_thread = threading.Thread(target=_loop, name="models_dev_refresh", daemon=True)
    _refresh_thread.start()
    logger.debug("models_dev: daemon de refresh iniciado (intervalo=%ds)", interval_sec)


def stop_background_refresh() -> None:
    """Para o daemon de refresh (útil em testes)."""
    global _refresh_stop
    _refresh_stop = True


def fetch_openrouter_catalog(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Busca catálogo completo direto da API pública do OpenRouter.

    Hierarquia: in-mem fresco → rede → in-mem expirado.
    Retorna lista de dicts normalizados (mesma forma que catalog_models).
    """
    global _openrouter_catalog_cache, _openrouter_catalog_time

    if (
        not force_refresh
        and _openrouter_catalog_cache
        and (time.time() - _openrouter_catalog_time) < _CACHE_TTL
    ):
        return _openrouter_catalog_cache

    try:
        import httpx

        resp = httpx.get(_OPENROUTER_API_URL, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        raw_list = resp.json().get("data", [])
        if not isinstance(raw_list, list):
            raise ValueError("resposta inesperada da API do OpenRouter")

        result: List[Dict[str, Any]] = []
        for m in raw_list:
            if not isinstance(m, dict):
                continue
            mid = m.get("id", "")
            if not mid:
                continue
            pricing = m.get("pricing") or {}

            def _f(v: Any) -> Optional[float]:
                try:
                    return float(v)
                except Exception:
                    return None

            cost_in = _f(pricing.get("prompt"))
            cost_out = _f(pricing.get("completion"))
            is_free = (
                mid.endswith(":free")
                or mid in ("openrouter/free", "openrouter/owl-alpha")
                or (cost_in == 0.0 and cost_out == 0.0)
            )
            result.append({
                "id": mid,
                "provider": "openrouter",
                "context_window": m.get("context_length"),
                "cost_in": cost_in,
                "cost_out": cost_out,
                "is_free": is_free,
                "capabilities": [],
                "description": m.get("description", ""),
            })

        _openrouter_catalog_cache = result
        _openrouter_catalog_time = time.time()
        logger.debug("openrouter: buscado da API — %d modelos", len(result))
        return result
    except Exception as exc:
        logger.debug("openrouter: falha na API direta: %s", exc)

    return _openrouter_catalog_cache


def _is_free_model(provider_id: str, model_id: str, cost_in: Optional[float], cost_out: Optional[float]) -> bool:
    """Best-effort free-model classification for catalog display."""
    if provider_id == "openrouter":
        return model_id.endswith(":free") or model_id == "openrouter/free"
    if cost_in is None:
        return False
    return cost_in == 0 and (cost_out is None or cost_out == 0)


def catalog_models(
    provider: Optional[str] = None,
    capability: Optional[str] = None,
    max_cost_per_m: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Retorna lista de modelos do catálogo models.dev com filtros opcionais.

    Args:
        provider: filtrar por provider ID (ex: "openai", "anthropic").
        capability: filtrar por capability (ex: "tools", "vision", "reasoning").
        max_cost_per_m: filtrar por custo máximo em USD/M tokens (input).

    Returns:
        Lista de dicts com keys: id, provider, context_window, cost_in, cost_out,
        capabilities, description.
    """
    data = fetch_models_dev()
    results: List[Dict[str, Any]] = []

    for prov_id, pdata in data.items():
        if not isinstance(pdata, dict):
            continue
        if provider and prov_id.lower() != provider.lower():
            continue

        models = pdata.get("models", {})
        if not isinstance(models, dict):
            continue

        for model_id, mdata in models.items():
            if not isinstance(mdata, dict):
                continue

            # Extract context window
            limit = mdata.get("limit", {})
            context_window = limit.get("context") if isinstance(limit, dict) else None

            # Extract costs
            pricing = mdata.get("pricing", {})
            cost_in: Optional[float] = None
            cost_out: Optional[float] = None
            if isinstance(pricing, dict):
                cost_in = pricing.get("input") or pricing.get("input_per_token")
                cost_out = pricing.get("output") or pricing.get("output_per_token")

            # Extract capabilities
            caps: list = []
            if isinstance(mdata.get("supports_tools"), bool) and mdata["supports_tools"]:
                caps.append("tools")
            if isinstance(mdata.get("supports_vision"), bool) and mdata["supports_vision"]:
                caps.append("vision")
            if isinstance(mdata.get("supports_reasoning"), bool) and mdata["supports_reasoning"]:
                caps.append("reasoning")
            # also check modalities list
            for modal in mdata.get("modalities", []):
                if isinstance(modal, str) and modal not in caps:
                    caps.append(modal)

            # Capability filter
            if capability and capability.lower() not in [c.lower() for c in caps]:
                continue

            # Cost filter
            if max_cost_per_m is not None and cost_in is not None:
                # cost_in might be in per-token (multiply by 1M) or already per-M
                effective = cost_in * 1_000_000 if cost_in < 0.01 else cost_in
                if effective > max_cost_per_m:
                    continue

            results.append({
                "id": model_id,
                "provider": prov_id,
                "context_window": context_window,
                "cost_in": cost_in,
                "cost_out": cost_out,
                "is_free": _is_free_model(prov_id, model_id, cost_in, cost_out),
                "capabilities": caps,
                "description": mdata.get("description", ""),
            })

    # Merge OpenRouter from live API (authoritative — models.dev is often stale/incomplete)
    if provider is None or provider.lower() == "openrouter":
        results = [r for r in results if r["provider"] != "openrouter"]
        results.extend(fetch_openrouter_catalog())

    # Sort: free first globally, then cheapest, then provider/id
    results.sort(key=lambda r: (
        0 if r.get("is_free") else 1,
        r.get("cost_in") or 999.0,
        r["provider"],
        r["id"],
    ))
    return results


# ---------------------------------------------------------------------------
# Helpers internos de lookup
# ---------------------------------------------------------------------------

def _get_provider_models(provider: str) -> Optional[Dict[str, Any]]:
    mdev_id = PROVIDER_TO_MODELS_DEV.get(provider)
    if not mdev_id:
        return None
    data = fetch_models_dev()
    pdata = data.get(mdev_id)
    if not isinstance(pdata, dict):
        return None
    models = pdata.get("models", {})
    return models if isinstance(models, dict) else None


def _find_model_entry(models: Dict[str, Any], model_id: str) -> Optional[Dict[str, Any]]:
    entry = models.get(model_id)
    if isinstance(entry, dict):
        return entry
    model_lower = model_id.lower()
    for mid, mdata in models.items():
        if mid.lower() == model_lower and isinstance(mdata, dict):
            return mdata
    return None


def _extract_context(entry: Dict[str, Any]) -> Optional[int]:
    if not isinstance(entry, dict):
        return None
    limit = entry.get("limit")
    if not isinstance(limit, dict):
        return None
    ctx = limit.get("context")
    if isinstance(ctx, (int, float)) and ctx > 0:
        return int(ctx)
    return None


# ---------------------------------------------------------------------------
# Filtros de ruído para catálogos
# ---------------------------------------------------------------------------

_NOISE_PATTERNS: re.Pattern = re.compile(
    r"-tts\b|embedding|live-|-(preview|exp)-\d{2,4}[-_]|"
    r"-image\b|-image-preview\b|-customtools\b",
    re.IGNORECASE,
)


def _should_hide_from_catalog(provider: str, model_id: str) -> bool:
    return False  # extender por provider se necessário


# ---------------------------------------------------------------------------
# API pública — queries
# ---------------------------------------------------------------------------

def lookup_context_window(provider: str, model: str) -> Optional[int]:
    """Retorna o context window (tokens) de provider+modelo via models.dev.

    Retorna None se não encontrado.
    """
    models = _get_provider_models(provider)
    if models is None:
        return None
    entry = _find_model_entry(models, model)
    if entry is None:
        return None
    return _extract_context(entry)


def get_model_info(provider_id: str, model_id: str) -> Optional[ModelInfo]:
    """Retorna ModelInfo completo para provider+modelo. None se não encontrado."""
    mdev_id = PROVIDER_TO_MODELS_DEV.get(provider_id, provider_id)
    data = fetch_models_dev()
    pdata = data.get(mdev_id)
    if not isinstance(pdata, dict):
        return None
    models = pdata.get("models", {})
    if not isinstance(models, dict):
        return None
    raw = _find_model_entry(models, model_id)
    if raw is None:
        return None
    return _parse_model_info(model_id, raw, mdev_id)


def get_provider_info(provider_id: str) -> Optional[ProviderInfo]:
    """Retorna ProviderInfo completo para um provider. None se não encontrado."""
    mdev_id = PROVIDER_TO_MODELS_DEV.get(provider_id, provider_id)
    data = fetch_models_dev()
    raw = data.get(mdev_id)
    if not isinstance(raw, dict):
        return None
    return _parse_provider_info(mdev_id, raw)


def list_provider_models(provider: str) -> List[str]:
    """Retorna todos os model IDs de um provider do models.dev. Lista vazia se não encontrado."""
    models = _get_provider_models(provider)
    if models is None:
        return []
    return [
        mid for mid in models.keys()
        if not _should_hide_from_catalog(provider, mid)
    ]


def list_agentic_models(provider: str) -> List[str]:
    """Retorna model IDs aptos para uso agêntico (tool_call=True, sem ruído).

    Filtra TTS, embedding, live, preview com timestamp, image-only.
    """
    models = _get_provider_models(provider)
    if models is None:
        return []
    result: list[str] = []
    for mid, entry in models.items():
        if not isinstance(entry, dict):
            continue
        if _should_hide_from_catalog(provider, mid):
            continue
        if not entry.get("tool_call", False):
            continue
        if _NOISE_PATTERNS.search(mid):
            continue
        result.append(mid)
    return result


def get_model_capabilities(provider: str, model: str) -> Optional[Dict[str, Any]]:
    """Retorna dict de capabilities do modelo. None se não encontrado.

    Chaves: supports_tools, supports_vision, supports_reasoning,
            context_window, max_output_tokens, model_family.
    """
    models = _get_provider_models(provider)
    if models is None:
        return None
    entry = _find_model_entry(models, model)
    if entry is None:
        return None

    input_mods = entry.get("modalities", {})
    if isinstance(input_mods, dict):
        input_mods = input_mods.get("input")
    if isinstance(input_mods, list):
        supports_vision = "image" in input_mods
    else:
        supports_vision = bool(entry.get("attachment", False))

    limit = entry.get("limit") or {}
    ctx = limit.get("context")
    out = limit.get("output")

    return {
        "supports_tools": bool(entry.get("tool_call", False)),
        "supports_vision": supports_vision,
        "supports_reasoning": bool(entry.get("reasoning", False)),
        "context_window": int(ctx) if isinstance(ctx, (int, float)) and ctx > 0 else 0,
        "max_output_tokens": int(out) if isinstance(out, (int, float)) and out > 0 else 0,
        "model_family": entry.get("family", "") or "",
        "open_weights": bool(entry.get("open_weights", False)),
        "cost_input": float((entry.get("cost") or {}).get("input", 0) or 0),
        "cost_output": float((entry.get("cost") or {}).get("output", 0) or 0),
    }


# ---------------------------------------------------------------------------
# Parsers internos
# ---------------------------------------------------------------------------

def _parse_model_info(model_id: str, raw: Dict[str, Any], provider_id: str) -> ModelInfo:
    limit = raw.get("limit") or {}
    if not isinstance(limit, dict):
        limit = {}
    cost = raw.get("cost") or {}
    if not isinstance(cost, dict):
        cost = {}
    modalities = raw.get("modalities") or {}
    if not isinstance(modalities, dict):
        modalities = {}

    input_mods = modalities.get("input") or []
    output_mods = modalities.get("output") or []

    ctx = limit.get("context")
    out = limit.get("output")
    inp = limit.get("input")

    return ModelInfo(
        id=model_id,
        name=raw.get("name", "") or model_id,
        family=raw.get("family", "") or "",
        provider_id=provider_id,
        reasoning=bool(raw.get("reasoning", False)),
        tool_call=bool(raw.get("tool_call", False)),
        attachment=bool(raw.get("attachment", False)),
        temperature=bool(raw.get("temperature", False)),
        structured_output=bool(raw.get("structured_output", False)),
        open_weights=bool(raw.get("open_weights", False)),
        input_modalities=tuple(input_mods) if isinstance(input_mods, list) else (),
        output_modalities=tuple(output_mods) if isinstance(output_mods, list) else (),
        context_window=int(ctx) if isinstance(ctx, (int, float)) and ctx > 0 else 0,
        max_output=int(out) if isinstance(out, (int, float)) and out > 0 else 0,
        max_input=int(inp) if isinstance(inp, (int, float)) and inp > 0 else None,
        cost_input=float(cost.get("input", 0) or 0),
        cost_output=float(cost.get("output", 0) or 0),
        cost_cache_read=float(cost["cache_read"]) if "cache_read" in cost and cost["cache_read"] is not None else None,
        cost_cache_write=float(cost["cache_write"]) if "cache_write" in cost and cost["cache_write"] is not None else None,
        knowledge_cutoff=raw.get("knowledge", "") or "",
        release_date=raw.get("release_date", "") or "",
        status=raw.get("status", "") or "",
    )


def _parse_provider_info(provider_id: str, raw: Dict[str, Any]) -> ProviderInfo:
    env = raw.get("env") or []
    models = raw.get("models") or {}
    return ProviderInfo(
        id=provider_id,
        name=raw.get("name", "") or provider_id,
        env=tuple(env) if isinstance(env, list) else (),
        api=raw.get("api", "") or "",
        doc=raw.get("doc", "") or "",
        model_count=len(models) if isinstance(models, dict) else 0,
    )
