"""Transactional, owner-scoped bookkeeping for runs submitted to AgentOS.

This store contains identifiers and state only; it never stores prompts, tokens
or tool arguments. A SUBMITTING run with no remote id is deliberately kept
recoverable: the caller must reconcile it, not send the request again.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class RemoteRunConflict(ValueError):
    """A duplicate update differs from its original run, or a session is busy."""


class RemoteRunNotFound(LookupError):
    """The run does not exist for this owner (also used for access denial)."""


@dataclass(frozen=True)
class RemoteRun:
    gateway_run_id: str
    channel: str
    update_id: str
    generation: int
    owner_id: str
    session_id: str
    entity_type: str
    entity_id: str
    remote_run_id: str | None
    state: str
    created_at: float
    updated_at: float


_TRANSITIONS = {
    "PREPARED": {"SUBMITTING", "CANCELLED"},
    "SUBMITTING": {"RUNNING", "PAUSED", "COMPLETED", "CANCELLED", "ERROR", "UNKNOWN"},
    "UNKNOWN": {"RUNNING", "PAUSED", "COMPLETED", "CANCELLED", "ERROR"},
    "RUNNING": {"PAUSED", "COMPLETED", "CANCELLED", "ERROR", "UNKNOWN"},
    "PAUSED": {"RUNNING", "COMPLETED", "CANCELLED", "ERROR", "UNKNOWN"},
    "COMPLETED": set(),
    "CANCELLED": set(),
    "ERROR": set(),
}
class RemoteRunStore:
    """SQLite-backed idempotency and per-session exclusion across processes."""

    def __init__(self, path: str | Path, *, require_managed_session: bool = False) -> None:
        if not isinstance(path, (str, Path)) or not str(path):
            raise ValueError("path must be a non-empty filesystem path")
        self.path = Path(path)
        self.require_managed_session = require_managed_session
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS remote_session_owners (
                    channel TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    PRIMARY KEY(channel, session_id)
                );
                CREATE TABLE IF NOT EXISTS remote_runs (
                    gateway_run_id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    update_id TEXT NOT NULL,
                    generation INTEGER NOT NULL CHECK(generation >= 0),
                    owner_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL CHECK(entity_type IN ('agent', 'team')),
                    entity_id TEXT NOT NULL,
                    remote_run_id TEXT,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(channel, update_id, generation)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS remote_runs_active_session
                    ON remote_runs(channel, session_id)
                    WHERE state NOT IN ('COMPLETED', 'CANCELLED', 'ERROR');
                CREATE UNIQUE INDEX IF NOT EXISTS remote_runs_remote_identity
                    ON remote_runs(entity_type, entity_id, remote_run_id)
                    WHERE remote_run_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS remote_runs_owner_state
                    ON remote_runs(owner_id, state);
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
    def _record(row: sqlite3.Row) -> RemoteRun:
        return RemoteRun(**dict(row))

    def claim(
        self, *, gateway_run_id: str, channel: str, update_id: str,
        generation: int, owner_id: str, session_id: str,
        entity_type: str, entity_id: str,
    ) -> tuple[RemoteRun, bool]:
        """Claim an update before submission; return (record, newly_created).

        A replay may return the existing record only for identical identity,
        session and target. It does not grant permission to submit again.
        """
        values = (gateway_run_id, channel, update_id, owner_id, session_id, entity_id)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("run identifiers must be non-empty strings")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("generation must be a non-negative integer")
        if entity_type not in {"agent", "team"}:
            raise ValueError("entity_type must be agent or team")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM remote_runs WHERE channel=? AND update_id=? AND generation=?",
                (channel, update_id, generation),
            ).fetchone()
            if row:
                record = self._record(row)
                if (record.owner_id, record.session_id, record.entity_type, record.entity_id) != (
                    owner_id, session_id, entity_type, entity_id
                ):
                    raise RemoteRunConflict("update already claimed with different context")
                return record, False
            managed_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_session_generations'"
            ).fetchone()
            managed_session = None
            if managed_table:
                managed_session = conn.execute(
                    "SELECT * FROM remote_session_generations WHERE session_id=?", (session_id,),
                ).fetchone()
            if self.require_managed_session and managed_session is None:
                raise RemoteRunConflict("session is not managed")
            if managed_session and (
                not managed_session["active"] or managed_session["channel"] != channel
                or managed_session["owner_id"] != owner_id or managed_session["generation"] != generation
            ):
                raise RemoteRunConflict("session is retired or does not match run identity")
            session_owner = conn.execute(
                "SELECT owner_id FROM remote_session_owners WHERE channel=? AND session_id=?",
                (channel, session_id),
            ).fetchone()
            if session_owner and session_owner["owner_id"] != owner_id:
                raise RemoteRunConflict("session belongs to another owner")
            conn.execute(
                "INSERT OR IGNORE INTO remote_session_owners VALUES (?, ?, ?)",
                (channel, session_id, owner_id),
            )
            now = time.time()
            try:
                conn.execute(
                    "INSERT INTO remote_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'PREPARED', ?, ?)",
                    (gateway_run_id, channel, update_id, generation, owner_id, session_id,
                     entity_type, entity_id, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise RemoteRunConflict("run id already used or session has an active run") from exc
            row = conn.execute("SELECT * FROM remote_runs WHERE gateway_run_id=?", (gateway_run_id,)).fetchone()
            return self._record(row), True

    def get(self, gateway_run_id: str, *, owner_id: str) -> RemoteRun:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM remote_runs WHERE gateway_run_id=? AND owner_id=?",
                (gateway_run_id, owner_id),
            ).fetchone()
        if row is None:
            raise RemoteRunNotFound("run not found")
        return self._record(row)

    def get_by_update(self, *, channel: str, update_id: str, owner_id: str) -> RemoteRun | None:
        """Look up an owned run without creating or resubmitting it."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM remote_runs WHERE channel=? AND update_id=? AND owner_id=?",
                (channel, update_id, owner_id),
            ).fetchall()
        if len(rows) > 1:
            raise RemoteRunConflict("update has multiple runs")
        return self._record(rows[0]) if rows else None

    def transition(
        self, gateway_run_id: str, *, owner_id: str, state: str,
        remote_run_id: str | None = None,
    ) -> RemoteRun:
        """Move state atomically; never change a known remote identity.

        The caller may mark ERROR only when the remote terminal error is
        established or submission is definitively rejected. A timeout or
        ambiguous response must be recorded as UNKNOWN instead.
        """
        if state not in _TRANSITIONS:
            raise ValueError("invalid state")
        if remote_run_id is not None and (not isinstance(remote_run_id, str) or not remote_run_id.strip()):
            raise ValueError("remote_run_id must be non-empty")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM remote_runs WHERE gateway_run_id=? AND owner_id=?",
                (gateway_run_id, owner_id),
            ).fetchone()
            if row is None:
                raise RemoteRunNotFound("run not found")
            record = self._record(row)
            if record.remote_run_id and remote_run_id and record.remote_run_id != remote_run_id:
                raise RemoteRunConflict("remote identity cannot change")
            if state not in _TRANSITIONS[record.state]:
                raise RemoteRunConflict("invalid state transition")
            actual_remote_id = record.remote_run_id or remote_run_id
            if state in {"RUNNING", "PAUSED", "COMPLETED"} and not actual_remote_id:
                raise RemoteRunConflict("remote identity required for this state")
            if state == "CANCELLED" and record.state != "PREPARED" and not actual_remote_id:
                raise RemoteRunConflict("cannot confirm cancellation without remote identity")
            try:
                conn.execute(
                    "UPDATE remote_runs SET state=?, remote_run_id=?, updated_at=? WHERE gateway_run_id=?",
                    (state, actual_remote_id, time.time(), gateway_run_id),
                )
            except sqlite3.IntegrityError as exc:
                raise RemoteRunConflict("remote identity already in use") from exc
            row = conn.execute("SELECT * FROM remote_runs WHERE gateway_run_id=?", (gateway_run_id,)).fetchone()
            return self._record(row)

    def recoverable(self, *, owner_id: str) -> list[RemoteRun]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM remote_runs WHERE owner_id=? AND state NOT IN "
                "('COMPLETED', 'CANCELLED', 'ERROR') ORDER BY created_at", (owner_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def purge_terminal(self, *, older_than: float) -> int:
        """Delete only terminal records past the caller's retention threshold."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM remote_runs WHERE state IN ('COMPLETED', 'CANCELLED', 'ERROR') AND updated_at < ?",
                (older_than,),
            )
            return cursor.rowcount
