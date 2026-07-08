"""Audit log de tool calls.

Registra cada execução de tool em logs/audit.jsonl com:
  - timestamp ISO 8601
  - session_id
  - action (nome da tool)
  - args (sanitizados de secrets)
  - status (ok | error)
  - error_msg (apenas em erro)
  - duration_ms

Thread-safe: usa lock por arquivo para escrita concorrente segura.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_locks: dict[str, threading.Lock] = {}
_locks_mutex = threading.Lock()

# Chaves que nunca aparecem no audit log (mesmo parcialmente)
_REDACT_KEYS = frozenset({
    "api_key", "apikey", "token", "secret", "password", "passwd",
    "authorization", "auth", "credential", "key", "private_key",
})

# Máximo de chars por valor de arg no audit log
_MAX_ARG_VALUE_LEN = 200


def _get_lock(path: str) -> threading.Lock:
    with _locks_mutex:
        if path not in _locks:
            _locks[path] = threading.Lock()
        return _locks[path]


def _sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Remove valores sensíveis dos args antes de logar."""
    if not isinstance(args, dict):
        return {}
    result = {}
    for k, v in args.items():
        k_lower = k.lower()
        if any(rk in k_lower for rk in _REDACT_KEYS):
            result[k] = "[REDACTED]"
        elif isinstance(v, str) and len(v) > _MAX_ARG_VALUE_LEN:
            result[k] = v[:_MAX_ARG_VALUE_LEN] + f"…(+{len(v) - _MAX_ARG_VALUE_LEN} chars)"
        else:
            result[k] = v
    return result


class AuditLogger:
    """Escreve entradas de audit log em JSONL thread-safe."""

    def __init__(self, log_dir: Path, session_id: str = "") -> None:
        self.log_dir = log_dir
        self.session_id = session_id or "unknown"
        self._audit_file = log_dir / "audit.jsonl"

    def _ensure_dir(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log_tool_call(
        self,
        action: str,
        args: dict[str, Any],
        *,
        status: str,           # "ok" | "error"
        duration_ms: float,
        error_msg: Optional[str] = None,
        result_preview: Optional[str] = None,
    ) -> None:
        """Registra uma execução de tool no audit log."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": self.session_id,
            "action": action,
            "args": _sanitize_args(args),
            "status": status,
            "duration_ms": round(duration_ms, 1),
        }
        if error_msg:
            entry["error"] = error_msg[:300]
        if result_preview and status == "ok":
            entry["preview"] = result_preview[:200]

        try:
            self._ensure_dir()
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            lock = _get_lock(str(self._audit_file))
            with lock:
                with open(self._audit_file, "a", encoding="utf-8") as f:
                    f.write(line)
        except Exception:
            pass  # audit log nunca bloqueia execução


# ---------------------------------------------------------------------------
# Context manager conveniente
# ---------------------------------------------------------------------------

class audit_tool_call:
    """Context manager que mede duração e chama AuditLogger.log_tool_call."""

    def __init__(
        self,
        logger: Optional[AuditLogger],
        action: str,
        args: dict[str, Any],
    ) -> None:
        self._logger = logger
        self._action = action
        self._args = args
        self._start = 0.0
        self._result: Optional[str] = None

    def set_result_preview(self, result: str) -> None:
        self._result = result

    def __enter__(self) -> "audit_tool_call":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._logger is None:
            return
        duration_ms = (time.monotonic() - self._start) * 1000
        if exc_type is None:
            self._logger.log_tool_call(
                self._action,
                self._args,
                status="ok",
                duration_ms=duration_ms,
                result_preview=self._result,
            )
        else:
            self._logger.log_tool_call(
                self._action,
                self._args,
                status="error",
                duration_ms=duration_ms,
                error_msg=str(exc_val)[:300] if exc_val else None,
            )
