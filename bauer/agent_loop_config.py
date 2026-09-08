"""Parsing e resolução de configuração para o modo autônomo ``/loop``."""

from __future__ import annotations

import re
from typing import Any

from .config_loader import LoopSection


def parse_loop_args(rest: str) -> tuple[str, dict[str, str]]:
    """Extrai flags de ``/loop`` sem alterar o texto livre da tarefa.

    Não usa ``shlex``: em modo POSIX ele consome barras invertidas de paths
    Windows, que devem chegar intactas ao modelo e às tools.
    """
    flag_to_key = {
        "max-minutes": "max_minutes",
        "max-tool-calls": "max_tool_calls",
        "max-cost": "max_cost_usd",
        "approval": "approval_mode",
    }
    overrides: dict[str, str] = {}

    def grab(match: re.Match[str]) -> str:
        overrides[flag_to_key[match.group(1)]] = match.group(2)
        return ""

    task = re.sub(
        r"(?:^|(?<=\s))--(max-minutes|max-tool-calls|max-cost|approval)\s+(\S+)\s*",
        grab,
        rest,
    )

    def grab_yolo(_match: re.Match[str]) -> str:
        overrides["approval_mode"] = "yolo"
        return ""

    task = re.sub(r"(?:^|(?<=\s))--yolo(?:\s+|$)", grab_yolo, task)
    return task.strip(), overrides


def resolve_loop_config(overrides: dict[str, Any]) -> LoopSection:
    """Resolve limites: flags > config > defaults seguros.

    Falha ao carregar a configuração usa os defaults de ``LoopSection``;
    valores explícitos inválidos retornam um ``ValueError`` orientado ao CLI.
    """
    try:
        from .config_loader import load_config

        base = load_config().loop
    except Exception:  # noqa: BLE001 - fallback de configuração do CLI
        base = LoopSection()

    data = base.model_dump()
    if "max_minutes" in overrides:
        try:
            data["max_minutes"] = int(overrides["max_minutes"])
        except ValueError:
            raise ValueError(f"--max-minutes inválido: {overrides['max_minutes']!r}") from None
    if "max_tool_calls" in overrides:
        try:
            data["max_tool_calls"] = int(overrides["max_tool_calls"])
        except ValueError:
            raise ValueError(
                f"--max-tool-calls inválido: {overrides['max_tool_calls']!r}"
            ) from None
    if "max_cost_usd" in overrides:
        try:
            data["max_cost_usd"] = float(overrides["max_cost_usd"])
        except ValueError:
            raise ValueError(f"--max-cost inválido: {overrides['max_cost_usd']!r}") from None
    if "approval_mode" in overrides:
        data["approval_mode"] = overrides["approval_mode"]
    if "approval_risk_threshold" in overrides:
        try:
            data["approval_risk_threshold"] = float(overrides["approval_risk_threshold"])
        except ValueError:
            raise ValueError(
                "approval_risk_threshold inválido: "
                f"{overrides['approval_risk_threshold']!r}"
            ) from None
    return LoopSection(**data)


def resolve_max_tool_turns() -> int:
    """Lê ``tools.max_tool_turns`` sem deixar falha de config quebrar a sessão."""
    try:
        from .config_loader import load_config

        return load_config().tools.max_tool_turns
    except Exception:  # noqa: BLE001 - mesmo default de ToolsSection
        return 150
