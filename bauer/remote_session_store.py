"""Persist opaque remote session IDs and conversation generations in SQLite.

Use the same database as RemoteRunStore. A rotation and a run claim then
serialize against one another; retired session IDs cannot start new runs.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class RemoteSessionConflict(ValueError):
    """A session cannot rotate while a run might still be active."""


@dataclass(frozen=True)
class RemoteSession:
    channel: str
    owner_id: str
    chat_id: str
    project_id: str
    generation: int
    session_id: str


class RemoteSessionStore:
    def __init__(self, path: str | Path) -> None:
        if not isinstance(path, (str, Path)) or not str(path):
            raise ValueError("path must be a non-empty filesystem path")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS remote_session_generations (
                    channel TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    generation INTEGER NOT NULL CHECK(generation >= 0),
                    session_id TEXT NOT NULL UNIQUE,
                    active INTEGER NOT NULL CHECK(active IN (0, 1)),
                    PRIMARY KEY(channel, owner_id, chat_id, project_id, generation)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS remote_session_current
                    ON remote_session_generations(channel, owner_id, chat_id, project_id)
                    WHERE active = 1;
                CREATE TABLE IF NOT EXISTS remote_session_rotations (
                    channel TEXT NOT NULL,
                    update_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    PRIMARY KEY(channel, update_id),
                    FOREIGN KEY(session_id) REFERENCES remote_session_generations(session_id)
                );
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _validate(channel: str, owner_id: str, chat_id: str, project_id: str) -> None:
        if any(not isinstance(v, str) or not v.strip() for v in (channel, owner_id, chat_id)):
            raise ValueError("channel, owner_id and chat_id must be non-empty strings")
        if not isinstance(project_id, str):
            raise ValueError("project_id must be a string")

    @staticmethod
    def _record(row: sqlite3.Row) -> RemoteSession:
        return RemoteSession(**{key: row[key] for key in (
            "channel", "owner_id", "chat_id", "project_id", "generation", "session_id"
        )})

    @staticmethod
    def _insert(conn: sqlite3.Connection, channel: str, owner_id: str, chat_id: str,
                project_id: str, generation: int) -> RemoteSession:
        session_id = uuid.uuid4().hex
        conn.execute("INSERT INTO remote_session_generations VALUES (?, ?, ?, ?, ?, ?, 1)",
                     (channel, owner_id, chat_id, project_id, generation, session_id))
        return RemoteSession(channel, owner_id, chat_id, project_id, generation, session_id)

    def current(self, *, channel: str, owner_id: str, chat_id: str,
                project_id: str = "") -> RemoteSession:
        self._validate(channel, owner_id, chat_id, project_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM remote_session_generations WHERE channel=? AND owner_id=? "
                "AND chat_id=? AND project_id=? AND active=1",
                (channel, owner_id, chat_id, project_id),
            ).fetchone()
            return self._record(row) if row else self._insert(conn, channel, owner_id, chat_id, project_id, 0)

    def rotate(self, *, channel: str, owner_id: str, chat_id: str,
               project_id: str = "", update_id: str | None = None) -> RemoteSession:
        """Rotate once per channel update; no rotation while a run is active.

        A channel must pass its stable update_id to make retries idempotent.
        The optional argument preserves the previous API for other callers.
        """
        self._validate(channel, owner_id, chat_id, project_id)
        if update_id is not None and (not isinstance(update_id, str) or not update_id.strip()):
            raise ValueError("update_id must be a non-empty string")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if update_id is not None:
                previous = conn.execute(
                    "SELECT r.owner_id, r.chat_id, r.project_id, g.* "
                    "FROM remote_session_rotations r JOIN remote_session_generations g "
                    "ON g.session_id=r.session_id WHERE r.channel=? AND r.update_id=?",
                    (channel, update_id),
                ).fetchone()
                if previous:
                    if (previous["owner_id"], previous["chat_id"], previous["project_id"]) != (
                        owner_id, chat_id, project_id
                    ):
                        raise RemoteSessionConflict("update already claimed with different context")
                    return self._record(previous)
            row = conn.execute(
                "SELECT * FROM remote_session_generations WHERE channel=? AND owner_id=? "
                "AND chat_id=? AND project_id=? AND active=1",
                (channel, owner_id, chat_id, project_id),
            ).fetchone()
            if row is None:
                result = self._insert(conn, channel, owner_id, chat_id, project_id, 0)
            else:
                runs_table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_runs'"
                ).fetchone()
                if runs_table and conn.execute(
                    "SELECT 1 FROM remote_runs WHERE channel=? AND session_id=? "
                    "AND state NOT IN ('COMPLETED', 'CANCELLED', 'ERROR') LIMIT 1",
                    (channel, row["session_id"]),
                ).fetchone():
                    raise RemoteSessionConflict("session has a run that is not terminal")
                conn.execute("UPDATE remote_session_generations SET active=0 WHERE session_id=?", (row["session_id"],))
                result = self._insert(conn, channel, owner_id, chat_id, project_id, row["generation"] + 1)
            if update_id is not None:
                conn.execute("INSERT INTO remote_session_rotations VALUES (?, ?, ?, ?, ?, ?)",
                             (channel, update_id, owner_id, chat_id, project_id, result.session_id))
            return result
