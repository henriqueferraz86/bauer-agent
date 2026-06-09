"""Checkpoint & recovery — durable daemon state snapshots.

The checkpoint subsystem lets the daemon save its current state to disk
periodically and restore it on the next start.  This allows the daemon to
resume in-progress goals after a crash or restart without losing context.

What is checkpointed
--------------------
* Active goal IDs and their step progress
* Budget counters (cost, LLM calls, tool calls)
* Shutdown reason from last run (for anomaly detection)
* Custom payload dict (for caller-specific state)

Recovery
--------
:class:`RecoveryManager` reads the latest checkpoint on startup and
returns a :class:`RecoveryResult` describing what was interrupted.

Usage::

    from bauer.checkpoint import CheckpointManager, RecoveryManager

    mgr = CheckpointManager(db_path=":memory:", session_id="sess_1")
    mgr.save(
        goals=["goal_abc", "goal_def"],
        budget={"cost_usd": 0.23, "llm_calls": 8},
        payload={"custom_key": "value"},
    )

    recovery = RecoveryManager(db_path=":memory:")
    result = recovery.latest()
    if result.interrupted:
        print(f"Resuming from {result.session_id}")
        for gid in result.active_goals:
            print(f"  goal: {gid}")
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class Checkpoint:
    """One checkpoint snapshot."""

    id: str
    session_id: str
    created_at: float
    active_goals: list[str]
    budget: dict[str, Any]
    payload: dict[str, Any]
    shutdown_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "created_at": self.created_at,
            "active_goals": self.active_goals,
            "budget": self.budget,
            "payload": self.payload,
            "shutdown_reason": self.shutdown_reason,
        }


@dataclass
class RecoveryResult:
    """What was found from a previous run."""

    checkpoint: Checkpoint | None = None
    interrupted: bool = False

    @property
    def session_id(self) -> str | None:
        return self.checkpoint.session_id if self.checkpoint else None

    @property
    def active_goals(self) -> list[str]:
        return self.checkpoint.active_goals if self.checkpoint else []

    @property
    def budget(self) -> dict[str, Any]:
        return self.checkpoint.budget if self.checkpoint else {}

    @property
    def payload(self) -> dict[str, Any]:
        return self.checkpoint.payload if self.checkpoint else {}


# ---------------------------------------------------------------------------
# CheckpointManager
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    created_at      REAL NOT NULL,
    active_goals    TEXT NOT NULL DEFAULT '[]',
    budget_json     TEXT NOT NULL DEFAULT '{}',
    payload_json    TEXT NOT NULL DEFAULT '{}',
    shutdown_reason TEXT
);
CREATE INDEX IF NOT EXISTS cp_session ON checkpoints(session_id);
CREATE INDEX IF NOT EXISTS cp_time    ON checkpoints(created_at DESC);
"""


