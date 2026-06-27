"""Memory tools: key-value persistente em .bauer_memory.json."""

from __future__ import annotations

import json
from pathlib import Path

from .base import ToolError


class MemoryToolsMixin:

    _MEMORY_FILE = ".bauer_memory.json"
    _MAX_VALUE_LEN = 10_000  # chars por valor
    _MAX_KEYS = 500

    def _memory_path(self) -> Path:
        return self.workspace / self._MEMORY_FILE

    def _memory_load(self) -> dict:
        p = self._memory_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _memory_save(self, data: dict) -> None:
        self._memory_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _memory(self, args: dict) -> str:
        """Key-value persistente em .bauer_memory.json dentro do workspace."""
        from datetime import datetime, timezone as _tz

        action = str(args.get("action", "")).lower()
        if not action:
            raise ToolError("memory requer 'action': set | get | list | delete.")

        if action == "set":
            key = args.get("key", "").strip()
            value = args.get("value")
            if not key:
                raise ToolError("memory set requer 'key'.")
            if value is None:
                raise ToolError("memory set requer 'value'.")
            value_str = str(value)
            if len(value_str) > self._MAX_VALUE_LEN:
                raise ToolError(
                    f"Valor muito grande ({len(value_str)} chars). "
                    f"Limite: {self._MAX_VALUE_LEN} chars."
                )
            data = self._memory_load()
            if len(data) >= self._MAX_KEYS and key not in data:
                raise ToolError(
                    f"Limite de {self._MAX_KEYS} chaves atingido. "
                    "Use memory delete para liberar espaco."
                )
            ts = datetime.now(_tz.utc).isoformat()
            data[key] = {"value": value_str, "updated_at": ts}
            self._memory_save(data)
            return f"Memory['{key}'] = {value_str[:80]}{'...' if len(value_str) > 80 else ''}"

        elif action == "get":
            key = args.get("key", "").strip()
            if not key:
                raise ToolError("memory get requer 'key'.")
            data = self._memory_load()
            if key not in data:
                return f"Chave '{key}' nao encontrada na memory."
            entry = data[key]
            val = entry["value"] if isinstance(entry, dict) else str(entry)
            ts = entry.get("updated_at", "") if isinstance(entry, dict) else ""
            return f"Memory['{key}'] = {val}\n(atualizado: {ts})"

        elif action == "list":
            data = self._memory_load()
            if not data:
                return "Memory vazia."
            lines = [f"Memory ({len(data)} chaves):"]
            for k, v in sorted(data.items()):
                val = v["value"] if isinstance(v, dict) else str(v)
                preview = val[:60].replace("\n", " ") + ("..." if len(val) > 60 else "")
                lines.append(f"  {k}: {preview}")
            return "\n".join(lines)

        elif action == "delete":
            key = args.get("key", "").strip()
            if not key:
                raise ToolError("memory delete requer 'key'.")
            data = self._memory_load()
            if key not in data:
                return f"Chave '{key}' nao encontrada — nada removido."
            del data[key]
            self._memory_save(data)
            return f"Memory['{key}'] removido."

        else:
            raise ToolError(f"Acao desconhecida: '{action}'. Use: set | get | list | delete.")
