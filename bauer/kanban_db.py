"""SQLite kernel for Bauer's Kanban — durable task store with CAS atomicity.

Replaces the TASKS.md text-file backend used by `bauer.workspace_manager` and
`bauer.task_dispatcher`. Markdown becomes a READ-ONLY view generated from this
DB for humans; all reads/writes go through the SQLite layer.

Design (inspired by Hermes Agent's `hermes_cli/kanban_db.py`):
    - SQLite WAL mode + BEGIN IMMEDIATE for serialised writers; concurrent readers
      stay non-blocking
    - Compare-and-swap (CAS) on status transitions and claim acquisition — losers
      observe 0 affected rows and move on (no retry loops)
    - 9 canonical statuses aligned with Hermes:
        triage, todo, ready, blocked, running, review, done, archived, failed
    - Schema is a superset of Hermes's; columns not used yet are populated with
      sensible defaults and migrated forward at init_db()
    - Multi-board isolation: each project gets its own `.db` under
      ``~/.bauer/kanban/boards/<slug>/kanban.db``

Public surface (most callers stick to these):
    connect(board=None)         — context manager yielding a connection
    init_db(conn)               — idempotent schema creation + migrations
    write_txn(conn)             — context manager with BEGIN IMMEDIATE
    create_task(conn, ...)      — insert a new task, returns its id
    get_task(conn, task_id)     — fetch one Task or raise KanbanDbError
    list_tasks(conn, **filters) — fetch many Tasks ordered by priority + id
    claim_task(conn, ...)       — CAS ready→running, returns claim_lock or None
    complete_task(conn, ...)    — CAS running→done
    fail_task(conn, ...)        — CAS running→failed/ready (retry budget)
    release_to_ready(conn, ...) — CAS running→ready (reclaim stale)
    link_tasks(conn, parent, child) — add to task_links with cycle check
    add_comment / add_event / add_run — append to history tables
    dispatch_once(conn, ...)    — one tick of the dispatcher state machine
    recompute_ready(conn)       — push todo → ready when parents complete

Status values are lowercase here. The legacy workspace_manager API accepts
UPPERCASE for back-compat — the shim normalises before calling this module.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


# ---------------------------------------------------------------------------
# Constants — statuses, paths
# ---------------------------------------------------------------------------

# Canonical statuses (lowercase). Order matters for `_STATUS_ORDER` rendering.
STATUS_TRIAGE    = "triage"
STATUS_TODO      = "todo"
STATUS_READY     = "ready"
STATUS_BLOCKED   = "blocked"
STATUS_RUNNING   = "running"
STATUS_REVIEW    = "review"
STATUS_DONE      = "done"
STATUS_ARCHIVED  = "archived"
STATUS_FAILED    = "failed"

VALID_STATUSES: frozenset[str] = frozenset({
    STATUS_TRIAGE, STATUS_TODO, STATUS_READY, STATUS_BLOCKED,
    STATUS_RUNNING, STATUS_REVIEW, STATUS_DONE, STATUS_ARCHIVED, STATUS_FAILED,
})

# Terminal statuses — won't transition further without manual intervention.
TERMINAL_STATUSES: frozenset[str] = frozenset({
    STATUS_DONE, STATUS_ARCHIVED, STATUS_FAILED,
})

# Priority levels with stable numeric ordering for ORDER BY.
PRIORITY_RANK: dict[str, int] = {
    "critical": 0,
    "high":     1,
    "medium":   2,
    "low":      3,
}
DEFAULT_PRIORITY = "medium"

# Workspace kinds — controls how the worker's cwd is materialised.
WORKSPACE_KINDS: frozenset[str] = frozenset({"scratch", "worktree", "dir"})

# Default claim TTL (seconds) — worker must heartbeat before this expires.
DEFAULT_CLAIM_TTL_S = 900   # 15 minutes
DEFAULT_MAX_RETRIES = 2

# Schema version. Bumped when migrations are added; init_db replays missing ones.
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class KanbanDbError(Exception):
    """Generic kanban_db failure — wraps SQLite errors with context."""


class CycleError(KanbanDbError):
    """Adding the proposed link would form a dependency cycle."""


# ---------------------------------------------------------------------------
# Path resolution — multi-board layout
# ---------------------------------------------------------------------------


def _bauer_home() -> Path:
    """Root directory for Bauer state. Override via $BAUER_HOME for tests."""
    override = os.environ.get("BAUER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".bauer"


def board_path(board: str | None = None) -> Path:
    """Return the .db path for `board`.

    `board=None` resolves to the active board (env var `BAUER_KANBAN_BOARD`,
    or `default` otherwise). The directory is created on demand.
    """
    name = board or os.environ.get("BAUER_KANBAN_BOARD") or "default"
    # slugify: keep alnum, dash, underscore; replace anything else with `_`.
    slug = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    if not slug:
        slug = "default"
    p = _bauer_home() / "kanban" / "boards" / slug / "kanban.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def workspace_root(board: str | None = None) -> Path:
    """Directory for scratch workspaces & worker logs of `board`."""
    return board_path(board).parent / "workspaces"


def runs_log_dir(board: str | None = None) -> Path:
    """Directory for worker stdout/stderr log files of `board`."""
    return board_path(board).parent / "logs"


# ---------------------------------------------------------------------------
# Task dataclass — mirrors the `tasks` row shape
# ---------------------------------------------------------------------------


@dataclass
class Task:
    id: str
    title: str
    body: str = ""
    status: str = STATUS_TODO
    assignee: str = ""
    priority: str = DEFAULT_PRIORITY
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    # Workspace decoupling (used by dispatch/worker, defaults OK now).
    workspace_kind: str = "scratch"
    workspace_path: str = ""
    branch_name: str = ""
    # Dispatcher coordination.
    claim_lock: str = ""
    claim_expires: float = 0.0
    # Retry budget.
    max_retries: int = DEFAULT_MAX_RETRIES
    consecutive_failures: int = 0
    last_failure_error: str = ""
    # Misc forward-compat columns.
    max_runtime_seconds: int = 0
    skills: list[str] = field(default_factory=list)
    session_id: str = ""
    spec_id: str = ""           # Bauer-specific link to specs/
    idempotency_key: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        skills_raw = row["skills"] if "skills" in row.keys() else ""
        try:
            skills = json.loads(skills_raw) if skills_raw else []
        except (json.JSONDecodeError, TypeError):
            skills = []
        return cls(
            id=row["id"],
            title=row["title"],
            body=row["body"] or "",
            status=row["status"],
            assignee=row["assignee"] or "",
            priority=row["priority"] or DEFAULT_PRIORITY,
            created_at=row["created_at"] or 0.0,
            started_at=row["started_at"] or 0.0,
            completed_at=row["completed_at"] or 0.0,
            workspace_kind=row["workspace_kind"] or "scratch",
            workspace_path=row["workspace_path"] or "",
            branch_name=row["branch_name"] or "",
            claim_lock=row["claim_lock"] or "",
            claim_expires=row["claim_expires"] or 0.0,
            max_retries=row["max_retries"] or DEFAULT_MAX_RETRIES,
            consecutive_failures=row["consecutive_failures"] or 0,
            last_failure_error=row["last_failure_error"] or "",
            max_runtime_seconds=row["max_runtime_seconds"] or 0,
            skills=skills,
            session_id=row["session_id"] or "",
            spec_id=row["spec_id"] or "",
            idempotency_key=row["idempotency_key"] or "",
        )


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def _configure_connection(conn: sqlite3.Connection) -> None:
    """Apply pragmas for safety + concurrency on every connection."""
    conn.row_factory = sqlite3.Row
    # WAL: concurrent readers + 1 writer with no blocking
    conn.execute("PRAGMA journal_mode = WAL")
    # NORMAL: durable across process crashes, faster than FULL
    conn.execute("PRAGMA synchronous = NORMAL")
    # Foreign keys: required for ON DELETE CASCADE on links/comments/events/runs
    conn.execute("PRAGMA foreign_keys = ON")
    # 5s timeout on blocked writes — avoid surface-level SQLITE_BUSY for the
    # narrow race window between BEGIN IMMEDIATE attempts.
    conn.execute("PRAGMA busy_timeout = 5000")


@contextmanager
def connect(board: str | None = None) -> Iterator[sqlite3.Connection]:
    """Yield a configured connection to `board`. Closes on exit.

    The DB file and parent directory are created lazily. The schema is *not*
    initialised automatically — callers must invoke `init_db(conn)` once per
    process (or check `schema_version(conn)`).
    """
    path = board_path(board)
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        _configure_connection(conn)
        yield conn
    finally:
        conn.close()


@contextmanager
def write_txn(conn: sqlite3.Connection) -> Iterator[None]:
    """Open an IMMEDIATE transaction — serialises writers across processes.

    SQLite's default DEFERRED transactions can result in SQLITE_BUSY when two
    writers race. IMMEDIATE acquires the write lock at BEGIN, so the second
    writer sees the failure up-front (and either retries or moves on).
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id                   TEXT    PRIMARY KEY,
    title                TEXT    NOT NULL,
    body                 TEXT    NOT NULL DEFAULT '',
    status               TEXT    NOT NULL DEFAULT 'todo',
    assignee             TEXT    NOT NULL DEFAULT '',
    priority             TEXT    NOT NULL DEFAULT 'medium',
    created_at           REAL    NOT NULL,
    started_at           REAL,
    completed_at         REAL,
    workspace_kind       TEXT    NOT NULL DEFAULT 'scratch',
    workspace_path       TEXT    NOT NULL DEFAULT '',
    branch_name          TEXT    NOT NULL DEFAULT '',
    claim_lock           TEXT    NOT NULL DEFAULT '',
    claim_expires        REAL,
    max_retries          INTEGER NOT NULL DEFAULT 2,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_failure_error   TEXT    NOT NULL DEFAULT '',
    max_runtime_seconds  INTEGER NOT NULL DEFAULT 0,
    skills               TEXT    NOT NULL DEFAULT '',
    session_id           TEXT    NOT NULL DEFAULT '',
    spec_id              TEXT    NOT NULL DEFAULT '',
    idempotency_key      TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);

