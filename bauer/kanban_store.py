"""Durable sidecar store for Bauer Kanban events and dispatcher runs.

TASKS.md remains the human-facing projection. This module keeps the operational
history that markdown is not good at: append-only events and run lifecycle rows.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_RUN_STATUSES = {"succeeded", "failed", "stale", "blocked", "cancelled"}


@dataclass(frozen=True)
class TaskEvent:
    id: int
    at: str
    task_id: str
    event_type: str
    actor: str
    status_from: str = ""
    status_to: str = ""
    run_id: str = ""
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskRun:
    run_id: str
    task_id: str
    status: str
    claim_id: str = ""
    runner: str = ""
    attempt: int = 0
    worker_pid: int | None = None
    log_path: str = ""
    started_at: str = ""
    heartbeat_at: str = ""
    finished_at: str = ""
    summary: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class KanbanStore:
    """SQLite-backed event/run store scoped to one Bauer workspace."""

    def __init__(self, workspace: str | Path = "workspace"):
        self.workspace = Path(workspace).resolve()
        self.dispatch_dir = self.workspace / ".bauer_dispatch"
        self.db_path = self.dispatch_dir / "kanban.sqlite3"

    def append_event(
        self,
        task_id: str,
        event_type: str,
        *,
        actor: str = "system",
        status_from: str = "",
        status_to: str = "",
        run_id: str = "",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> TaskEvent:
        task_id = _normalize_task_id(task_id)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO task_events
                    (at, task_id, event_type, actor, status_from, status_to, run_id, message, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _now_iso(),
                    task_id,
                    event_type.strip(),
                    actor.strip() or "system",
                    status_from.strip(),
                    status_to.strip(),
                    run_id.strip(),
                    _short(message, 2000),
                    _json(metadata or {}),
                ),
            )
            row = conn.execute(
                "SELECT * FROM task_events WHERE id = last_insert_rowid()"
            ).fetchone()
        return _event_from_row(row)

    def start_run(
        self,
        *,
        run_id: str,
        task_id: str,
        claim_id: str = "",
        runner: str = "",
        attempt: int = 0,
        log_path: str = "",
        status: str = "claimed",
        metadata: dict[str, Any] | None = None,
    ) -> TaskRun:
        run_id = run_id.strip()
        if not run_id:
            raise ValueError("run_id is required")
        now = _now_iso()
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT * FROM task_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO task_runs
                        (run_id, task_id, status, claim_id, runner, attempt, log_path,
                         started_at, heartbeat_at, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        _normalize_task_id(task_id),
                        status,
                        claim_id,
                        runner,
                        int(attempt or 0),
                        log_path,
                        now,
                        now,
                        _json(metadata or {}),
                    ),
                )
            row = conn.execute(
                "SELECT * FROM task_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return _run_from_row(row)

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        worker_pid: int | None = None,
        summary: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        heartbeat: bool = True,
    ) -> TaskRun | None:
        run_id = run_id.strip()
        if not run_id:
            return None
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM task_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            current = _run_from_row(row)
            merged_metadata = dict(current.metadata)
            if metadata:
                merged_metadata.update(metadata)
            next_status = status or current.status
            now = _now_iso()
            finished_at = current.finished_at
            if next_status in TERMINAL_RUN_STATUSES and not finished_at:
                finished_at = now
            conn.execute(
                """
                UPDATE task_runs
                SET status = ?, worker_pid = ?, heartbeat_at = ?, finished_at = ?,
                    summary = ?, error = ?, metadata_json = ?
                WHERE run_id = ?
                """,
                (
                    next_status,
                    worker_pid if worker_pid is not None else current.worker_pid,
                    now if heartbeat else current.heartbeat_at,
                    finished_at,
                    _short(summary, 4000) if summary is not None else current.summary,
                    _short(error, 4000) if error is not None else current.error,
                    _json(merged_metadata),
                    run_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM task_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return _run_from_row(row)

    def get_run(self, run_id: str) -> TaskRun | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM task_runs WHERE run_id = ?",
                (run_id.strip(),),
            ).fetchone()
        return _run_from_row(row) if row else None

    def latest_run_for_task(self, task_id: str) -> TaskRun | None:
        runs = self.list_runs(task_id=task_id, limit=1)
        return runs[0] if runs else None

    def list_runs(
        self,
        *,
        task_id: str = "",
        statuses: list[str] | tuple[str, ...] | None = None,
        limit: int = 50,
    ) -> list[TaskRun]:
        params: list[Any] = []
        where: list[str] = []
        if task_id:
            where.append("task_id = ?")
            params.append(_normalize_task_id(task_id))
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            where.append(f"status IN ({placeholders})")
            params.extend(statuses)
        sql = "SELECT * FROM task_runs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY heartbeat_at DESC, started_at DESC, run_id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_run_from_row(row) for row in rows]

    def list_events(self, *, task_id: str = "", limit: int = 50) -> list[TaskEvent]:
        params: list[Any] = []
        sql = "SELECT * FROM task_events"
        if task_id:
            sql += " WHERE task_id = ?"
            params.append(_normalize_task_id(task_id))
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_event_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        self.dispatch_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema(conn)
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                task_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                status_from TEXT NOT NULL DEFAULT '',
                status_to TEXT NOT NULL DEFAULT '',
                run_id TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_task_events_task_id_id
                ON task_events(task_id, id DESC);
            CREATE INDEX IF NOT EXISTS idx_task_events_type_id
                ON task_events(event_type, id DESC);

            CREATE TABLE IF NOT EXISTS task_runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                claim_id TEXT NOT NULL DEFAULT '',
                runner TEXT NOT NULL DEFAULT '',
                attempt INTEGER NOT NULL DEFAULT 0,
                worker_pid INTEGER,
                log_path TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_task_runs_task_id_started
                ON task_runs(task_id, started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_task_runs_status_started
                ON task_runs(status, started_at DESC);
            """
        )


