"""Persisted, explicit project bindings for private gateway conversations.

The channel authenticates the operator before calling this service. Registered
projects are the operator's access boundary; arbitrary remote paths are never
accepted. A binding pins the resolved path so registry changes fail closed.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


class ChannelProjectError(ValueError):
    """A selected project cannot safely receive a new operation."""


@dataclass(frozen=True)
class ChannelProjectContext:
    project_id: str
    name: str
    workspace: Path
    session_key: str
    router: Any = None
    system_prompt: str = ""


class ChannelProjectStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS bindings (operator TEXT PRIMARY KEY, "
                           "project_id TEXT NOT NULL, workspace TEXT NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS models (session TEXT PRIMARY KEY, "
                           "provider TEXT NOT NULL, model TEXT NOT NULL)")
                yield db
        finally:
            db.close()

    def binding(self, operator: str) -> tuple[str, str] | None:
        if not self.path.exists():
            return None
        with self._connect() as db:
            row = db.execute("SELECT project_id, workspace FROM bindings WHERE operator=?",
                             (operator,)).fetchone()
        return (str(row[0]), str(row[1])) if row else None

    def bind(self, operator: str, project_id: str, workspace: Path) -> None:
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO bindings VALUES (?, ?, ?)",
                       (operator, project_id, str(workspace)))

    def model(self, session: str) -> tuple[str, str] | None:
        if not self.path.exists():
            return None
        with self._connect() as db:
            row = db.execute("SELECT provider, model FROM models WHERE session=?",
                             (session,)).fetchone()
        return (str(row[0]), str(row[1])) if row else None

    def save_model(self, session: str, provider: str, model: str) -> None:
        with self._connect() as db:
            if model:
                db.execute("INSERT OR REPLACE INTO models VALUES (?, ?, ?)",
                           (session, provider, model))
            else:
                db.execute("DELETE FROM models WHERE session=?", (session,))


def private_telegram(msg: Any) -> bool:
    if msg.channel != "telegram":
        return False
    chat = (msg.raw.get("message") or {}).get("chat") or {}
    return chat.get("type") == "private"


def operator_key(msg: Any) -> str:
    return f"{msg.channel}:{msg.chat_id}:{msg.user_id}"


def context_key(msg: Any, project_id: str) -> str:
    # Only bounded, filesystem-safe identifiers reach session stores/log names.
    owner = hashlib.sha256(operator_key(msg).encode()).hexdigest()[:24]
    return f"tg-project-{owner}-{project_id}"
