"""Helpers compartilhados da camada de auditoria — parsing de tempo e de eventos.

Sem I/O de rede, sem LLM. Tudo best-effort: timestamp ilegível vira None."""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Tools que ALTERAM arquivos — usadas para derivar `files_changed` dos eventos.
FILE_MUTATING_TOOLS = frozenset({
    "write_file", "create_dir", "delete_file", "append_file", "move_file",
    "copy_file", "patch",
})
# Tools que executam comandos — derivam `commands_executed`.
COMMAND_TOOLS = frozenset({"run_command", "process"})


def parse_iso(ts: Any) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts))
    except (ValueError, TypeError):
        return None


def duration_ms(started_at: Any, finished_at: Any, fallback: Any = None) -> float | None:
    """Wall-clock em ms entre started_at e finished_at (ou fallback, ex.: updated_at)."""
    start = parse_iso(started_at)
    end = parse_iso(finished_at) or parse_iso(fallback)
    if start is None or end is None:
        return None
    ms = (end - start).total_seconds() * 1000.0
    return round(ms, 2) if ms >= 0 else None


def event_arg(event_data: dict[str, Any], key: str) -> str:
    """Lê data['args'][key] de um evento de tool, defensivamente."""
    try:
        args = event_data.get("args") or {}
        val = args.get(key)
        return str(val) if val is not None else ""
    except Exception:  # noqa: BLE001
        return ""
