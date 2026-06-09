"""Goal tracker — SQLite persistence for autonomous agent goals.

Each *goal* is a high-level objective the agent is working toward.
Goals are decomposed into :class:`PlanStep` objects by
:mod:`bauer.autonomous_planner`.  The tracker persists goal state so
the daemon can survive restarts and resume in-progress work.

Schema
------
``goals`` table::

    id          TEXT PRIMARY KEY   (goal_<ulid>)
    title       TEXT NOT NULL
    description TEXT
    status      TEXT               (pending|running|done|failed|cancelled)
    priority    INTEGER DEFAULT 5  (1=highest, 10=lowest)
    steps_json  TEXT               (JSON list of step dicts)
    created_at  REAL               (time.time())
    started_at  REAL
    completed_at REAL
    session_id  TEXT               (daemon session that owns this goal)
    error       TEXT               (last failure message)

Usage::

    from bauer.goal_tracker import GoalTracker, GoalStatus

    tracker = GoalTracker(db_path=Path("~/.bauer/goals.db"))
    goal_id = tracker.create("Refactor the auth module")
    tracker.update_status(goal_id, GoalStatus.RUNNING)
    ...
    tracker.mark_complete(goal_id, summary="Done — 3 files changed")
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Generator


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class GoalStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class GoalRecord:
    """Immutable snapshot of one goal row."""

    id: str
    title: str
    status: GoalStatus
    description: str = ""
    priority: int = 5
    steps: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = 0.0
    started_at: float | None = None
    completed_at: float | None = None
    session_id: str | None = None
    error: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in (GoalStatus.DONE, GoalStatus.FAILED, GoalStatus.CANCELLED)

    @property
    def elapsed_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.completed_at or time.time()
        return end - self.started_at


# ---------------------------------------------------------------------------
# GoalTracker
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    description  TEXT DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',
    priority     INTEGER NOT NULL DEFAULT 5,
    steps_json   TEXT NOT NULL DEFAULT '[]',
    created_at   REAL NOT NULL,
    started_at   REAL,
    completed_at REAL,
    session_id   TEXT,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS goals_session ON goals(session_id);
"""