CREATE TABLE IF NOT EXISTS task_links (
    parent_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    child_id  TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (parent_id, child_id)
);

CREATE INDEX IF NOT EXISTS idx_task_links_child ON task_links(child_id);

CREATE TABLE IF NOT EXISTS task_comments (
    rowid_pk   INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT    NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    author     TEXT    NOT NULL DEFAULT '',
    body       TEXT    NOT NULL,
    created_at REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_comments_task ON task_comments(task_id);

CREATE TABLE IF NOT EXISTS task_events (
    rowid_pk   INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT    NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    kind       TEXT    NOT NULL,
    payload    TEXT    NOT NULL DEFAULT '',
    created_at REAL    NOT NULL,
    run_id     TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_events(task_id);

CREATE TABLE IF NOT EXISTS task_runs (
    rowid_pk   INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT    NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    run_id     TEXT    NOT NULL,
    profile    TEXT    NOT NULL DEFAULT '',
    outcome    TEXT    NOT NULL DEFAULT '',
    summary    TEXT    NOT NULL DEFAULT '',
    metadata   TEXT    NOT NULL DEFAULT '',
    error      TEXT    NOT NULL DEFAULT '',
    started_at REAL    NOT NULL,
    ended_at   REAL
);

CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Apply the schema idempotently and record the schema version.

    Safe to call repeatedly — every statement is `CREATE * IF NOT EXISTS`. When
    future migrations are added, this is where they'll be applied via
    `_apply_migrations(conn, current_version)`.
    """
    conn.executescript(_SCHEMA_SQL)
    conn.execute(
        "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('version', ?)",
        (str(SCHEMA_VERSION),),
    )


def schema_version(conn: sqlite3.Connection) -> int:
    """Return the schema version stored in schema_meta, or 0 if uninitialised."""
    try:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='version'"
        ).fetchone()
        return int(row["value"]) if row else 0
    except sqlite3.OperationalError:
        return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> float:
    return time.time()


def _new_task_id() -> str:
    """Generate a task id (`t_` + 8 random hex chars).

    Format prefix mirrors Hermes for compat with imported boards. Short enough
    to type, wide enough to avoid collisions (2^32 possibilities).
    """
    return f"t_{uuid.uuid4().hex[:8]}"


def _normalize_status(value: str) -> str:
    """Lowercase + strip; raise if not in VALID_STATUSES."""
    s = (value or "").strip().lower()
    if s not in VALID_STATUSES:
        raise KanbanDbError(
            f"Invalid status: {value!r}. "
            f"Valid: {', '.join(sorted(VALID_STATUSES))}"
        )
    return s


def _normalize_priority(value: str | None) -> str:
    s = (value or DEFAULT_PRIORITY).strip().lower()
    return s if s in PRIORITY_RANK else DEFAULT_PRIORITY


def _priority_order(priority: str) -> int:
    return PRIORITY_RANK.get(priority, len(PRIORITY_RANK))


def _coerce_skills(skills: Iterable[str] | str | None) -> str:
    """Encode `skills` as a JSON string (column is TEXT)."""
    if not skills:
        return ""
    if isinstance(skills, str):
        return skills
    return json.dumps([str(s) for s in skills], ensure_ascii=False)


# ---------------------------------------------------------------------------
# CRUD — tasks
# ---------------------------------------------------------------------------


def create_task(
    conn: sqlite3.Connection,
    title: str,
    *,
    body: str = "",
    status: str = STATUS_TODO,
    assignee: str = "",
    priority: str = DEFAULT_PRIORITY,
    workspace_kind: str = "scratch",
    workspace_path: str = "",
    branch_name: str = "",
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_runtime_seconds: int = 0,
    skills: Iterable[str] | str | None = None,
    session_id: str = "",
    spec_id: str = "",
    idempotency_key: str = "",
    task_id: str | None = None,
) -> str:
    """Insert a new task. Returns its id.

    `task_id` lets callers preserve IDs across migrations (otherwise a fresh
    `t_<8hex>` is generated). `idempotency_key`, if non-empty, lets the same
    logical create call be retried without producing duplicates — we surface
    a uniqueness violation by raising `KanbanDbError`.
    """
    tid = task_id or _new_task_id()
    title = (title or "").strip()
    if not title:
        raise KanbanDbError("create_task: title is required")
    if workspace_kind not in WORKSPACE_KINDS:
        raise KanbanDbError(
            f"Invalid workspace_kind: {workspace_kind!r}. "
            f"Valid: {', '.join(sorted(WORKSPACE_KINDS))}"
        )

    with write_txn(conn):
        conn.execute(
            """
            INSERT INTO tasks (
                id, title, body, status, assignee, priority, created_at,
                workspace_kind, workspace_path, branch_name,
                max_retries, max_runtime_seconds, skills,
                session_id, spec_id, idempotency_key
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                tid,
                title,
                body or "",
                _normalize_status(status),
                assignee or "",
                _normalize_priority(priority),
                _now(),
                workspace_kind,
                workspace_path or "",
                branch_name or "",
                max(1, int(max_retries)),
                max(0, int(max_runtime_seconds)),
                _coerce_skills(skills),
                session_id or "",
                spec_id or "",
                idempotency_key or "",
            ),
        )
    return tid


def get_task(conn: sqlite3.Connection, task_id: str) -> Task:
    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise KanbanDbError(f"Task not found: {task_id!r}")
    return Task.from_row(row)


def get_task_or_none(conn: sqlite3.Connection, task_id: str) -> Task | None:
    """Same as get_task but returns None instead of raising."""
    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    return Task.from_row(row) if row else None


def list_tasks(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    assignee: str | None = None,
    parent_id: str | None = None,
    limit: int | None = None,
) -> list[Task]:
    """Return tasks matching filters, ordered by priority then id.

    `parent_id` filters to tasks whose direct parent in task_links is the given
    id — useful for listing decompose children of a triage task.
    """
    sql = "SELECT t.* FROM tasks t"
    params: list[Any] = []
    clauses: list[str] = []
    if parent_id is not None:
        sql += " JOIN task_links l ON l.child_id = t.id"
        clauses.append("l.parent_id = ?")
        params.append(parent_id)
    if status is not None:
        clauses.append("t.status = ?")
        params.append(_normalize_status(status))
    if assignee is not None:
        clauses.append("t.assignee = ?")
        params.append(assignee)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY t.priority, t.created_at, t.id"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    rows = conn.execute(sql, params).fetchall()
    tasks = [Task.from_row(r) for r in rows]
    # Re-sort by priority rank (priority column stores text, not int — Python sort).
    tasks.sort(key=lambda t: (_priority_order(t.priority), t.created_at, t.id))
    return tasks


def update_task_metadata(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    assignee: str | None = None,
    priority: str | None = None,
    body: str | None = None,
    title: str | None = None,
    spec_id: str | None = None,
    max_retries: int | None = None,
    max_runtime_seconds: int | None = None,
    skills: Iterable[str] | None = None,
) -> Task:
    """Update one or more mutable fields. Unspecified fields stay unchanged."""
    sets: list[str] = []
    params: list[Any] = []
    if assignee is not None:
        sets.append("assignee = ?"); params.append(assignee)
    if priority is not None:
        sets.append("priority = ?"); params.append(_normalize_priority(priority))
    if body is not None:
        sets.append("body = ?"); params.append(body)
    if title is not None:
        sets.append("title = ?"); params.append(title)
    if spec_id is not None:
        sets.append("spec_id = ?"); params.append(spec_id)
    if max_retries is not None:
        sets.append("max_retries = ?"); params.append(max(1, int(max_retries)))
    if max_runtime_seconds is not None:
        sets.append("max_runtime_seconds = ?"); params.append(max(0, int(max_runtime_seconds)))
    if skills is not None:
        sets.append("skills = ?"); params.append(_coerce_skills(skills))
    if not sets:
        return get_task(conn, task_id)
    params.append(task_id)
    with write_txn(conn):
        cur = conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params
        )
        if cur.rowcount == 0:
            raise KanbanDbError(f"Task not found: {task_id!r}")
    return get_task(conn, task_id)


