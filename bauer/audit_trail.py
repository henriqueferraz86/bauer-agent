"""Audit trail — append-only log of all agent actions.

Every significant action the autonomous agent takes is recorded in an
append-only SQLite table.  Entries are never updated or deleted (only the
full table can be purged via :meth:`AuditTrail.purge_before`).

This allows operators to:
* Review exactly what the agent did and why
* Debug unexpected behavior
* Satisfy compliance requirements

Schema
------
``audit_events`` table::

    id          TEXT PRIMARY KEY   (audit_<ulid12>)
    session_id  TEXT
    event_type  TEXT               (tool_call|llm_call|goal_start|goal_done|
                                    escalation|approval|config_change|error)
    actor       TEXT               (worker_0 | daemon | planner | ...)
    resource    TEXT               (what was acted upon: tool name, file, URL)
    action      TEXT               (execute | approve | deny | complete | ...)
    outcome     TEXT               (success | failure | denied | skipped)
    detail      TEXT               (JSON blob with extra context)
    duration_ms REAL               (wall time in milliseconds, if applicable)
    created_at  REAL               (time.time())

Usage::

    from bauer.audit_trail import AuditTrail

    trail = AuditTrail()
    trail.log(
        event_type="tool_call",
        actor="worker_0",
        resource="run_command",
        action="execute",
        outcome="success",
        detail={"command": "pytest tests/", "exit_code": 0},
        duration_ms=1234.5,
    )

    events = trail.query(event_type="tool_call", limit=10)
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class AuditEvent:
    """One audit log entry."""

    id: str
    session_id: str | None
    event_type: str
    actor: str
    resource: str
    action: str
    outcome: str
    detail: dict[str, Any]
    duration_ms: float | None
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "actor": self.actor,
            "resource": self.resource,
            "action": self.action,
            "outcome": self.outcome,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "created_at": self.created_at,
        }


# Valid event types (extensible — unknown types are stored as-is)
VALID_EVENT_TYPES = frozenset({
    "tool_call", "llm_call", "goal_start", "goal_done", "goal_failed",
    "escalation", "approval", "config_change", "error", "trigger_fired",
    "checkpoint", "shutdown", "startup",
})

# Valid outcomes
VALID_OUTCOMES = frozenset({"success", "failure", "denied", "skipped", "partial"})


# ---------------------------------------------------------------------------
# AuditTrail
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id          TEXT PRIMARY KEY,
    session_id  TEXT,
    event_type  TEXT NOT NULL,
    actor       TEXT NOT NULL DEFAULT 'unknown',
    resource    TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL DEFAULT '',
    outcome     TEXT NOT NULL DEFAULT 'success',
    detail_json TEXT NOT NULL DEFAULT '{}',
    duration_ms REAL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ae_session   ON audit_events(session_id);
CREATE INDEX IF NOT EXISTS ae_type      ON audit_events(event_type);
CREATE INDEX IF NOT EXISTS ae_time      ON audit_events(created_at DESC);
CREATE INDEX IF NOT EXISTS ae_outcome   ON audit_events(outcome);
"""


class AuditTrail:
    """Append-only audit log.

    Parameters
    ----------
    db_path:
        SQLite database path.  Use ``:memory:`` for tests.
    session_id:
        Optional default session ID attached to every entry.
    max_size_mb:
        Soft cap on database size in MB.  When exceeded, entries older
        than ``max_age_days`` are pruned.  Default 100 MB.
    max_age_days:
        Entries older than this many days are eligible for pruning.
        Default 30 days.
    """

    def __init__(
        self,
        db_path: Path | str = ":memory:",
        *,
        session_id: str | None = None,
        max_size_mb: float = 100.0,
        max_age_days: int = 30,
    ) -> None:
        self._db_path = str(db_path)
        self._session_id = session_id
        self._max_size_mb = max_size_mb
        self._max_age_days = max_age_days
        self._mem_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:")
            self._mem_conn.row_factory = sqlite3.Row
        else:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log(
        self,
        event_type: str,
        *,
        actor: str = "daemon",
        resource: str = "",
        action: str = "",
        outcome: str = "success",
        detail: dict[str, Any] | None = None,
        duration_ms: float | None = None,
        session_id: str | None = None,
        created_at: float | None = None,
    ) -> str:
        """Append one audit event and return its ID."""
        event_id = f"audit_{uuid.uuid4().hex[:12]}"
        now = created_at if created_at is not None else time.time()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_events
                    (id, session_id, event_type, actor, resource, action,
                     outcome, detail_json, duration_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id or self._session_id,
                    event_type[:64],
                    actor[:128],
                    resource[:512],
                    action[:128],
                    outcome[:64],
                    json.dumps(detail or {}),
                    duration_ms,
                    now,
                ),
            )
        return event_id

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, event_id: str) -> AuditEvent | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM audit_events WHERE id = ?", (event_id,)
            ).fetchone()
        return self._row_to_event(row) if row else None

    def query(
        self,
        *,
        event_type: str | None = None,
        actor: str | None = None,
        outcome: str | None = None,
        session_id: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """Return audit events matching the given filters."""
        clauses: list[str] = []
        params: list[Any] = []

        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("created_at <= ?")
            params.append(until)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM audit_events {where} "
                f"ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def count(
        self,
        *,
        event_type: str | None = None,
        outcome: str | None = None,
    ) -> int:
        clauses: list[str] = []
        params: list[Any] = []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if outcome:
            clauses.append("outcome = ?")
            params.append(outcome)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM audit_events {where}", params
            ).fetchone()[0]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def purge_before(self, cutoff: float) -> int:
        """Delete entries older than *cutoff* (epoch seconds).  Returns count."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM audit_events WHERE created_at < ?", (cutoff,)
            )
        return cur.rowcount

    def stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            by_type = {
                row["event_type"]: row["cnt"]
                for row in conn.execute(
                    "SELECT event_type, COUNT(*) AS cnt FROM audit_events "
                    "GROUP BY event_type"
                ).fetchall()
            }
            by_outcome = {
                row["outcome"]: row["cnt"]
                for row in conn.execute(
                    "SELECT outcome, COUNT(*) AS cnt FROM audit_events "
                    "GROUP BY outcome"
                ).fetchall()
            }
        return {
            "total": total,
            "by_type": by_type,
            "by_outcome": by_outcome,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
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

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> AuditEvent:
        d = dict(row)
        return AuditEvent(
            id=d["id"],
            session_id=d.get("session_id"),
            event_type=d["event_type"],
            actor=d.get("actor") or "unknown",
            resource=d.get("resource") or "",
            action=d.get("action") or "",
            outcome=d.get("outcome") or "success",
            detail=json.loads(d.get("detail_json") or "{}"),
            duration_ms=d.get("duration_ms"),
            created_at=d.get("created_at") or 0.0,
        )