def _event_from_row(row: sqlite3.Row) -> TaskEvent:
    return TaskEvent(
        id=int(row["id"]),
        at=row["at"],
        task_id=row["task_id"],
        event_type=row["event_type"],
        actor=row["actor"],
        status_from=row["status_from"],
        status_to=row["status_to"],
        run_id=row["run_id"],
        message=row["message"],
        metadata=_loads(row["metadata_json"]),
    )


def _run_from_row(row: sqlite3.Row) -> TaskRun:
    pid = row["worker_pid"]
    return TaskRun(
        run_id=row["run_id"],
        task_id=row["task_id"],
        status=row["status"],
        claim_id=row["claim_id"],
        runner=row["runner"],
        attempt=int(row["attempt"] or 0),
        worker_pid=int(pid) if pid is not None else None,
        log_path=row["log_path"],
        started_at=row["started_at"],
        heartbeat_at=row["heartbeat_at"],
        finished_at=row["finished_at"],
        summary=row["summary"],
        error=row["error"],
        metadata=_loads(row["metadata_json"]),
    )


def _json(value: dict[str, Any]) -> str:
    def _clean(raw: Any) -> Any:
        if raw is None or isinstance(raw, (str, int, float, bool)):
            return raw
        if isinstance(raw, dict):
            return {str(k): _clean(v) for k, v in raw.items()}
        if isinstance(raw, (list, tuple, set)):
            return [_clean(v) for v in raw]
        return str(raw)

    return json.dumps(_clean(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str) -> dict[str, Any]:
    try:
        raw = json.loads(value or "{}")
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def _normalize_task_id(task_id: str) -> str:
    raw = str(task_id).strip()
    if raw.upper().startswith("T") and raw[1:].isdigit():
        raw = raw[1:]
    if raw.isdigit():
        return str(int(raw)).zfill(3)
    return raw.zfill(3)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short(value: str | None, limit: int) -> str:
    return (value or "")[:limit]