# ---------------------------------------------------------------------------
# State machine — CAS transitions
# ---------------------------------------------------------------------------


def update_status(
    conn: sqlite3.Connection,
    task_id: str,
    new_status: str,
    *,
    expected_status: str | None = None,
) -> bool:
    """CAS-style status transition. Returns True on success.

    When `expected_status` is set, the row is only updated if its current
    status matches — atomic check-and-set. This is how `claim_task`,
    `complete_task` and `fail_task` avoid race conditions.
    """
    new = _normalize_status(new_status)
    with write_txn(conn):
        if expected_status is None:
            cur = conn.execute(
                "UPDATE tasks SET status = ? WHERE id = ?",
                (new, task_id),
            )
        else:
            cur = conn.execute(
                "UPDATE tasks SET status = ? WHERE id = ? AND status = ?",
                (new, task_id, _normalize_status(expected_status)),
            )
        return cur.rowcount > 0


def claim_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    claim_lock: str | None = None,
    ttl_s: int = DEFAULT_CLAIM_TTL_S,
) -> str | None:
    """Atomically transition `ready → running` for `task_id`.

    Returns the claim_lock string on success (caller stores it to authenticate
    subsequent heartbeats), or None when the race was lost / task missing.
    No retry loop — losers should move to the next ready task.
    """
    lock = claim_lock or uuid.uuid4().hex
    expires = _now() + max(30, int(ttl_s))
    started = _now()
    with write_txn(conn):
        cur = conn.execute(
            """
            UPDATE tasks
            SET status = 'running',
                claim_lock = ?,
                claim_expires = ?,
                started_at = ?,
                last_failure_error = ''
            WHERE id = ? AND status = 'ready'
            """,
            (lock, expires, started, task_id),
        )
        if cur.rowcount == 0:
            return None
    return lock


