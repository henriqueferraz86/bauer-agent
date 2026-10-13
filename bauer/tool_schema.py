"""Tradução do contrato descritivo de argumentos para function calling nativo."""

from __future__ import annotations

import re


_SCALAR_TYPES = {"str": "string", "int": "integer", "bool": "boolean", "dict": "object", "float": "number"}
_REQUIRED = re.compile(r"\bobrigat[oó]rio\b", re.IGNORECASE)
_CONDITIONAL = re.compile(r"\b(?:para|exceto)\b|\b(?:action|mode)\s*=", re.IGNORECASE)


def _argument_schema(description: str) -> dict:
    type_hint = description.split("—", 1)[0].strip()
    array = re.fullmatch(r"list(?:\[(\w+)\])?", type_hint)
    if array is not None:
        item_type = _SCALAR_TYPES.get(array.group(1))
        return {"type": "array", "items": {"type": item_type} if item_type else {}, "description": description}
    return {"type": _SCALAR_TYPES.get(type_hint, "string"), "description": description}


def parameters_from_descriptions(args_info: dict) -> dict:
    """Mantém condições na descrição, sem impô-las a todas as ações da tool.

    Apenas anotações obrigatórias incondicionais entram em ``required``.
    Listas sem tipo de item (como parâmetros SQLite) aceitam valores mistos.
    O handler continua responsável pelas condições específicas da ação.
    """
    properties: dict[str, dict] = {}
    required: list[str] = []
    for name, description in args_info.items():
        description = str(description)
        properties[name] = _argument_schema(description)
        annotations = re.findall(r"\(([^()]*)\)", description)
        if any(_REQUIRED.search(annotation) and not _CONDITIONAL.search(annotation) for annotation in annotations):
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}
