"""Resolução de identidade legível de providers para a interface."""

from __future__ import annotations

HOST_MARKERS: tuple[tuple[str, str], ...] = (
    (":11434", "ollama"), ("ollama", "ollama"), ("openrouter", "openrouter"),
    ("anthropic", "anthropic"), ("deepseek", "deepseek"), ("groq", "groq"),
    ("mistral", "mistral"), ("together", "together"), ("cerebras", "cerebras"),
    ("sambanova", "sambanova"), ("perplexity", "perplexity"), ("fireworks", "fireworks"),
    ("moonshot", "moonshot"), ("dashscope", "alibaba"), ("opencode", "opencode"),
    ("x.ai", "xai"), ("googleapis", "gemini"), ("azure", "azure"), ("openai", "openai"),
)


def detect_provider_from_host(host: str) -> str:
    value = (host or "").lower()
    return next((name for marker, name in HOST_MARKERS if marker in value), "openai")


def declared_provider() -> str:
    try:
        from .config_loader import load_config
        return str(load_config().model.provider or "")
    except Exception as exc:  # noqa: BLE001 -- painel degrada sem config
        from .logging_config import log_suppressed
        log_suppressed("provider.declarado", exc)
        return ""