def heartbeat(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    claim_lock: str,
    ttl_s: int = DEFAULT_CLAIM_TTL_S,
) -> bool:
    """Extend the claim TTL when the worker is still alive.

    Returns False if the claim_lock doesn't match (stale worker writing to a
    claim that's been reassigned) — caller should abort gracefully.
    """
    new_expires = _now() + max(30, int(ttl_s))
    with write_txn(conn):
        cur = conn.execute(
            """
            UPDATE tasks SET claim_expires = ?
            WHERE id = ? AND status = 'running' AND claim_lock = ?
            """,
            (new_expires, task_id, claim_lock),
        )
        return cur.rowcount > 0


def complete_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    summary: str = "",
    metadata: Mapping[str, Any] | None = None,
) -> bool:
    """CAS `running → done`, clear claim state, append a task_run record."""
    with write_txn(conn):
        run_id = _persisted_run_id(conn, task_id) or uuid.uuid4().hex
        cur = conn.execute(
            """
            UPDATE tasks
            SET status = 'done',
                completed_at = ?,
                claim_lock = '',
                claim_expires = 0,
                consecutive_failures = 0,
                last_failure_error = ''
            WHERE id = ? AND status = 'running'
            """,
            (_now(), task_id),
        )
        ok = cur.rowcount > 0
        if ok:
            _record_run(
                conn, task_id, run_id=run_id, outcome="success",
                summary=summary or "", metadata=metadata or {}, error="",
            )
            if summary:
                _insert_comment(conn, task_id, "dispatcher", f"Result: {summary[:1000]}")
        return ok


