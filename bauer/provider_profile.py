"""Provider Profile — declarative registry of all LLM providers.

Each ProviderProfile captures:
  - auth_type: how to authenticate (api_key, oauth, device_flow, none)
  - env_vars: which environment variables carry credentials
  - base_url: canonical API endpoint
  - chat_path: path suffix for chat completions (default: /v1/chat/completions)
  - no_v1_prefix: True if endpoint already includes version path (gemini, github, etc.)
  - description: human-readable one-liner
  - models_url: endpoint to list available models (optional)
  - extra_headers: static headers required (e.g. Copilot-Integration-Id)
  - supports_streaming: whether SSE streaming is available
  - supports_tools: whether native function calling is available

Usage::

    from bauer.provider_profile import get_profile, list_providers

    profile = get_profile("anthropic")
    print(profile.env_vars)          # ["ANTHROPIC_API_KEY"]
    print(profile.auth_type)         # "api_key"

    models = await profile.fetch_models(api_key="sk-ant-...")
    # ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", ...]
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# ProviderProfile dataclass
# ---------------------------------------------------------------------------


@dataclass
class ProviderProfile:
    """Declarative description of one LLM provider."""

    name: str
    """Canonical provider id used in config.yaml (e.g. 'anthropic')."""

    display_name: str
    """Human-readable name (e.g. 'Anthropic')."""

    description: str
    """One-line description shown in model picker."""

    auth_type: str
    """One of: 'api_key', 'oauth', 'device_flow', 'none'."""

    env_vars: list[str] = field(default_factory=list)
    """Environment variable names that carry the credentials."""

    base_url: str = ""
    """Canonical API base URL (no trailing slash)."""

    chat_path: str = "/v1/chat/completions"
    """Path suffix for chat completions endpoint."""

    no_v1_prefix: bool = False
    """True when chat_path already contains version (gemini, github, copilot)."""

    models_url: str = ""
    """URL to fetch available model ids (GET, auth: Bearer token)."""

    extra_headers: dict[str, str] = field(default_factory=dict)
    """Static HTTP headers always sent with every request."""

    supports_streaming: bool = True
    """Whether the provider supports SSE streaming."""

    supports_tools: bool = True
    """Whether the provider supports native function calling."""

    wire_protocol: str = "openai"
    """One of: 'openai' (default), 'anthropic', 'ollama'."""

    default_context: int = 32768
    """Default context window (tokens) when config doesn't override.

    FONTE ÚNICA de contexto por provider — preflight e context_manager leem
    daqui. Antes existiam 3 mapas divergentes (bug real 2026-06-10: opencode
    65536 no preflight vs 128000 no context_manager).
    """

    is_free: bool = False
    """True quando o provider não cobra por uso (local, sem API key, ou tier gratuito real)."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_api_key(self) -> str | None:
        """Return the first non-empty value from env_vars, or None."""
        for var in self.env_vars:
            val = os.environ.get(var, "").strip()
            if val:
                return val
        return None

    def is_configured(self) -> bool:
        """Return True if credentials appear to be present in the environment."""
        if self.auth_type == "none":
            return True
        return self.get_api_key() is not None

    def fetch_models(self, api_key: str | None = None) -> list[str]:
        """Fetch available model ids from the provider API.

        Returns an empty list on any error (graceful degradation).
        Uses a short timeout so the caller isn't blocked.
        """
        if not self.models_url:
            return []
        key = api_key or self.get_api_key() or ""
        try:
            import httpx
            headers: dict[str, str] = dict(self.extra_headers)
            if key:
                headers["Authorization"] = f"Bearer {key}"
            resp = httpx.get(
                self.models_url,
                headers=headers,
                timeout=8.0,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            # OpenAI-compat: {"data": [{"id": "..."}]}
            if "data" in data:
                return [m.get("id", "") for m in data["data"] if m.get("id")]
            # Anthropic: {"models": [{"id": ...}]} | Ollama: {"models": [{"name": ...}]}
            if "models" in data:
                return [
                    m.get("id") or m.get("name", "")
                    for m in data["models"]
                    if isinstance(m, dict) and (m.get("id") or m.get("name"))
                ]
            # Ollama: {"models": [{"name": "..."}]}
            if isinstance(data, list):
                return [m.get("id") or m.get("name", "") for m in data if isinstance(m, dict)]
        except Exception:
            pass
        return []

    def probe(self, api_key: str | None = None) -> bool:
        """Return True if the provider is reachable and credentials are valid."""
        if self.auth_type == "none":
            try:
                import httpx
                resp = httpx.get(
                    self.base_url.rstrip("/") + "/api/tags",  # Ollama health
                    timeout=4.0,
                )
                return resp.status_code < 500
            except Exception:
                return False
        key = api_key or self.get_api_key() or ""
        if not key:
            return False
        models = self.fetch_models(api_key=key)
        return len(models) > 0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PROFILES: dict[str, ProviderProfile] = {}


def _reg(p: ProviderProfile) -> ProviderProfile:
    _PROFILES[p.name] = p
    return p


# ---------------------------------------------------------------------------
# All 15 supported providers
# ---------------------------------------------------------------------------

_reg(ProviderProfile(
    name="ollama",
    default_context=8192,
    display_name="Ollama",
    description="Modelos locais gratuitos (requer Ollama rodando)",
    auth_type="none",
    env_vars=[],
    base_url="http://localhost:11434",
    chat_path="/v1/chat/completions",
    models_url="http://localhost:11434/api/tags",
    wire_protocol="ollama",
    is_free=True,
))

_reg(ProviderProfile(
    name="opencode",
    default_context=65536,
    display_name="OpenCode Zen",
    description="Modelos gratuitos via opencode.ai — sem API key",
    auth_type="none",
    env_vars=["OPENCODE_API_KEY"],
    base_url="https://opencode.ai/zen",
    models_url="https://opencode.ai/zen/v1/models",
    extra_headers={"User-Agent": "opencode/1.15.11"},
    is_free=True,
))

_reg(ProviderProfile(
    name="openrouter",
    default_context=128000,
    display_name="OpenRouter",
    description="200+ modelos: GPT, Claude, Gemini — 1 chave",
    auth_type="api_key",
    env_vars=["OPENROUTER_API_KEY"],
    base_url="https://openrouter.ai/api",
    models_url="https://openrouter.ai/api/v1/models",
))

_reg(ProviderProfile(
    name="openai",
    default_context=128000,
    display_name="ChatGPT OAuth",
    description="Login com conta ChatGPT — sem API key (via browser)",
    auth_type="oauth",
    env_vars=[],
    base_url="https://api.openai.com",
    models_url="https://api.openai.com/v1/models",
))

_reg(ProviderProfile(
    name="openai-api",
    default_context=128000,
    display_name="OpenAI API Key",
    description="ChatGPT com API key (sk-...) — platform.openai.com",
    auth_type="api_key",
    env_vars=["OPENAI_API_KEY"],
    base_url="https://api.openai.com",
    models_url="https://api.openai.com/v1/models",
))

_reg(ProviderProfile(
    name="anthropic",
    default_context=200000,
    display_name="Anthropic",
    description="Claude Haiku, Sonnet, Opus",
    auth_type="api_key",
    env_vars=["ANTHROPIC_API_KEY"],
    base_url="https://api.anthropic.com",
    chat_path="/v1/messages",
    models_url="https://api.anthropic.com/v1/models",
    wire_protocol="anthropic",
))

_reg(ProviderProfile(
    name="gemini",
    default_context=1000000,
    display_name="Google Gemini",
    description="Gemini Flash, Pro (GEMINI_API_KEY)",
    auth_type="api_key",
    env_vars=["GEMINI_API_KEY"],
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    chat_path="/chat/completions",
    no_v1_prefix=True,
))

_reg(ProviderProfile(
    name="groq",
    default_context=131072,
    display_name="Groq",
    description="Llama ultra-rápido, gratuito com limites",
    auth_type="api_key",
    env_vars=["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai",
    models_url="https://api.groq.com/openai/v1/models",
    is_free=True,
))

_reg(ProviderProfile(
    name="mistral",
    default_context=32768,
    display_name="Mistral AI",
    description="Mistral Small/Medium/Large, Codestral",
    auth_type="api_key",
    env_vars=["MISTRAL_API_KEY"],
    base_url="https://api.mistral.ai",
    models_url="https://api.mistral.ai/v1/models",
))

_reg(ProviderProfile(
    name="xai",
    default_context=131072,
    display_name="xAI Grok",
    description="Grok 3 — modelos da xAI/Elon Musk",
    auth_type="api_key",
    env_vars=["XAI_API_KEY"],
    base_url="https://api.x.ai",
    models_url="https://api.x.ai/v1/models",
))

_reg(ProviderProfile(
    name="together",
    default_context=32768,
    display_name="Together AI",
    description="Llama, Mistral, Qwen — hospedagem aberta",
    auth_type="api_key",
    env_vars=["TOGETHER_API_KEY"],
    base_url="https://api.together.xyz",
    models_url="https://api.together.xyz/v1/models",
))

_reg(ProviderProfile(
    name="deepseek",
    default_context=65536,
    display_name="DeepSeek",
    description="DeepSeek V3 e R1 — China, preço baixo",
    auth_type="api_key",
    env_vars=["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
    models_url="https://api.deepseek.com/models",
))

_reg(ProviderProfile(
    name="github",
    default_context=128000,
    display_name="GitHub Models",
    description="GPT-4o, Llama via GitHub (requer GITHUB_TOKEN)",
    auth_type="device_flow",
    env_vars=["GITHUB_TOKEN"],
    base_url="https://models.inference.ai.azure.com",
    chat_path="/chat/completions",
    no_v1_prefix=True,
    extra_headers={"X-GitHub-Api-Version": "2023-07-07"},
    is_free=True,
))

_reg(ProviderProfile(
    name="copilot",
    default_context=128000,
    display_name="GitHub Copilot",
    description="Copilot API — requer 'bauer auth login -p copilot'",
    auth_type="device_flow",
    env_vars=[],
    base_url="https://api.githubcopilot.com",
    chat_path="/chat/completions",
    no_v1_prefix=True,
    extra_headers={
        "Copilot-Integration-Id": "vscode-chat",
        "Editor-Version": "vscode/1.99.0",
        "Editor-Plugin-Version": "copilot-chat/0.26.0",
        "User-Agent": "GitHubCopilotChat/0.26.0",
        "X-GitHub-Api-Version": "2023-07-07",
    },
))

_reg(ProviderProfile(
    name="custom",
    default_context=32768,
    display_name="Custom",
    description="Qualquer endpoint OpenAI-compatible (LM Studio, vLLM…)",
    auth_type="api_key",
    env_vars=["CUSTOM_API_KEY"],
    base_url="",  # set dynamically from config
))

_reg(ProviderProfile(
    name="azure",
    default_context=128000,
    display_name="Azure OpenAI",
    description="OpenAI via Azure — deployment próprio",
    auth_type="api_key",
    env_vars=["AZURE_OPENAI_API_KEY"],
    base_url="",  # https://{endpoint}/openai/deployments/{deployment}
))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_DEFAULT_CONTEXT_FALLBACK = 32768


def get_profile(name: str) -> ProviderProfile | None:
    """Return the ProviderProfile for *name*, or None if unknown."""
    return _PROFILES.get(name)


def get_default_context(provider: str) -> int:
    """Contexto padrão (tokens) de um provider — FONTE ÚNICA.

    preflight (doctor) e context_manager leem daqui. Providers desconhecidos
    recebem fallback conservador de 32768.
    """
    profile = _PROFILES.get(provider)
    return profile.default_context if profile else _DEFAULT_CONTEXT_FALLBACK


def default_context_map() -> dict[str, int]:
    """Mapa {provider: default_context} de todos os profiles registrados."""
    return {name: p.default_context for name, p in _PROFILES.items()}


def list_providers() -> list[ProviderProfile]:
    """Return all registered ProviderProfiles in insertion order."""
    return list(_PROFILES.values())


def providers_by_auth_type(auth_type: str) -> list[ProviderProfile]:
    """Return all profiles with the given auth_type."""
    return [p for p in _PROFILES.values() if p.auth_type == auth_type]


def configured_providers() -> list[ProviderProfile]:
    """Return providers that appear to have credentials in the environment."""
    return [p for p in _PROFILES.values() if p.is_configured()]


def probe_all(timeout_per_provider: float = 6.0) -> dict[str, bool]:
    """Probe all configured providers in parallel.

    Returns {provider_name: is_alive} for each provider that has credentials.
    """
    import concurrent.futures
    targets = configured_providers()
    results: dict[str, bool] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(targets), 8)) as ex:
        futures = {ex.submit(p.probe): p.name for p in targets}
        for fut in concurrent.futures.as_completed(futures, timeout=timeout_per_provider * 2):
            name = futures[fut]
            try:
                results[name] = fut.result(timeout=timeout_per_provider)
            except Exception:
                results[name] = False

    return results


def env_var_status() -> list[dict[str, Any]]:
    """Return a list of {provider, env_var, set: bool} dicts for doctor/status display."""
    rows: list[dict[str, Any]] = []
    for p in _PROFILES.values():
        for var in p.env_vars:
            rows.append({
                "provider": p.name,
                "display_name": p.display_name,
                "env_var": var,
                "set": bool(os.environ.get(var, "").strip()),
                "auth_type": p.auth_type,
            })
    return rows
