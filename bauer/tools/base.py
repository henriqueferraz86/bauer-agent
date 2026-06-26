"""Tipos e exceções compartilhados pelos mixins de tools.

Mora aqui (e não em tool_router.py) para evitar import circular: os mixins
em bauer/tools/*.py importam destas classes, e tool_router.py as re-exporta
para preservar `from bauer.tool_router import ToolError, SandboxError, DryRunResult`.
"""

from __future__ import annotations


class ToolError(Exception):
    """Erro de execução de tool com mensagem legível."""


class SandboxError(ToolError):
    """Tentativa de acesso fora do workspace."""


class DryRunResult:
    """Retornado quando dry_run=True: descreve o que teria acontecido sem executar."""
    def __init__(self, tool: str, summary: str):
        self.tool = tool
        self.summary = summary

    def __str__(self) -> str:
        return f"[dry_run] {self.tool}: {self.summary}"
