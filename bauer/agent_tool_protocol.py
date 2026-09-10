"""Parsing e normalização do protocolo textual de ferramentas (bridge)."""

from __future__ import annotations

import json
from collections.abc import Callable


def extract_text_from_pseudo_json(response: str) -> str | None:
    try:
        obj = json.loads(response.strip())
        args = obj.get("args", {}) if isinstance(obj, dict) else {}
        for key in ("conteudo", "content", "text", "resposta", "message", "mensagem", "response"):
            if isinstance(args, dict) and isinstance(args.get(key), str):
                return args[key]
    except Exception:
        return None
    return None


def normalize_tool_object(obj: object, available: set[str]) -> dict | None:
    """Aceita o contrato bridge e atalhos seguros de tools comuns.

    Alguns modelos no modo bridge preservam apenas os argumentos da chamada
    (por exemplo, ``{"query": "..."}``) e omitem o nome da ferramenta. Esses
    atalhos só são normalizados quando o conjunto de chaves identifica uma
    ferramenta sem ambiguidade.
    """
    if not isinstance(obj, dict):
        return None
    if obj.get("action") in available:
        return obj
    if (
        "action" not in obj and "command" in obj and "run_command" in available
        and isinstance(obj.get("command"), str) and obj["command"].strip()
        and set(obj).issubset({"command", "confirm", "background"})
    ):
        args = {"command": obj["command"]}
        for key in ("confirm", "background"):
            if key in obj:
                args[key] = obj[key]
        return {"action": "run_command", "args": args}
    if (
        "action" not in obj
        and "query" in obj
        and "web_search" in available
        and isinstance(obj.get("query"), str)
        and obj["query"].strip()
        and set(obj).issubset({"query", "max_results"})
    ):
        return {"action": "web_search", "args": dict(obj)}
    return None


def extract_embedded_json_action(text: str, available: set[str]) -> dict | None:
    """Encontra o primeiro objeto bridge válido, inclusive após texto narrativo."""
    decoder = json.JSONDecoder()
    index = text.find("{")
    while index != -1:
        try:
            obj, _ = decoder.raw_decode(text, index)
            normalized = normalize_tool_object(obj, available)
            if normalized is not None:
                return normalized
        except json.JSONDecodeError:
            pass
        index = text.find("{", index + 1)
    return None


def try_parse_tool(response: str, available: set[str], parse: Callable[[str], object]) -> dict | None:
    """Interpreta um único tool call bridge sem depender do ToolRouter concreto."""
    stripped = response.strip()
    try:
        normalized = normalize_tool_object(parse(stripped), available)
        if normalized is not None:
            return normalized
    except Exception:  # noqa: BLE001 -- parsers de provider podem levantar tipos distintos
        pass
    if stripped.startswith("{"):
        try:
            obj, _ = json.JSONDecoder().raw_decode(stripped)
            normalized = normalize_tool_object(obj, available)
            if normalized is not None:
                return normalized
        except Exception:  # noqa: BLE001 -- JSON parcial é resposta inválida, não falha de turno
            pass
    return extract_embedded_json_action(stripped, available)


def try_parse_tools_batch(response: str, available: set[str], parse: Callable[[str], object]) -> list[dict] | None:
    """Interpreta vários objetos JSON por linha, com fallback ao call único."""
    actions: list[dict] = []
    for line in response.strip().splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            normalized = normalize_tool_object(json.loads(line), available)
            if normalized is not None:
                actions.append(normalized)
        except json.JSONDecodeError:
            pass
    if actions:
        return actions
    single = try_parse_tool(response, available, parse)
    return [single] if single is not None else None