class GoalTracker:
    """Persist and query autonomous agent goals.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.  Created (including parent
        directories) if it does not exist.  Pass ``:memory:`` for an
        in-process ephemeral database (useful in tests).
    session_id:
        Optional daemon session identifier that is stored alongside
        each goal created in this tracker instance.
    """

    def __init__(
        self,
        db_path: Path | str = ":memory:",
        *,
        session_id: str | None = None,
    ) -> None:
        self._db_path = str(db_path)
        self._session_id = session_id
        # For :memory: we keep a single persistent connection because each
        # sqlite3.connect(":memory:") call opens a *different* empty database.
        self._mem_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:")
            self._mem_conn.row_factory = sqlite3.Row
        else:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create(
        self,
        title: str,
        *,
        description: str = "",
        priority: int = 5,
        steps: list[dict[str, Any]] | None = None,
        session_id: str | None = None,
    ) -> str:
        """Create a new goal and return its ID.

        Parameters
        ----------
        title:
            Short (≤ 200 chars) human-readable goal description.
        description:
            Optional longer description / acceptance criteria.
        priority:
            1 (highest) to 10 (lowest).  Default 5.
        steps:
            Optional initial step list.  Each step is a dict with at
            least a ``title`` key.
        session_id:
            Override the tracker-level session_id for this goal.
        """
        goal_id = f"goal_{uuid.uuid4().hex[:12]}"
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO goals
                    (id, title, description, status, priority,
                     steps_json, created_at, session_id)
                VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    goal_id,
                    title[:200],
                    description,
                    max(1, min(10, priority)),
                    json.dumps(steps or []),
                    now,
                    session_id or self._session_id,
                ),
            )
        return goal_id

    def get(self, goal_id: str) -> GoalRecord | None:
        """Fetch a single goal by ID, or ``None`` if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM goals WHERE id = ?", (goal_id,)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def update_status(
        self,
        goal_id: str,
        status: GoalStatus | str,
        *,
        error: str | None = None,
        steps: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Update goal status (and optionally its steps / error).

        Returns True if the row was found and updated, False otherwise.
        """
        status_val = GoalStatus(status).value if isinstance(status, str) else status.value
        now = time.time()

        fields: list[str] = ["status = ?"]
        params: list[Any] = [status_val]

        if status_val == GoalStatus.RUNNING.value:
            fields.append("started_at = COALESCE(started_at, ?)")
            params.append(now)
        if status_val in (GoalStatus.DONE.value, GoalStatus.FAILED.value,
                          GoalStatus.CANCELLED.value):
            fields.append("completed_at = ?")
            params.append(now)
        if error is not None:
            fields.append("error = ?")
            params.append(error)
        if steps is not None:
            fields.append("steps_json = ?")
            params.append(json.dumps(steps))

        params.append(goal_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE goals SET {', '.join(fields)} WHERE id = ?",
                params,
            )
        return cur.rowcount > 0

    def update_steps(self, goal_id: str, steps: list[dict[str, Any]]) -> bool:
        """Persist the current step list for a goal."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE goals SET steps_json = ? WHERE id = ?",
                (json.dumps(steps), goal_id),
            )
        return cur.rowcount > 0

    def mark_complete(self, goal_id: str, *, summary: str = "") -> bool:
        """Convenience: mark a goal as DONE with an optional summary."""
        rec = self.get(goal_id)
        if rec is None:
            return False
        steps = rec.steps
        if summary and steps:
            # Append summary as a pseudo-step for history.
            steps = steps + [{"title": f"[summary] {summary}", "status": "done"}]
        return self.update_status(goal_id, GoalStatus.DONE, steps=steps)

    def mark_failed(self, goal_id: str, *, error: str = "") -> bool:
        """Convenience: mark a goal as FAILED."""
        return self.update_status(goal_id, GoalStatus.FAILED, error=error)

    def cancel(self, goal_id: str) -> bool:
        """Cancel a goal."""
        return self.update_status(goal_id, GoalStatus.CANCELLED)

    def delete(self, goal_id: str) -> bool:
        """Hard-delete a goal record (use sparingly — prefer cancel/fail)."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_active(self) -> list[GoalRecord]:
        """Return all pending + running goals, highest priority first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status IN ('pending', 'running') "
                "ORDER BY priority ASC, created_at ASC"
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_by_status(self, status: GoalStatus | str) -> list[GoalRecord]:
        """Return all goals with a given status."""
        status_val = GoalStatus(status).value if isinstance(status, str) else status.value
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status = ? ORDER BY created_at DESC",
                (status_val,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_all(self, *, limit: int = 100, offset: int = 0) -> list[GoalRecord]:
        """Return goals sorted by creation time (most recent first)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM goals ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def count(self, status: GoalStatus | str | None = None) -> int:
        """Count goals, optionally filtered by status."""
        with self._connect() as conn:
            if status is None:
                return conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
            status_val = GoalStatus(status).value if isinstance(status, str) else status.value
            return conn.execute(
                "SELECT COUNT(*) FROM goals WHERE status = ?", (status_val,)
            ).fetchone()[0]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        if self._mem_conn is not None:
            # In-memory DB: reuse the single persistent connection.
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
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> GoalRecord:
        d = dict(row)
        return GoalRecord(
            id=d["id"],
            title=d["title"],
            description=d.get("description") or "",
            status=GoalStatus(d["status"]),
            priority=d.get("priority", 5),
            steps=json.loads(d.get("steps_json") or "[]"),
            created_at=d.get("created_at") or 0.0,
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            session_id=d.get("session_id"),
            error=d.get("error"),
        )
