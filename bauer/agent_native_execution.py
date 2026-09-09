"""Primitivas compartilhadas pelos fluxos de function calling nativo."""

from __future__ import annotations

import json


def parse_native_arguments(arguments: str) -> dict:
    """Decodifica argumentos de uma tool call; payload inválido vira objeto vazio."""
    try:
        value = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def native_tool_message(client, model_name: str, payload: list[dict], schemas: list, *, on_delta=None) -> dict:
    """Chama o provider, incluindo streaming somente quando o caller o pediu."""
    if on_delta is None:
        return client.chat_with_tools(model_name, payload, tools=schemas)
    return client.chat_with_tools(model_name, payload, tools=schemas, on_delta=on_delta)


def report_native_cost(client, model_name: str) -> None:
    """Reporta uso do provider sem permitir que telemetria interrompa o turno."""
    try:
        from .cost_meter import provider_from_client, report_llm_cost
        report_llm_cost(provider_from_client(client), model_name, getattr(client, "last_usage", None))
    except Exception:  # noqa: BLE001 -- medição é acessória
        pass