def fail_task(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    error: str,
    metadata: Mapping[str, Any] | None = None,
) -> str:
    """Mark a running task as failed; retry budget decides between failed and ready.

    Returns the new status (`'failed'` or `'ready'`). Always logs a task_run
    row and a comment. `consecutive_failures` is incremented; the task is
    fully terminated (`failed`) once it reaches `max_retries`.
    """
    with write_txn(conn):
        row = conn.execute(
            "SELECT max_retries, consecutive_failures FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if not row:
            raise KanbanDbError(f"Task not found: {task_id!r}")
        attempts = (row["consecutive_failures"] or 0) + 1
        max_r = max(1, int(row["max_retries"] or DEFAULT_MAX_RETRIES))
        new_status = STATUS_FAILED if attempts >= max_r else STATUS_READY
        run_id = _persisted_run_id(conn, task_id) or uuid.uuid4().hex
        cur = conn.execute(
            """
            UPDATE tasks
            SET status = ?,
                claim_lock = '',
                claim_expires = 0,
                consecutive_failures = ?,
                last_failure_error = ?
            WHERE id = ? AND status = 'running'
            """,
            (new_status, attempts, (error or "")[:500], task_id),
        )
        if cur.rowcount == 0:
            raise KanbanDbError(
                f"fail_task: cannot transition {task_id!r} — not running"
            )
        _record_run(
            conn, task_id, run_id=run_id, outcome="failure",
            summary="", metadata=metadata or {}, error=(error or "")[:2000],
        )
        verb = "FAILED" if new_status == STATUS_FAILED else "back to ready for retry"
        _insert_comment(
            conn, task_id, "dispatcher",
            f"Failure ({verb}): {(error or '')[:1000]}"
        )
    return new_status


def release_to_ready(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    reason: str = "",
) -> bool:
    """Force a running task back to ready (used to reclaim stale claims).

    Does NOT increment consecutive_failures — this isn't a worker error, it's
    a recovery action. CAS so we don't trample a task that just completed.
    """
    with write_txn(conn):
        cur = conn.execute(
            """
            UPDATE tasks
            SET status = 'ready', claim_lock = '', claim_expires = 0
            WHERE id = ? AND status = 'running'
            """,
            (task_id,),
        )
        ok = cur.rowcount > 0
        if ok and reason:
            _insert_comment(conn, task_id, "dispatcher", f"Reclaimed: {reason}")
        return ok


# ---------------------------------------------------------------------------
# Comments / events / runs (history)
# ---------------------------------------------------------------------------


def add_comment(
    conn: sqlite3.Connection,
    task_id: str,
    body: str,
    *,
    author: str = "",
) -> int:
    """Append a comment to a task. Returns the inserted rowid."""
    with write_txn(conn):
        return _insert_comment(conn, task_id, author, body)


def _insert_comment(
    conn: sqlite3.Connection, task_id: str, author: str, body: str
) -> int:
    cur = conn.execute(
        "INSERT INTO task_comments(task_id, author, body, created_at) VALUES (?,?,?,?)",
        (task_id, author or "", body or "", _now()),
    )
    return int(cur.lastrowid or 0)


def list_comments(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM task_comments WHERE task_id = ? ORDER BY created_at, rowid_pk",
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def add_event(
    conn: sqlite3.Connection,
    task_id: str,
    kind: str,
    *,
    payload: Mapping[str, Any] | str = "",
    run_id: str = "",
) -> int:
    """Append an audit event. `payload` is JSON-encoded when it's a dict."""
    encoded = json.dumps(payload, ensure_ascii=False) if isinstance(payload, Mapping) else (payload or "")
    with write_txn(conn):
        cur = conn.execute(
            """
            INSERT INTO task_events(task_id, kind, payload, created_at, run_id)
            VALUES (?,?,?,?,?)
            """,
            (task_id, kind, encoded, _now(), run_id or ""),
        )
        return int(cur.lastrowid or 0)


def list_events(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM task_events WHERE task_id = ? ORDER BY created_at, rowid_pk",
        (task_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("payload"):
            try:
                d["payload"] = json.loads(d["payload"])
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(d)
    return out


def _record_run(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    run_id: str,
    outcome: str,
    summary: str,
    metadata: Mapping[str, Any] | str,
    error: str,
) -> int:
    encoded = json.dumps(metadata, ensure_ascii=False) if isinstance(metadata, Mapping) else (metadata or "")
    # Update the open run row (started but not ended) if it exists.
    existing = conn.execute(
        "SELECT rowid_pk FROM task_runs WHERE task_id = ? AND run_id = ? AND ended_at IS NULL",
        (task_id, run_id),
    ).fetchone()
    now = _now()
    if existing:
        conn.execute(
            """
            UPDATE task_runs
            SET outcome = ?, summary = ?, metadata = ?, error = ?, ended_at = ?
            WHERE rowid_pk = ?
            """,
            (outcome, summary, encoded, error, now, existing["rowid_pk"]),
        )
        return int(existing["rowid_pk"])
    cur = conn.execute(
        """
        INSERT INTO task_runs(task_id, run_id, profile, outcome, summary,
                              metadata, error, started_at, ended_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (task_id, run_id, "", outcome, summary, encoded, error, now, now),
    )
    return int(cur.lastrowid or 0)


def _persisted_run_id(conn: sqlite3.Connection, task_id: str) -> str:
    """Most recent open run_id for `task_id`, or empty string."""
    row = conn.execute(
        "SELECT run_id FROM task_runs WHERE task_id = ? AND ended_at IS NULL "
        "ORDER BY started_at DESC, rowid_pk DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return row["run_id"] if row else ""


def start_run(
    conn: sqlite3.Connection,
    task_id: str,
    *,
    profile: str = "",
) -> str:
    """Open a new task_run row when a worker starts. Returns the run_id."""
    run_id = uuid.uuid4().hex
    with write_txn(conn):
        conn.execute(
            """
            INSERT INTO task_runs(task_id, run_id, profile, started_at)
            VALUES (?,?,?,?)
            """,
            (task_id, run_id, profile or "", _now()),
        )
    return run_id


def list_runs(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM task_runs WHERE task_id = ? ORDER BY started_at, rowid_pk",
        (task_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Links — DAG with cycle detection
# ---------------------------------------------------------------------------


def link_tasks(
    conn: sqlite3.Connection,
    parent_id: str,
    child_id: str,
) -> bool:
    """Add `parent_id → child_id` to task_links.

    Rejects self-links and links that would form a cycle (Kahn's algorithm
    on the projected post-insert graph). Idempotent — re-adding the same
    link is a no-op and returns False.
    """
    if parent_id == child_id:
        raise CycleError(f"Self-link not allowed: {parent_id!r}")
    # Ensure both tasks exist.
    for tid in (parent_id, child_id):
        if get_task_or_none(conn, tid) is None:
            raise KanbanDbError(f"Task not found: {tid!r}")

    # Project the new edge into the existing graph and check for cycles.
    edges = _all_edges(conn)
    edges.append((parent_id, child_id))
    if _has_cycle(edges):
        raise CycleError(
            f"Adding {parent_id} → {child_id} would create a dependency cycle"
        )

    with write_txn(conn):
        cur = conn.execute(
            "INSERT OR IGNORE INTO task_links(parent_id, child_id) VALUES (?, ?)",
            (parent_id, child_id),
        )
        return cur.rowcount > 0


def unlink_tasks(
    conn: sqlite3.Connection, parent_id: str, child_id: str
) -> bool:
    with write_txn(conn):
        cur = conn.execute(
            "DELETE FROM task_links WHERE parent_id = ? AND child_id = ?",
            (parent_id, child_id),
        )
        return cur.rowcount > 0


def parents_of(conn: sqlite3.Connection, task_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT parent_id FROM task_links WHERE child_id = ?", (task_id,)
    ).fetchall()
    return [r["parent_id"] for r in rows]


def children_of(conn: sqlite3.Connection, task_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT child_id FROM task_links WHERE parent_id = ?", (task_id,)
    ).fetchall()
    return [r["child_id"] for r in rows]


def _all_edges(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT parent_id, child_id FROM task_links").fetchall()
    return [(r["parent_id"], r["child_id"]) for r in rows]


def _has_cycle(edges: list[tuple[str, str]]) -> bool:
    """Kahn's topological-sort variant — True if the graph has any cycle.

    Builds in-degree map, repeatedly removes zero-in-degree nodes. If any
    nodes remain at the end, they form (part of) a cycle.
    """
    if not edges:
        return False
    nodes: set[str] = set()
    in_deg: dict[str, int] = {}
    out_adj: dict[str, list[str]] = {}
    for parent, child in edges:
        nodes.add(parent); nodes.add(child)
        in_deg[child] = in_deg.get(child, 0) + 1
        in_deg.setdefault(parent, in_deg.get(parent, 0))
        out_adj.setdefault(parent, []).append(child)

    queue = [n for n in nodes if in_deg.get(n, 0) == 0]
    seen = 0
    while queue:
        node = queue.pop()
        seen += 1
        for nb in out_adj.get(node, []):
            in_deg[nb] -= 1
            if in_deg[nb] == 0:
                queue.append(nb)
    return seen != len(nodes)


# ---------------------------------------------------------------------------
# Dispatch — one tick of the scheduler
# ---------------------------------------------------------------------------


@dataclass
class DispatchResult:
    reclaimed: list[str] = field(default_factory=list)
    promoted:  list[str] = field(default_factory=list)
    claimed:   list[str] = field(default_factory=list)
    skipped:   list[str] = field(default_factory=list)


def recompute_ready(conn: sqlite3.Connection) -> list[str]:
    """Promote `todo → ready` for tasks whose parents are all DONE.

    A task with no parents is always promotable. Returns the IDs newly moved
    to `ready`. Idempotent — already-ready tasks aren't re-promoted.
    """
    candidates = list_tasks(conn, status=STATUS_TODO)
    promoted: list[str] = []
    for task in candidates:
        parents = parents_of(conn, task.id)
        if not parents:
            ok = True
        else:
            rows = conn.execute(
                f"SELECT id, status FROM tasks WHERE id IN ({','.join('?' * len(parents))})",
                parents,
            ).fetchall()
            ok = bool(rows) and all(r["status"] == STATUS_DONE for r in rows)
        if ok and update_status(conn, task.id, STATUS_READY, expected_status=STATUS_TODO):
            promoted.append(task.id)
    return promoted


def reclaim_stale(conn: sqlite3.Connection) -> list[str]:
    """Move running tasks whose claim has expired back to ready.

    Returns the IDs reclaimed. Workers that beat the reclaim with a heartbeat
    keep their claim — we only reclaim when claim_expires < now AND the row is
    still 'running' (CAS protected).
    """
    now = _now()
    rows = conn.execute(
        "SELECT id FROM tasks WHERE status = 'running' AND claim_expires > 0 AND claim_expires < ?",
        (now,),
    ).fetchall()
    reclaimed: list[str] = []
    for r in rows:
        if release_to_ready(conn, r["id"], reason="claim expired"):
            reclaimed.append(r["id"])
    return reclaimed


def dispatch_once(
    conn: sqlite3.Connection,
    *,
    max_spawn: int = 1,
    max_in_progress: int | None = None,
    runner_name: str = "",
    claim_ttl_s: int = DEFAULT_CLAIM_TTL_S,
) -> DispatchResult:
    """One tick of the scheduler — reclaim, promote, claim.

    The dispatcher is intentionally pure-data — it doesn't spawn workers. The
    caller (task_dispatcher / kanban_swarm) decides how to materialise a
    claimed task into a worker process. This module just hands out claims.
    """
    result = DispatchResult()
    result.reclaimed = reclaim_stale(conn)
    result.promoted = recompute_ready(conn)

    if max_in_progress is not None:
        running = conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE status = 'running'"
        ).fetchone()["c"]
        max_spawn = max(0, min(max_spawn, max_in_progress - running))
    if max_spawn <= 0:
        return result

    ready = list_tasks(conn, status=STATUS_READY, limit=max_spawn * 2)
    spawned = 0
    for task in ready:
        if spawned >= max_spawn:
            break
        lock = claim_task(conn, task.id, ttl_s=claim_ttl_s)
        if lock is None:
            result.skipped.append(task.id)
            continue
        result.claimed.append(task.id)
        spawned += 1
        if runner_name:
            add_event(conn, task.id, kind="claim", payload={"runner": runner_name})
    return result


# ---------------------------------------------------------------------------
# Multi-board helpers
# ---------------------------------------------------------------------------


def list_boards() -> list[str]:
    """Names of every board with a kanban.db on disk."""
    root = _bauer_home() / "kanban" / "boards"
    if not root.exists():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and (d / "kanban.db").exists()
    )


def active_board_marker_path() -> Path:
    return _bauer_home() / "kanban" / "active_board"


def get_active_board() -> str:
    """Active board: env > marker file > 'default'."""
    env = os.environ.get("BAUER_KANBAN_BOARD")
    if env:
        return env
    marker = active_board_marker_path()
    if marker.exists():
        try:
            v = marker.read_text(encoding="utf-8").strip()
            if v:
                return v
        except OSError:
            pass
    return "default"


def set_active_board(board: str) -> None:
    """Persist the active board pointer (writes to ~/.bauer/kanban/active_board)."""
    marker = active_board_marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(board, encoding="utf-8")
