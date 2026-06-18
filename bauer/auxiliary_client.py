"""Auxiliary LLM clients — cheap/fast models for routine subtasks.

Each Bauer subsystem can opt to route its LLM calls to a different
(provider, model) than the user's main agent model. Typical use cases:
    - kanban_decompose: split a large task into 2-6 children (medium model)
    - kanban_specify: promote a triage idea to a structured spec (small model)
    - context compression: summarise old turns (very small model)

The slot system is opt-in. When `config.auxiliary.<slot>` is empty (default),
the main model is reused — so existing config.yaml files don't need editing.

Public surface::

    from bauer.auxiliary_client import get_text_auxiliary_client

    client, model_name = get_text_auxiliary_client("kanban_decomposer")
    if client is None:
        # graceful fallback — caller should keep the user moving
        return SpecifyOutcome(ok=False, reason="auxiliary unavailable")

    response = "".join(client.chat_stream(model_name, messages))

The returned client implements `chat_stream(model, messages) -> Iterator[str]`
just like the main agent client, so callers don't need provider-specific
branches.

Failure modes are *always* gracious:
    - Slot not configured + main model unavailable → (None, None)
    - Provider not built (import / auth error) → (None, None)
    - Returning None lets callers degrade to a deterministic fallback
      (e.g. decompose-without-LLM = single child equal to the parent)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public type
# ---------------------------------------------------------------------------


@runtime_checkable
class TextLLMClient(Protocol):
    """Minimal interface every auxiliary client must satisfy.

    `chat_stream` returns an iterator of text chunks; `default_model` is the
    model name to pass back so callers don't need to remember the slot's
    resolved (provider, model) pair separately.
    """

    default_model: str

    def chat_stream(self, model: str, messages: list[dict]) -> Any:
        """Yield text chunks. Caller joins them via ''.join(...)."""
        ...


# Slots recognised by `get_text_auxiliary_client`. Keep this list in sync with
# AuxiliarySection in config_loader.py — there's no run-time inspection of the
# Pydantic model because we want this module to stay importable without a
# loaded config (e.g. for unit tests with a stub).
VALID_SLOTS: frozenset[str] = frozenset({
    "kanban_decomposer",
    "triage_specifier",
    "compression_model",
    "background_reviewer",  # G10
    "approval_model",       # G4 — revisao LLM de tools de alto risco
})


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _resolve_slot(slot: str, cfg) -> tuple[str, str]:
    """Return the (provider, model) to use for `slot`.

    Falls back to the main `model.provider` / `model.name` when the slot's
    fields are empty. Empty strings here mean "use main", *not* "use nothing"
    — the caller can still get a working client from the main config.

    Returns a tuple of two strings; either or both may be empty when the
    main config is also unset (rare — only happens in misconfigured tests).
    """
    aux = getattr(getattr(cfg, "auxiliary", None), slot, None)
    main = getattr(cfg, "model", None)
    main_provider = getattr(main, "provider", "") if main else ""
    main_model = getattr(main, "name", "") if main else ""

    provider = (getattr(aux, "provider", "") or main_provider).strip()
    model = (getattr(aux, "model", "") or main_model).strip()
    return provider, model


def get_text_auxiliary_client(
    slot: str,
    cfg=None,
) -> tuple[TextLLMClient | None, str | None]:
    """Build (or return) an auxiliary text-generating client for `slot`.

    Args:
        slot: Name of the auxiliary slot. Must be in `VALID_SLOTS`.
        cfg: Loaded `BauerConfig`. If `None`, attempts to load `config.yaml`
            from the current working directory; on failure returns
            `(None, None)` instead of raising — auxiliary use cases are
            best-effort by design.

    Returns:
        `(client, model_name)` on success.
        `(None, None)` when:
            - the slot name is not in `VALID_SLOTS`
            - neither slot config nor main config define provider/model
            - the provider client class can't be imported / authenticated
        Callers MUST check `client is None` and provide a deterministic
        fallback path.

    Best-effort semantics — this function never raises. All exceptions are
    logged at INFO and converted to `(None, None)` so the caller's main
    flow stays predictable.
    """
    if slot not in VALID_SLOTS:
        logger.info("auxiliary_client: unknown slot %r; valid=%s",
                    slot, sorted(VALID_SLOTS))
        return None, None

    if cfg is None:
        cfg = _try_load_default_config()
        if cfg is None:
            return None, None

    provider, model = _resolve_slot(slot, cfg)
    if not provider or not model:
        logger.info("auxiliary_client: slot %r unconfigured (provider=%r, "
                    "model=%r) — caller should fall back",
                    slot, provider, model)
        return None, None

    try:
        client = _build_client_for_provider(provider, model, cfg)
    except Exception as exc:
        # Build failures are routine in test environments — log at INFO,
        # not WARNING, to avoid log noise. Real misconfiguration in
        # production will surface anyway when the user tries to use the
        # main chat (same provider).
        logger.info("auxiliary_client: failed to build %r/%r for slot %r: %s",
                    provider, model, slot, exc)
        return None, None

    return client, model


# ---------------------------------------------------------------------------
# Client builders
# ---------------------------------------------------------------------------


def _try_load_default_config():
    """Best-effort load of config.yaml. Returns None on failure."""
    try:
        from .config_loader import load_config
        # Look for config.yaml in CWD; tests can override via env var.
        path = os.environ.get("BAUER_CONFIG", "config.yaml")
        return load_config(path)
    except Exception as exc:
        logger.info("auxiliary_client: cfg autoload failed (%s); "
                    "callers must pass cfg explicitly", exc)
        return None


def _provider_section(cfg, provider: str):
    """Return the per-provider config section (cfg.openai / cfg.anthropic / ...).

    Falls back to `None` when no section exists — callers tolerate that.
    """
    name = provider.lower()
    return getattr(cfg, name, None)


def _build_client_for_provider(provider: str, model: str, cfg) -> TextLLMClient:
    """Construct a client object for `provider`. Raises on unsupported provider.

    Supports the 4 most-common families for auxiliary work:
        - openai-compatible (openai, groq, mistral, xai, together, deepseek,
          openrouter, opencode, custom, gemini in compat mode)
        - anthropic (native client)
        - ollama (local)
        - github / azure (treated as openai-compat via shared OpenAIClient)

    Provider names follow the same conventions as the main config sections.
    """
    p = provider.lower().strip()

    # --- Anthropic native ---------------------------------------------------
    if p == "anthropic":
        from .anthropic_client import AnthropicClient
        section = _provider_section(cfg, "anthropic")
        api_key = getattr(section, "api_key", "") if section else ""
        timeout = int(getattr(section, "timeout_seconds", 60) if section else 60)
        return AnthropicClient(api_key=api_key, timeout_seconds=timeout, model=model)

    # --- Ollama local -------------------------------------------------------
    if p == "ollama":
        from .ollama_client import OllamaClient
        section = _provider_section(cfg, "ollama")
        host = getattr(section, "host", "http://localhost:11434") if section else "http://localhost:11434"
        timeout = int(getattr(section, "timeout_seconds", 30) if section else 30)
        api_key = getattr(section, "api_key", "") if section else ""
        return OllamaClient(host=host, timeout_seconds=timeout, api_key=api_key)

    # --- OpenAI-compatible providers ---------------------------------------
    # All these share the same client class — only host + chat_path + headers
    # differ. We use a small static table to keep the body terse.
    from .openai_client import OpenAIClient

    OPENAI_COMPAT_CONFIG: dict[str, dict[str, Any]] = {
        "openai": {
            "host": "https://api.openai.com",
            "chat_path": "/v1/chat/completions",
            "section": "openai",
        },
        "groq": {
            "host": "https://api.groq.com/openai/v1",
            "chat_path": "/chat/completions",
            "section": "groq",
        },
        "mistral": {
            "host": "https://api.mistral.ai/v1",
            "chat_path": "/chat/completions",
            "section": "mistral",
        },
        "xai": {
            "host": "https://api.x.ai/v1",
            "chat_path": "/chat/completions",
            "section": "xai",
        },
        "together": {
            "host": "https://api.together.xyz/v1",
            "chat_path": "/chat/completions",
            "section": "together",
        },
        "deepseek": {
            "host": "https://api.deepseek.com/v1",
            "chat_path": "/chat/completions",
            "section": "deepseek",
        },
        "openrouter": {
            "host": "https://openrouter.ai/api/v1",
            "chat_path": "/chat/completions",
            "section": "openrouter",
        },
        "opencode": {
            "host": "https://opencode.ai/zen",
            "chat_path": "/v1/chat/completions",
            "section": "opencode",
        },
        "gemini": {
            "host": "https://generativelanguage.googleapis.com/v1beta/openai",
            "chat_path": "/chat/completions",
            "section": "gemini",
        },
        "github": {
            "host": "https://models.inference.ai.azure.com",
            "chat_path": "/chat/completions",
            "section": "github",
        },
    }

    spec = OPENAI_COMPAT_CONFIG.get(p)
    if spec is None:
        raise ValueError(
            f"auxiliary_client: provider {provider!r} not supported. "
            f"Known: anthropic, ollama, {', '.join(sorted(OPENAI_COMPAT_CONFIG))}"
        )

    section = _provider_section(cfg, spec["section"])
    # Pull host/api_key from the provider section when set (some providers
    # let users override via config — e.g. self-hosted OpenAI-compat
    # endpoints). Fall back to the spec defaults otherwise.
    host = getattr(section, "host", spec["host"]) if section else spec["host"]
    api_key = getattr(section, "api_key", "") if section else ""
    timeout = int(getattr(section, "timeout_seconds", 60) if section else 60)

    # `opencode` zen has a default public key — see existing cli.py special-casing.
    if p == "opencode" and not api_key:
        api_key = os.environ.get("OPENCODE_API_KEY", "public")

    extra_headers: dict[str, str] | None = None
    if p == "opencode":
        extra_headers = {"User-Agent": "opencode/1.15.11"}
    elif p == "github":
        # GitHub Models accepts the same Bearer flow but needs api-version.
        extra_headers = {"X-GitHub-Api-Version": "2023-07-07"}

    return OpenAIClient(
        host=host,
        timeout_seconds=timeout,
        api_key=api_key,
        model=model,
        extra_headers=extra_headers,
        chat_path=spec["chat_path"],
    )


# ---------------------------------------------------------------------------
# Convenience: one-shot text generation
# ---------------------------------------------------------------------------


def get_compression_client(cfg=None) -> tuple["TextLLMClient | None", "str | None"]:
    """Return (client, model_name) for context compression.

    Shorthand for ``get_text_auxiliary_client("compression_model", cfg)``.
    Used by :mod:`bauer.context_manager` for semantic summarisation when the
    primary model is not passed explicitly.

    Returns ``(None, None)`` when the slot is unconfigured — the caller
    should fall back to rule-based summarisation.
    """
    return get_text_auxiliary_client("compression_model", cfg)


def call_aux_text(
    slot: str,
    messages: list[dict],
    *,
    cfg=None,
    fallback: str = "",
) -> str:
    """Convenience wrapper — calls the slot and joins streaming chunks.

    Returns `fallback` (default "") when the auxiliary client is unavailable
    or the call raises. Use this when the caller wants a "best-effort" string
    with a known fallback path rather than dealing with the (client, model)
    pair manually.
    """
    client, model = get_text_auxiliary_client(slot, cfg)
    if client is None or not model:
        return fallback
    try:
        return "".join(client.chat_stream(model, messages))
    except Exception as exc:
        logger.info("auxiliary_client.call_aux_text(%r): %s", slot, exc)
        return fallback