class CheckpointManager:
    """Write checkpoints for one daemon session.

    Parameters
    ----------
    db_path:
        SQLite database path.  Use ``:memory:`` for tests.
    session_id:
        The current daemon session identifier.
    keep_last_n:
        Only retain the last N checkpoints per session.  Default 10.
    """

    def __init__(
        self,
        db_path: Path | str = ":memory:",
        *,
        session_id: str | None = None,
        keep_last_n: int = 10,
    ) -> None:
        self._db_path = str(db_path)
        self._session_id = session_id or f"sess_{uuid.uuid4().hex[:8]}"
        self._keep_last_n = keep_last_n
        self._mem_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:")
            self._mem_conn.row_factory = sqlite3.Row
        else:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @property
    def session_id(self) -> str:
        return self._session_id

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(
        self,
        *,
        goals: list[str] | None = None,
        budget: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        shutdown_reason: str | None = None,
    ) -> str:
        """Write a checkpoint and return its ID."""
        cp_id = f"cp_{uuid.uuid4().hex[:12]}"
        now = time.time()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO checkpoints
                    (id, session_id, created_at, active_goals,
                     budget_json, payload_json, shutdown_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cp_id,
                    self._session_id,
                    now,
                    json.dumps(goals or []),
                    json.dumps(budget or {}),
                    json.dumps(payload or {}),
                    shutdown_reason,
                ),
            )

        self._prune()
        return cp_id

    def mark_shutdown(self, reason: str) -> None:
        """Update the latest checkpoint with a shutdown reason."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE checkpoints SET shutdown_reason = ?
                WHERE id = (
                    SELECT id FROM checkpoints
                    WHERE session_id = ?
                    ORDER BY created_at DESC LIMIT 1
                )
                """,
                (reason, self._session_id),
            )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def latest(self) -> Checkpoint | None:
        """Return the most recent checkpoint for this session."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE session_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (self._session_id,),
            ).fetchone()
        return self._row_to_checkpoint(row) if row else None

    def list_all(self, *, limit: int = 50) -> list[Checkpoint]:
        """Return all checkpoints for this session, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM checkpoints WHERE session_id = ? "
                "ORDER BY rowid DESC LIMIT ?",
                (self._session_id, limit),
            ).fetchall()
        return [self._row_to_checkpoint(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE session_id = ?",
                (self._session_id,),
            ).fetchone()[0]

    def delete_all(self) -> int:
        """Delete all checkpoints for this session.  Returns rows deleted."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM checkpoints WHERE session_id = ?",
                (self._session_id,),
            )
        return cur.rowcount

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _prune(self) -> None:
        """Keep only the last N checkpoints for this session."""
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM checkpoints WHERE session_id = ? AND rowid NOT IN (
                    SELECT rowid FROM checkpoints WHERE session_id = ?
                    ORDER BY rowid DESC LIMIT ?
                )
                """,
                (self._session_id, self._session_id, self._keep_last_n),
            )

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        if self._mem_conn is not None:
            try:
                yield self._mem_conn
                self._mem_conn.commit()
            except Exception:
                self._mem_conn.rollback()
                raise
            return
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _row_to_checkpoint(row: sqlite3.Row) -> Checkpoint:
        d = dict(row)
        return Checkpoint(
            id=d["id"],
            session_id=d["session_id"],
            created_at=d["created_at"],
            active_goals=json.loads(d.get("active_goals") or "[]"),
            budget=json.loads(d.get("budget_json") or "{}"),
            payload=json.loads(d.get("payload_json") or "{}"),
            shutdown_reason=d.get("shutdown_reason"),
        )


# ---------------------------------------------------------------------------
# RecoveryManager
# ---------------------------------------------------------------------------


class RecoveryManager:
    """Read checkpoints from any session to support restart recovery.

    Parameters
    ----------
    db_path:
        The same database used by :class:`CheckpointManager`.
    """

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self._db_path = str(db_path)
        self._mem_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:")
            self._mem_conn.row_factory = sqlite3.Row
        else:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # Ensure schema exists (in case this is a fresh DB)
        self._init_schema()

    def latest(self, *, session_id: str | None = None) -> RecoveryResult:
        """Return the latest checkpoint, optionally filtered by session.

        A result is considered "interrupted" if the checkpoint has no
        ``shutdown_reason`` (i.e. the daemon was killed abruptly) OR
        if the reason is not ``"requested"`` / ``"graceful"``.
        """
        with self._connect() as conn:
            if session_id:
                row = conn.execute(
                    "SELECT * FROM checkpoints WHERE session_id = ? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (session_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM checkpoints ORDER BY created_at DESC LIMIT 1"
                ).fetchone()

        if row is None:
            return RecoveryResult(interrupted=False)

        cp = CheckpointManager._row_to_checkpoint(row)
        graceful = cp.shutdown_reason in ("requested", "graceful", None.__class__.__name__)
        interrupted = cp.shutdown_reason not in ("requested", "graceful") or cp.shutdown_reason is None
        return RecoveryResult(checkpoint=cp, interrupted=interrupted)

    def list_sessions(self) -> list[str]:
        """Return distinct session IDs from all checkpoints."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id FROM checkpoints "
                "GROUP BY session_id ORDER BY MAX(created_at) DESC"
            ).fetchall()
        return [r["session_id"] for r in rows]

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        if self._mem_conn is not None:
            try:
                yield self._mem_conn
                self._mem_conn.commit()
            except Exception:
                self._mem_conn.rollback()
                raise
            return
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
