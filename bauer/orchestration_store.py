"""Durable store for orchestration runs and DAG node state."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled"}
TERMINAL_NODE_STATUSES = {"succeeded", "failed", "skipped"}


@dataclass(frozen=True)
class OrchestrationRun:
    run_id: str
    objective: str
    mode: str
    status: str
    plan: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    error: str = ""
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestrationNode:
    run_id: str
    step_id: int
    goal: str
    status: str
    depends_on: list[int] = field(default_factory=list)
    tools: bool = True
    agent: str = ""
    task_id: str = ""
    dispatch_run_id: str = ""
    model_used: str = ""
    response: str = ""
    tool_log: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""


class OrchestrationStore:
    """SQLite-backed orchestration run/node store scoped to one workspace."""

    def __init__(self, workspace: str | Path = "workspace"):
        self.workspace = Path(workspace).resolve()
        self.store_dir = self.workspace / ".bauer_orchestrator"
        self.db_path = self.store_dir / "orchestrations.sqlite3"

    def create_run(
        self,
        *,
        run_id: str,
        objective: str,
        mode: str,
        plan: list[dict[str, Any]],
        status: str = "planned",
        metadata: dict[str, Any] | None = None,
    ) -> OrchestrationRun:
        if not run_id.strip():
            raise ValueError("run_id is required")
        now = _now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO orchestration_runs
                    (run_id, objective, mode, status, plan_json, started_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id.strip(),
                    objective,
                    mode,
                    status,
                    _json(plan),
                    now,
                    now,
                    _json(metadata or {}),
                ),
            )
            row = conn.execute("SELECT * FROM orchestration_runs WHERE run_id = ?", (run_id,)).fetchone()
        return _run_from_row(row)

    def upsert_planned_nodes(self, run_id: str, steps: list[dict[str, Any]]) -> None:
        with self._connection() as conn:
            now = _now_iso()
            for step in steps:
                conn.execute(
                    """
                    INSERT INTO orchestration_nodes
                        (run_id, step_id, goal, status, depends_on_json, tools, agent, updated_at)
                    VALUES (?, ?, ?, 'planned', ?, ?, ?, ?)
                    ON CONFLICT(run_id, step_id) DO UPDATE SET
                        goal = excluded.goal,
                        depends_on_json = excluded.depends_on_json,
                        tools = excluded.tools,
                        agent = excluded.agent,
                        updated_at = excluded.updated_at
                    """,
                    (
                        run_id,
                        int(step.get("id", 0)),
                        str(step.get("goal", "")),
                        _json([int(dep) for dep in step.get("depends_on", [])]),
                        1 if step.get("tools", True) else 0,
                        str(step.get("agent", "")),
                        now,
                    ),
                )

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        summary: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrchestrationRun | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM orchestration_runs WHERE run_id = ?", (run_id,)).fetchone()
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
                UPDATE orchestration_runs
                SET status = ?, summary = ?, error = ?, updated_at = ?, finished_at = ?, metadata_json = ?
                WHERE run_id = ?
                """,
                (
                    next_status,
                    _short(summary, 8000) if summary is not None else current.summary,
                    _short(error, 8000) if error is not None else current.error,
                    now,
                    finished_at,
                    _json(merged_metadata),
                    run_id,
                ),
            )
            row = conn.execute("SELECT * FROM orchestration_runs WHERE run_id = ?", (run_id,)).fetchone()
        return _run_from_row(row)

    def update_node(
        self,
        run_id: str,
        step_id: int,
        *,
        status: str | None = None,
        task_id: str | None = None,
        dispatch_run_id: str | None = None,
        model_used: str | None = None,
        response: str | None = None,
        tool_log: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> OrchestrationNode | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM orchestration_nodes WHERE run_id = ? AND step_id = ?",
                (run_id, int(step_id)),
            ).fetchone()
            if row is None:
                return None
            current = _node_from_row(row)
            next_status = status or current.status
            now = _now_iso()
            started_at = current.started_at
            if next_status == "running" and not started_at:
                started_at = now
            finished_at = current.finished_at
            if next_status in TERMINAL_NODE_STATUSES and not finished_at:
                finished_at = now
            conn.execute(
                """
                UPDATE orchestration_nodes
                SET status = ?, task_id = ?, dispatch_run_id = ?, model_used = ?,
                    response = ?, tool_log_json = ?, error = ?,
                    started_at = ?, updated_at = ?, finished_at = ?
                WHERE run_id = ? AND step_id = ?
                """,
                (
                    next_status,
                    _short(task_id, 100) if task_id is not None else current.task_id,
                    _short(dispatch_run_id, 200) if dispatch_run_id is not None else current.dispatch_run_id,
                    model_used if model_used is not None else current.model_used,
                    _short(response, 16000) if response is not None else current.response,
                    _json(tool_log) if tool_log is not None else _json(current.tool_log),
                    _short(error, 8000) if error is not None else current.error,
                    started_at,
                    now,
                    finished_at,
                    run_id,
                    int(step_id),
                ),
            )
            row = conn.execute(
                "SELECT * FROM orchestration_nodes WHERE run_id = ? AND step_id = ?",
                (run_id, int(step_id)),
            ).fetchone()
        return _node_from_row(row)

    def get_node(self, run_id: str, step_id: int) -> OrchestrationNode | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM orchestration_nodes WHERE run_id = ? AND step_id = ?",
                (run_id, int(step_id)),
            ).fetchone()
        return _node_from_row(row) if row else None

    def get_run(self, run_id: str) -> OrchestrationRun | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM orchestration_runs WHERE run_id = ?", (run_id,)).fetchone()
        return _run_from_row(row) if row else None

    def latest_resumable_run(self, objective: str) -> OrchestrationRun | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM orchestration_runs
                WHERE objective = ? AND status IN ('planned', 'running', 'paused', 'failed')
                ORDER BY updated_at DESC, started_at DESC
                LIMIT 1
                """,
                (objective,),
            ).fetchone()
        return _run_from_row(row) if row else None

    def list_runs(self, *, limit: int = 50, statuses: list[str] | None = None) -> list[OrchestrationRun]:
        params: list[Any] = []
        sql = "SELECT * FROM orchestration_runs"
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            sql += f" WHERE status IN ({placeholders})"
            params.extend(statuses)
        sql += " ORDER BY updated_at DESC, started_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_run_from_row(row) for row in rows]

    def list_nodes(self, run_id: str) -> list[OrchestrationNode]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM orchestration_nodes WHERE run_id = ? ORDER BY step_id ASC",
                (run_id,),
            ).fetchall()
        return [_node_from_row(row) for row in rows]

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
            CREATE TABLE IF NOT EXISTS orchestration_runs (
                run_id TEXT PRIMARY KEY,
                objective TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'hybrid',
                status TEXT NOT NULL DEFAULT 'planned',
                plan_json TEXT NOT NULL DEFAULT '[]',
                summary TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_orchestration_runs_objective_updated
                ON orchestration_runs(objective, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_orchestration_runs_status_updated
                ON orchestration_runs(status, updated_at DESC);

            CREATE TABLE IF NOT EXISTS orchestration_nodes (
                run_id TEXT NOT NULL,
                step_id INTEGER NOT NULL,
                goal TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'planned',
                depends_on_json TEXT NOT NULL DEFAULT '[]',
                tools INTEGER NOT NULL DEFAULT 1,
                agent TEXT NOT NULL DEFAULT '',
                task_id TEXT NOT NULL DEFAULT '',
                dispatch_run_id TEXT NOT NULL DEFAULT '',
                model_used TEXT NOT NULL DEFAULT '',
                response TEXT NOT NULL DEFAULT '',
                tool_log_json TEXT NOT NULL DEFAULT '[]',
                error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (run_id, step_id),
                FOREIGN KEY (run_id) REFERENCES orchestration_runs(run_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_orchestration_nodes_run_status
                ON orchestration_nodes(run_id, status);
            """
        )
        _ensure_column(conn, "orchestration_nodes", "task_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "orchestration_nodes", "dispatch_run_id", "TEXT NOT NULL DEFAULT ''")


def _run_from_row(row: sqlite3.Row) -> OrchestrationRun:
    return OrchestrationRun(
        run_id=row["run_id"],
        objective=row["objective"],
        mode=row["mode"],
        status=row["status"],
        plan=_loads_list(row["plan_json"]),
        summary=row["summary"],
        error=row["error"],
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
        metadata=_loads_dict(row["metadata_json"]),
    )


def _node_from_row(row: sqlite3.Row) -> OrchestrationNode:
    return OrchestrationNode(
        run_id=row["run_id"],
        step_id=int(row["step_id"]),
        goal=row["goal"],
        status=row["status"],
        depends_on=[int(dep) for dep in _loads_list(row["depends_on_json"])],
        tools=bool(row["tools"]),
        agent=row["agent"],
        task_id=row["task_id"],
        dispatch_run_id=row["dispatch_run_id"],
        model_used=row["model_used"],
        response=row["response"],
        tool_log=_loads_list(row["tool_log_json"]),
        error=row["error"],
        started_at=row["started_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
    )


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


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if column not in {str(row["name"]) for row in rows}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _loads_dict(value: str) -> dict[str, Any]:
    try:
        raw = json.loads(value or "{}")
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def _loads_list(value: str) -> list[Any]:
    try:
        raw = json.loads(value or "[]")
        return raw if isinstance(raw, list) else []
    except json.JSONDecodeError:
        return []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short(value: str | None, limit: int) -> str:
    return (value or "")[:limit]
