"""Explicit schemas for externally executed, read-only Gateway tools."""
from __future__ import annotations


def build_external_read_tools():
    from agno.tools.function import Function

    def read_file(path: str, offset: int = 0, limit: int = 1000) -> str:
        """Read part of a file in the selected Bauer project."""
        raise AssertionError("external tool cannot execute inside AgentOS")

    tool = Function.from_callable(read_file)
    tool.external_execution = True
    return [tool]
