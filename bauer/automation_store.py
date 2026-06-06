"""Durable automation/cron store for Bauer.

The scheduler layer intentionally only queues Kanban tasks. Task execution stays
inside TaskDispatcher/orchestration so retries, workers, logs and tool policies
remain centralized.
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ACTIVE_JOB_STATUSES = {"active"}
TERMINAL_RUN_STATUSES = {"queued", "skipped", "failed"}


@dataclass(frozen=True)
class AutomationJob:
    job_id: str
    name: str
    prompt: str
    schedule_str: str
    schedule: dict[str, Any]
    status: str = "active"
    next_run_at: str = ""
    last_run_at: str = ""
    run_count: int = 0
    fail_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AutomationRun:
    run_id: str
    job_id: str
    task_id: str = ""
    status: str = "queued"
    due_at: str = ""
    queued_at: str = ""
    finished_at: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class AutomationStore:
    """SQLite-backed automation jobs scoped to one workspace."""

    def __init__(self, workspace: str | Path = "workspace"):
        self.workspace = Path(workspace).resolve()
        self.store_dir = self.workspace / ".bauer_automation"
        self.db_path = self.store_dir / "automations.sqlite3"

    def create_job(
        self,
        *,
        name: str,
        prompt: str,
        schedule: str,
        job_id: str = "",
        status: str = "active",
        next_run_at: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> AutomationJob:
        clean_name = _clean_name(name)
        if not clean_name:
            raise ValueError("automation job name is required")
        if not prompt.strip():
            raise ValueError("automation job prompt is required")
        parsed = parse_schedule(schedule)
        now = now_iso()
        next_run_at = next_run_at or next_run_after(parsed, after=now)
        job_id = job_id or f"auto-{uuid.uuid4().hex[:12]}"
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO automation_jobs
                    (job_id, name, prompt, schedule_str, schedule_json, status,
                     next_run_at, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    clean_name,
                    prompt.strip(),
                    schedule.strip(),
                    _json(parsed),
                    status,
                    next_run_at,
                    now,
                    now,
                    _json(metadata or {}),
                ),
            )
            row = conn.execute("SELECT * FROM automation_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _job_from_row(row)

    def update_job(
        self,
        job_id_or_name: str,
        *,
        status: str | None = None,
        next_run_at: str | None = None,
        last_run_at: str | None = None,
        run_count_delta: int = 0,
        fail_count_delta: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> AutomationJob | None:
        with self._connection() as conn:
            row = self._select_job_row(conn, job_id_or_name)
            if row is None:
                return None
            current = _job_from_row(row)
            merged_metadata = dict(current.metadata)
            if metadata:
                merged_metadata.update(metadata)
            conn.execute(
                """
                UPDATE automation_jobs
                SET status = ?, next_run_at = ?, last_run_at = ?,
                    run_count = ?, fail_count = ?, updated_at = ?, metadata_json = ?
                WHERE job_id = ?
                """,
                (
                    status if status is not None else current.status,
                    next_run_at if next_run_at is not None else current.next_run_at,
                    last_run_at if last_run_at is not None else current.last_run_at,
                    max(0, current.run_count + int(run_count_delta)),
                    max(0, current.fail_count + int(fail_count_delta)),
                    now_iso(),
                    _json(merged_metadata),
                    current.job_id,
                ),
            )
            row = conn.execute("SELECT * FROM automation_jobs WHERE job_id = ?", (current.job_id,)).fetchone()
        return _job_from_row(row)

    def delete_job(self, job_id_or_name: str) -> bool:
        with self._connection() as conn:
            row = self._select_job_row(conn, job_id_or_name)
            if row is None:
                return False
            conn.execute("DELETE FROM automation_jobs WHERE job_id = ?", (row["job_id"],))
        return True

    def get_job(self, job_id_or_name: str) -> AutomationJob | None:
        with self._connection() as conn:
            row = self._select_job_row(conn, job_id_or_name)
        return _job_from_row(row) if row else None

    def list_jobs(self, *, limit: int = 100, statuses: list[str] | None = None) -> list[AutomationJob]:
        params: list[Any] = []
        sql = "SELECT * FROM automation_jobs"
        if statuses:
            sql += " WHERE status IN (" + ", ".join("?" for _ in statuses) + ")"
            params.extend(statuses)
        sql += " ORDER BY status ASC, next_run_at ASC, name ASC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_job_from_row(row) for row in rows]

    def due_jobs(self, *, now: str | None = None, limit: int = 20) -> list[AutomationJob]:
        now = now or now_iso()
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM automation_jobs
                WHERE status IN ('active')
                  AND next_run_at != ''
                  AND next_run_at <= ?
                ORDER BY next_run_at ASC, name ASC
                LIMIT ?
                """,
                (now, max(1, int(limit))),
            ).fetchall()
        return [_job_from_row(row) for row in rows]

    def create_run(
        self,
        *,
        job_id: str,
        due_at: str,
        task_id: str = "",
        status: str = "queued",
        run_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> AutomationRun:
        run_id = run_id or f"autorun-{uuid.uuid4().hex[:12]}"
        now = now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO automation_runs
                    (run_id, job_id, task_id, status, due_at, queued_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, job_id, task_id, status, due_at, now, _json(metadata or {})),
            )
            row = conn.execute("SELECT * FROM automation_runs WHERE run_id = ?", (run_id,)).fetchone()
        return _run_from_row(row)

    def update_run(
        self,
        run_id: str,
        *,
        task_id: str | None = None,
        status: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AutomationRun | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM automation_runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            current = _run_from_row(row)
            merged_metadata = dict(current.metadata)
            if metadata:
                merged_metadata.update(metadata)
            next_status = status or current.status
            finished_at = current.finished_at
            if next_status in TERMINAL_RUN_STATUSES and not finished_at:
                finished_at = now_iso()
            conn.execute(
                """
                UPDATE automation_runs
                SET task_id = ?, status = ?, error = ?, finished_at = ?, metadata_json = ?
                WHERE run_id = ?
                """,
                (
                    task_id if task_id is not None else current.task_id,
                    next_status,
                    _short(error, 4000) if error is not None else current.error,
                    finished_at,
                    _json(merged_metadata),
                    run_id,
                ),
            )
            row = conn.execute("SELECT * FROM automation_runs WHERE run_id = ?", (run_id,)).fetchone()
        return _run_from_row(row)

    def list_runs(
        self,
        *,
        job_id: str = "",
        limit: int = 50,
    ) -> list[AutomationRun]:
        params: list[Any] = []
        sql = "SELECT * FROM automation_runs"
        if job_id:
            sql += " WHERE job_id = ?"
            params.append(job_id)
        sql += " ORDER BY queued_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_run_from_row(row) for row in rows]

    def _select_job_row(self, conn: sqlite3.Connection, job_id_or_name: str) -> sqlite3.Row | None:
        value = job_id_or_name.strip()
        return conn.execute(
            "SELECT * FROM automation_jobs WHERE job_id = ? OR name = ?",
            (value, value),
        ).fetchone()

    def _connect(self) -> sqlite3.Connection:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self._ensure_schema(conn)
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS automation_jobs (
                job_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                prompt TEXT NOT NULL,
                schedule_str TEXT NOT NULL,
                schedule_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'active',
                next_run_at TEXT NOT NULL DEFAULT '',
                last_run_at TEXT NOT NULL DEFAULT '',
                run_count INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_automation_jobs_due
                ON automation_jobs(status, next_run_at);

            CREATE TABLE IF NOT EXISTS automation_runs (
                run_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                task_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'queued',
                due_at TEXT NOT NULL DEFAULT '',
                queued_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (job_id) REFERENCES automation_jobs(job_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_automation_runs_job_queued
                ON automation_runs(job_id, queued_at DESC);
            """
        )


def parse_schedule(schedule: str) -> dict[str, Any]:
    raw = schedule.strip()
    lowered = raw.lower()
    if not raw:
        raise ValueError("schedule is required")

    match = re.match(r"^every\s+(\d+)([mhd])$", lowered)
    if match:
        value = int(match.group(1))
        if value <= 0:
            raise ValueError("interval schedule must be positive")
        unit = match.group(2)
        seconds = value * {"m": 60, "h": 3600, "d": 86400}[unit]
        return {"type": "interval", "value": value, "unit": unit, "seconds": seconds}

    match = re.match(r"^daily\s+(\d{1,2}):(\d{2})$", lowered)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            raise ValueError("daily schedule must use HH:MM in 24h time")
        return {"type": "daily", "hour": hour, "minute": minute}

    if lowered.startswith("at "):
        return {"type": "once", "at": _parse_datetime(raw[3:].strip()).isoformat()}

    if lowered.startswith("cron:") or lowered.startswith("cron "):
        expr = raw[5:].strip()
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError("cron schedule must have five fields")
        minute, hour, day, month, dow = parts
        if (day, month, dow) != ("*", "*", "*"):
            raise ValueError("cron v1 supports only '*' for day/month/dow")
        _parse_cron_field(minute, minimum=0, maximum=59)
        _parse_cron_field(hour, minimum=0, maximum=23)
        return {"type": "cron", "expression": expr, "minute": minute, "hour": hour}

    try:
        return {"type": "once", "at": _parse_datetime(raw).isoformat()}
    except ValueError as exc:
        raise ValueError(
            "schedule not recognized. Use 'every 30m', 'every 2h', "
            "'daily 09:00', 'at 2026-06-01T10:00:00+00:00' or 'cron: */15 * * * *'."
        ) from exc


def next_run_after(schedule: dict[str, Any], *, after: str | datetime | None = None) -> str:
    base = _coerce_datetime(after) if after is not None else datetime.now(timezone.utc)
    kind = schedule.get("type")
    if kind == "interval":
        return (base + timedelta(seconds=int(schedule["seconds"]))).isoformat()
    if kind == "daily":
        candidate = base.replace(
            hour=int(schedule["hour"]),
            minute=int(schedule["minute"]),
            second=0,
            microsecond=0,
        )
        if candidate <= base:
            candidate += timedelta(days=1)
        return candidate.isoformat()
    if kind == "once":
        return _parse_datetime(str(schedule["at"])).isoformat()
    if kind == "cron":
        return _next_cron(schedule, base).isoformat()
    raise ValueError(f"unsupported schedule type: {kind}")


def next_after_run(schedule: dict[str, Any], *, due_at: str | datetime | None = None) -> str:
    if schedule.get("type") == "once":
        return ""
    return next_run_after(schedule, after=due_at)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _next_cron(schedule: dict[str, Any], base: datetime) -> datetime:
    minute_field = str(schedule["minute"])
    hour_field = str(schedule["hour"])
    candidate = (base + timedelta(minutes=1)).replace(second=0, microsecond=0)
    deadline = candidate + timedelta(days=370)
    while candidate <= deadline:
        if _cron_matches(candidate.minute, minute_field, 0, 59) and _cron_matches(candidate.hour, hour_field, 0, 23):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError("could not compute next cron run within one year")


def _parse_cron_field(value: str, *, minimum: int, maximum: int) -> None:
    if value == "*":
        return
    if value.startswith("*/"):
        step = int(value[2:])
        if step <= 0 or step > maximum + 1:
            raise ValueError(f"invalid cron step: {value}")
        return
    parsed = int(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"cron field out of range: {value}")


def _cron_matches(value: int, field: str, minimum: int, maximum: int) -> bool:
    if field == "*":
        return True
    if field.startswith("*/"):
        return (value - minimum) % int(field[2:]) == 0
    parsed = int(field)
    return minimum <= parsed <= maximum and value == parsed


def _parse_datetime(value: str) -> datetime:
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def _coerce_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0)
    return _parse_datetime(value)


def _job_from_row(row: sqlite3.Row) -> AutomationJob:
    return AutomationJob(
        job_id=row["job_id"],
        name=row["name"],
        prompt=row["prompt"],
        schedule_str=row["schedule_str"],
        schedule=_loads_dict(row["schedule_json"]),
        status=row["status"],
        next_run_at=row["next_run_at"],
        last_run_at=row["last_run_at"],
        run_count=int(row["run_count"]),
        fail_count=int(row["fail_count"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        metadata=_loads_dict(row["metadata_json"]),
    )


def _run_from_row(row: sqlite3.Row) -> AutomationRun:
    return AutomationRun(
        run_id=row["run_id"],
        job_id=row["job_id"],
        task_id=row["task_id"],
        status=row["status"],
        due_at=row["due_at"],
        queued_at=row["queued_at"],
        finished_at=row["finished_at"],
        error=row["error"],
        metadata=_loads_dict(row["metadata_json"]),
    )


def _clean_name(name: str) -> str:
    return re.sub(r"\s+", "-", name.strip())[:120]


def _json(value: Any) -> str:
    def _clean(raw: Any) -> Any:
        if raw is None or isinstance(raw, (str, int, float, bool)):
            return raw
        if isinstance(raw, dict):
            return {str(k): _clean(v) for k, v in raw.items()}
        if isinstance(raw, (list, tuple, set)):
            return [_clean(v) for v in raw]
        return str(raw)

    return json.dumps(_clean(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads_dict(value: str) -> dict[str, Any]:
    try:
        raw = json.loads(value or "{}")
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def _short(value: str | None, limit: int) -> str:
    return (value or "")[:limit]
