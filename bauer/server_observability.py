"""Primitivas de observabilidade seguras para o servidor HTTP."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from uuid import uuid4

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_RUNTIME_DATABASES = ("runtime_state.sqlite3", "budget_ledger.sqlite3")


def request_id(incoming: str | None) -> str:
    """Aceita somente IDs de correlação de formato limitado."""
    candidate = (incoming or "").strip()
    return candidate if _REQUEST_ID.fullmatch(candidate) else uuid4().hex


def apply_security_headers(response) -> None:
    """Headers seguros que não dependem de saber onde TLS foi terminado."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")


def runtime_ready(root: Path) -> tuple[bool, str | None]:
    """Checa stores locais sem chamar providers ou revelar dados persistidos."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".readyz-probe"
        probe.touch(exist_ok=True)
        probe.unlink(missing_ok=True)
        for name in _RUNTIME_DATABASES:
            path = root / name
            if not path.exists():
                continue
            with sqlite3.connect(str(path), timeout=2.0) as conn:
                result = conn.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                return False, "runtime database integrity check failed"
    except (OSError, sqlite3.DatabaseError):
        return False, "runtime storage unavailable"
    return True, None
