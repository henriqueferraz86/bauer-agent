"""Capacidade e falhas de protocolo para function calling nativo."""

from __future__ import annotations

import re

NATIVE_UNSUPPORTED_CODES = frozenset({400, 404, 405, 422, 501})


class NativeToolsUnsupported(Exception):
    """O provider recusou o parâmetro ``tools=`` e deve cair para bridge."""


def client_supports_native_tools(client) -> bool:
    """Aceita apenas clients concretos para não ligar native em mocks."""
    from .ollama_client import OllamaClient
    from .openai_client import OpenAIClient

    return isinstance(client, OpenAIClient | OllamaClient) and bool(
        getattr(client, "supports_native_tools", False)
    )


def is_native_unsupported_error(exc: Exception) -> bool:
    match = re.search(r"HTTP (\d{3})", str(exc))
    return bool(match) and int(match.group(1)) in NATIVE_UNSUPPORTED_CODES
