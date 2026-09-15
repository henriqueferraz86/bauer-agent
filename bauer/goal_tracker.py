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

Additional lease fields allow a persistent controller to reclaim work after a
restart.  ``goal_materialized_tasks`` is the idempotency ledger between goals
and Kanban tasks.

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
    BLOCKED = "blocked"
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
    lease_owner: str | None = None
    lease_expires_at: float | None = None
    heartbeat_at: float | None = None
    lease_seconds: int = 300
    attempts: int = 0
    replans: int = 0

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            GoalStatus.DONE,
            GoalStatus.FAILED,
            GoalStatus.BLOCKED,
            GoalStatus.CANCELLED,
        )

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
    error        TEXT,
    lease_owner  TEXT,
    lease_expires_at REAL,
    heartbeat_at REAL,
    lease_seconds INTEGER NOT NULL DEFAULT 300,
    attempts     INTEGER NOT NULL DEFAULT 0,
    replans      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS goals_status ON goals(status);
CREATE INDEX IF NOT EXISTS goals_session ON goals(session_id);
CREATE TABLE IF NOT EXISTS goal_materialized_tasks (
    goal_id    TEXT NOT NULL,
    task_id    TEXT NOT NULL,
    step_key   TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (goal_id, step_key),
    UNIQUE (goal_id, task_id),
    FOREIGN KEY (goal_id) REFERENCES goals(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS goal_tasks_task ON goal_materialized_tasks(task_id);
"""

_GOAL_MIGRATION_COLUMNS = {
    "lease_owner": "TEXT",
    "lease_expires_at": "REAL",
    "heartbeat_at": "REAL",
    "lease_seconds": "INTEGER NOT NULL DEFAULT 300",
    "attempts": "INTEGER NOT NULL DEFAULT 0",
    "replans": "INTEGER NOT NULL DEFAULT 0",
}


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
        if status_val in (
            GoalStatus.DONE.value,
            GoalStatus.FAILED.value,
            GoalStatus.BLOCKED.value,
            GoalStatus.CANCELLED.value,
        ):
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

    # ------------------------------------------------------------------
    # Durable claims and task materialization
    # ------------------------------------------------------------------

    def claim_next(self, session_id: str, lease_seconds: int = 300) -> GoalRecord | None:
        """Atomically claim the next pending or stale running goal.

        ``BEGIN IMMEDIATE`` serializes candidate selection and the update, so
        two controller processes cannot claim the same goal. A running goal is
        claimable only after its lease expires.
        """
        owner = str(session_id).strip()
        if not owner:
            raise ValueError("session_id nao pode ser vazio")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds deve ser positivo")

        now = time.time()
        expires = now + lease_seconds
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM goals
                WHERE status = 'pending'
                   OR (status = 'running' AND
                       (lease_expires_at IS NULL OR lease_expires_at <= ?))
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None

            goal_id = str(row["id"])
            cur = conn.execute(
                """
                UPDATE goals
                SET status = 'running',
                    started_at = COALESCE(started_at, ?),
                    session_id = ?,
                    lease_owner = ?,
                    lease_expires_at = ?,
                    heartbeat_at = ?,
                    lease_seconds = ?,
                    attempts = attempts + 1,
                    completed_at = NULL
                WHERE id = ?
                  AND (status = 'pending' OR
                       (status = 'running' AND
                        (lease_expires_at IS NULL OR lease_expires_at <= ?)))
                """,
                (now, owner, owner, expires, now, lease_seconds, goal_id, now),
            )
            if cur.rowcount != 1:
                return None
            claimed = conn.execute(
                "SELECT * FROM goals WHERE id = ?", (goal_id,)
            ).fetchone()
        return self._row_to_record(claimed) if claimed else None

    def heartbeat(self, goal_id: str, session_id: str) -> bool:
        """Renew a goal lease only when ``session_id`` is its current owner."""
        owner = str(session_id).strip()
        if not owner:
            return False
        now = time.time()
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE goals
                SET heartbeat_at = ?,
                    lease_expires_at = ? + lease_seconds
                WHERE id = ? AND status = 'running' AND lease_owner = ?
                """,
                (now, now, goal_id, owner),
            )
        return cur.rowcount > 0

    def increment_replans(self, goal_id: str) -> int | None:
        """Increment and return a goal's durable replan counter."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE goals SET replans = replans + 1 WHERE id = ?",
                (goal_id,),
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT replans FROM goals WHERE id = ?", (goal_id,)
            ).fetchone()
        return int(row[0]) if row else None

    def release_or_requeue_stale(self, now: float | None = None) -> int:
        """Return expired running goals to ``pending`` without losing history."""
        cutoff = time.time() if now is None else float(now)
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE goals
                SET status = 'pending',
                    session_id = NULL,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    completed_at = NULL
                WHERE status = 'running'
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at <= ?
                """,
                (cutoff,),
            )
        return cur.rowcount

    def record_materialized_task(self, goal_id: str, task_id: str, step_key: str) -> bool:
        """Record a goal step/task link, returning whether it was newly added.

        Replaying the exact same link is a no-op. A controller can call
        :meth:`get_materialized_task` before creating a task to recover from a
        crash between task creation and this ledger write.
        """
        clean_goal = str(goal_id).strip()
        clean_task = str(task_id).strip()
        clean_step = str(step_key).strip()
        if not clean_goal or not clean_task or not clean_step:
            raise ValueError("goal_id, task_id e step_key sao obrigatorios")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO goal_materialized_tasks
                    (goal_id, task_id, step_key, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (clean_goal, clean_task, clean_step, time.time()),
            )
        return cur.rowcount == 1

    def get_materialized_task(self, goal_id: str, step_key: str) -> str | None:
        """Return the task already linked to a goal step, if any."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT task_id FROM goal_materialized_tasks
                WHERE goal_id = ? AND step_key = ?
                """,
                (goal_id, step_key),
            ).fetchone()
        return str(row[0]) if row else None

    def list_materialized_tasks(self, goal_id: str) -> list[dict[str, str]]:
        """Return durable task links for one goal in insertion order."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT task_id, step_key FROM goal_materialized_tasks
                WHERE goal_id = ? ORDER BY created_at ASC
                """,
                (goal_id,),
            ).fetchall()
        return [{"task_id": str(row[0]), "step_key": str(row[1])} for row in rows]

    def clear_materialized_tasks(self, goal_id: str) -> int:
        """Drop active step links before a new replan, preserving Kanban history."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM goal_materialized_tasks WHERE goal_id = ?",
                (goal_id,),
            )
        return cur.rowcount

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
            existing = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(goals)").fetchall()
            }
            for name, declaration in _GOAL_MIGRATION_COLUMNS.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE goals ADD COLUMN {name} {declaration}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS goals_lease ON goals(status, lease_expires_at)"
            )

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
            lease_owner=d.get("lease_owner"),
            lease_expires_at=d.get("lease_expires_at"),
            heartbeat_at=d.get("heartbeat_at"),
            lease_seconds=int(d.get("lease_seconds") or 300),
            attempts=int(d.get("attempts") or 0),
            replans=int(d.get("replans") or 0),
        )
