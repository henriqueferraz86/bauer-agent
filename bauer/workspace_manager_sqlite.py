"""SQLite-backed alternative to `WorkspaceManager`.

Drop-in replacement for `bauer.workspace_manager.WorkspaceManager` whose tasks
live in the `bauer.kanban_db` SQLite kernel instead of `workspace/TASKS.md`.

Key differences from the markdown backend:
    - Atomic CAS on every status change (no race conditions when two workers
      try to claim the same task)
    - Tasks survive across workspace clones / disk moves (DB is at
      ``~/.bauer/kanban/boards/<slug>/kanban.db``)
    - Multi-board isolation: each project can pin its own board via
      `WorkspaceManagerSqlite(board="alpha")` or `BAUER_KANBAN_BOARD` env var
    - TASKS.md becomes a *generated view* — human-readable snapshot regenerated
      after every mutating call. Editing it by hand is now a no-op (changes
      will be overwritten on the next write).

Public API matches `WorkspaceManager` 1:1:
    init_project, add_task, list_tasks, update_task_status, update_task_metadata,
    add_task_comment, get_task, get_project_info

Status normalisation:
    API exposes UPPERCASE statuses (TODO, READY, IN_PROGRESS, DONE, BLOCKED,
    FAILED) for back-compat with the rest of the codebase. Storage uses
    lowercase (todo, ready, running, done, blocked, failed) per Hermes/kanban_db
    convention. Translation happens at every entry/exit boundary.

ID format:
    Numeric zero-padded ("001", "002", ...) preserved — the kanban_db
    `task_id` parameter accepts any string, so we keep WorkspaceManager's
    sequential numbering. New IDs are generated via `MAX(CAST(id AS INTEGER))`
    + 1; non-numeric IDs (e.g. from a migrated swarm) are silently ignored.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import kanban_db as kb
from .workspace_manager import Task, WorkspaceError, _normalize_task_id


# ---------------------------------------------------------------------------
# Status normalisation tables
# ---------------------------------------------------------------------------

# API (UPPERCASE) → DB (lowercase).
_STATUS_TO_DB: dict[str, str] = {
    "TODO":        kb.STATUS_TODO,
    "READY":       kb.STATUS_READY,
    "IN_PROGRESS": kb.STATUS_RUNNING,
    "DONE":        kb.STATUS_DONE,
    "BLOCKED":     kb.STATUS_BLOCKED,
    "FAILED":      kb.STATUS_FAILED,
}

# Inverse — DB → API. Includes Hermes-only statuses (triage/review/archived)
# that have no UPPERCASE equivalent; mapped to the closest WorkspaceManager
# status so list_tasks() never returns something the caller can't render.
_STATUS_FROM_DB: dict[str, str] = {
    kb.STATUS_TRIAGE:   "TODO",     # triage maps to TODO until specify lifts it
    kb.STATUS_TODO:     "TODO",
    kb.STATUS_READY:    "READY",
    kb.STATUS_RUNNING:  "IN_PROGRESS",
    kb.STATUS_REVIEW:   "IN_PROGRESS",
    kb.STATUS_BLOCKED:  "BLOCKED",
    kb.STATUS_DONE:     "DONE",
    kb.STATUS_ARCHIVED: "DONE",
    kb.STATUS_FAILED:   "FAILED",
}

_VALID_API_STATUSES = frozenset(_STATUS_TO_DB.keys())


def _status_to_db(value: str) -> str:
    s = (value or "").strip().upper()
    if s not in _STATUS_TO_DB:
        raise WorkspaceError(
            f"Status invalido: '{value}'. "
            f"Validos: {', '.join(sorted(_VALID_API_STATUSES))}"
        )
    return _STATUS_TO_DB[s]


def _status_from_db(value: str) -> str:
    return _STATUS_FROM_DB.get((value or "").strip().lower(), "TODO")


# ---------------------------------------------------------------------------
# WorkspaceManagerSqlite
# ---------------------------------------------------------------------------


class WorkspaceManagerSqlite:
    """Task storage backed by kanban_db. Same public API as WorkspaceManager."""

    def __init__(
        self,
        workspace: str | Path = "workspace",
        *,
        board: str | None = None,
        regenerate_view: bool = True,
    ):
        """
        Args:
            workspace: directory to anchor PROJECT.md / TASKS.md view.
            board: kanban_db board name. None → uses the active board pointer
                (env var BAUER_KANBAN_BOARD, marker file, or "default").
            regenerate_view: when True (default), TASKS.md is regenerated as a
                human-readable snapshot after every mutating call. Set False
                in hot-path code that doesn't need the file (e.g. dispatcher
                background workers) to save IO.
        """
        self.workspace = Path(workspace).resolve()
        self.tasks_file = self.workspace / "TASKS.md"
        self.project_file = self.workspace / "PROJECT.md"
        self._board = board
        self._regenerate_view = regenerate_view

    # --- internal helpers ---------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a kanban_db connection for this manager's board.

        Connections aren't pooled — each call opens fresh. SQLite's
        in-memory cost is negligible, and stateless connections avoid
        thread-affinity issues when shared with the dispatcher.
        """
        # The context manager handles cleanup; here we open a raw connection
        # because most methods want to use it across multiple statements
        # within their own transaction boundaries. Caller closes via try/
        # finally — see the public methods below.
        conn = sqlite3.connect(str(kb.board_path(self._board)), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Apply schema if missing. Idempotent."""
        if kb.schema_version(conn) < kb.SCHEMA_VERSION:
            kb.init_db(conn)

    def _next_task_id(self, conn: sqlite3.Connection) -> str:
        """Generate the next sequential numeric ID (zero-padded to 3 digits)."""
        # CAST() to INTEGER ignores rows whose id isn't pure digits (e.g.
        # swarm tasks like "t_abc123" or migrated tasks). MAX returns NULL on
        # an empty table — COALESCE handles that.
        row = conn.execute(
            """
            SELECT COALESCE(MAX(CAST(id AS INTEGER)), 0) AS m
            FROM tasks
            WHERE id GLOB '[0-9]*'
            """
        ).fetchone()
        return str((row["m"] or 0) + 1).zfill(3)

    def _to_task(self, conn: sqlite3.Connection, db_task: kb.Task) -> Task:
        """Convert a kanban_db Task into a workspace_manager.Task instance."""
        parents = kb.parents_of(conn, db_task.id)
        parent_id = parents[0] if parents else ""
        # Pull comments — each row already has author/created_at/body.
        comments_rows = kb.list_comments(conn, db_task.id)
        comments: list[dict[str, str]] = []
        for c in comments_rows:
            at_iso = _epoch_to_iso(c.get("created_at") or 0)
            comments.append({
                "at": at_iso,
                "author": c.get("author", ""),
                "text": c.get("body", ""),
            })
        # Rebuild the metadata dict that the markdown backend exposes. Most of
        # its keys come from task_events (dispatcher state, automation, etc.)
        # but the most-recent ones win — events are append-only, we keep last.
        metadata: dict[str, str] = {}
        for event in kb.list_events(conn, db_task.id):
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            for key, value in payload.items():
                metadata[str(key).strip().lower()] = "" if value is None else str(value)

        # Hot dispatcher fields live in dedicated columns; surface them under
        # the same metadata keys that the markdown backend used so callers
        # don't notice the move.
        if db_task.claim_lock:
            metadata.setdefault("claim_id", db_task.claim_lock)
        if db_task.claim_expires:
            metadata.setdefault("claim_expires", str(int(db_task.claim_expires)))
        if db_task.consecutive_failures:
            metadata.setdefault("attempts", str(db_task.consecutive_failures))
        if db_task.max_retries:
            metadata.setdefault("max_retries", str(db_task.max_retries))
        if db_task.last_failure_error:
            metadata.setdefault("last_error", db_task.last_failure_error)

        return Task(
            id=db_task.id,
            status=_status_from_db(db_task.status),
            title=db_task.title,
            description=db_task.body,
            spec_id=db_task.spec_id,
            priority=db_task.priority,
            assignee=db_task.assignee,
            parent_id=parent_id,
            created_at=_epoch_to_iso(db_task.created_at)[:10],   # YYYY-MM-DD
            comments=comments,
            metadata=metadata,
        )

    # --- init_project -------------------------------------------------------

    def init_project(self, name: str, description: str = "") -> list[Path]:
        """Initialise workspace + SQLite board + optional PROJECT.md."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        created: list[Path] = []

        conn = self._connect()
        try:
            self._ensure_schema(conn)
        finally:
            conn.close()

        if not self.project_file.exists():
            ts = _today()
            self.project_file.write_text(
                f"# Projeto: {name}\n\n"
                f"criado: {ts}\n\n"
                f"## Descricao\n\n{description.strip() or 'Sem descricao.'}\n\n---\n",
                encoding="utf-8",
            )
            created.append(self.project_file)

        if not self.tasks_file.exists():
            self._write_view([])
            created.append(self.tasks_file)

        return created

    # --- add_task -----------------------------------------------------------

    def add_task(
        self,
        title: str,
        description: str = "",
        spec_id: str = "",
        status: str = "TODO",
        priority: str = "medium",
        assignee: str = "",
        parent_id: str = "",
        metadata: dict[str, str | int | None] | None = None,
    ) -> Task:
        """Insert a new task. ID is numeric, zero-padded, sequential per board."""
        title = (title or "").strip()
        if not title:
            raise WorkspaceError("title vazio.")
        db_status = _status_to_db(status)

        conn = self._connect()
        try:
            self._ensure_schema(conn)
            task_id = self._next_task_id(conn)

            kb.create_task(
                conn,
                title,
                body=description or "",
                status=db_status,
                assignee=assignee.strip(),
                priority=(priority or "medium").strip().lower(),
                spec_id=spec_id.strip(),
                task_id=task_id,
            )

            # Parent link (only if both ends exist).
            if parent_id and parent_id.strip():
                parent_norm = _normalize_task_id(parent_id)
                if kb.get_task_or_none(conn, parent_norm) is not None:
                    try:
                        kb.link_tasks(conn, parent_norm, task_id)
                    except (kb.CycleError, kb.KanbanDbError):
                        # Don't fail the task creation on a bad link — surface
                        # it via an event so the caller can react.
                        kb.add_event(conn, task_id, kind="parent_link_failed",
                                     payload={"parent": parent_norm})

            # Free-form metadata becomes a single event so it survives without
            # bloating the columns. The dispatcher reads these via list_events().
            if metadata:
                clean: dict[str, str] = {}
                for key, value in metadata.items():
                    if value is None:
                        continue
                    clean[str(key).strip().lower()] = _single_line(str(value))
                if clean:
                    kb.add_event(conn, task_id, kind="metadata_set", payload=clean)

            kb.add_event(
                conn, task_id, kind="task.created",
                payload={
                    "title": title,
                    "priority": (priority or "medium").lower(),
                    "assignee": assignee.strip(),
                },
            )
        finally:
            conn.close()

        if self._regenerate_view:
            self._regenerate_tasks_md()
        return self.get_task(task_id)

    # --- list_tasks ---------------------------------------------------------

    def list_tasks(self) -> list[Task]:
        conn = self._connect()
        try:
            self._ensure_schema(conn)
            db_tasks = kb.list_tasks(conn)
            return [self._to_task(conn, t) for t in db_tasks]
        finally:
            conn.close()

    # --- get_task -----------------------------------------------------------

    def get_task(self, task_id: str) -> Task:
        task_id = _normalize_task_id(task_id)
        conn = self._connect()
        try:
            db_task = kb.get_task_or_none(conn, task_id)
            if db_task is None:
                raise WorkspaceError(f"Tarefa '{task_id}' nao encontrada.")
            return self._to_task(conn, db_task)
        finally:
            conn.close()

    # --- update_task_status -------------------------------------------------

    def update_task_status(self, task_id: str, new_status: str) -> Task:
        task_id = _normalize_task_id(task_id)
        db_status = _status_to_db(new_status)
        conn = self._connect()
        try:
            if not kb.update_status(conn, task_id, db_status):
                # Either task missing or row didn't change — verify and surface
                # the right error.
                if kb.get_task_or_none(conn, task_id) is None:
                    raise WorkspaceError(f"Tarefa '{task_id}' nao encontrada.")
                # Same status already — treat as no-op (consistent with
                # markdown backend, which never rejected re-asserts).
            kb.add_event(
                conn, task_id, kind="task.status_changed",
                payload={"status_to": db_status},
            )
        finally:
            conn.close()
        if self._regenerate_view:
            self._regenerate_tasks_md()
        return self.get_task(task_id)

    # --- update_task_metadata ----------------------------------------------

    def update_task_metadata(
        self,
        task_id: str,
        *,
        priority: str | None = None,
        assignee: str | None = None,
        parent_id: str | None = None,
        metadata: dict[str, str | int | None] | None = None,
    ) -> Task:
        task_id = _normalize_task_id(task_id)
        conn = self._connect()
        try:
            if kb.get_task_or_none(conn, task_id) is None:
                raise WorkspaceError(f"Tarefa '{task_id}' nao encontrada.")
            kb.update_task_metadata(
                conn, task_id,
                priority=priority,
                assignee=assignee,
            )
            if parent_id is not None:
                # Drop any prior parents, install the new one (mirrors the
                # markdown backend's "single parent" assumption).
                for existing in kb.parents_of(conn, task_id):
                    kb.unlink_tasks(conn, existing, task_id)
                new_parent = (parent_id or "").strip()
                if new_parent:
                    parent_norm = _normalize_task_id(new_parent)
                    if kb.get_task_or_none(conn, parent_norm) is not None:
                        try:
                            kb.link_tasks(conn, parent_norm, task_id)
                        except (kb.CycleError, kb.KanbanDbError):
                            kb.add_event(conn, task_id, kind="parent_link_failed",
                                         payload={"parent": parent_norm})
            if metadata:
                clean: dict[str, str] = {}
                for key, value in metadata.items():
                    if value is None:
                        # Markdown deletion semantics: None means "remove key".
                        # We record the deletion as a separate event so the
                        # most-recent value wins in _to_task().
                        clean[str(key).strip().lower()] = ""
                    else:
                        clean[str(key).strip().lower()] = _single_line(str(value))
                kb.add_event(conn, task_id, kind="metadata_set", payload=clean)
            kb.add_event(conn, task_id, kind="task.metadata_updated", payload={})
        finally:
            conn.close()
        if self._regenerate_view:
            self._regenerate_tasks_md()
        return self.get_task(task_id)

    # --- add_task_comment ---------------------------------------------------

    def add_task_comment(self, task_id: str, text: str, author: str = "agent") -> Task:
        task_id = _normalize_task_id(task_id)
        comment = _single_line(text)
        if not comment:
            raise WorkspaceError("Comentario vazio.")
        author = _single_line(author or "agent")

        conn = self._connect()
        try:
            if kb.get_task_or_none(conn, task_id) is None:
                raise WorkspaceError(f"Tarefa '{task_id}' nao encontrada.")
            kb.add_comment(conn, task_id, comment, author=author)
            kb.add_event(
                conn, task_id, kind="task.commented",
                payload={"author": author, "text": comment[:200]},
            )
        finally:
            conn.close()
        if self._regenerate_view:
            self._regenerate_tasks_md()
        return self.get_task(task_id)

    # --- get_project_info ---------------------------------------------------

    def get_project_info(self) -> str:
        if not self.project_file.exists():
            return "[PROJECT.md nao encontrado — rode: bauer project init]"
        return self.project_file.read_text(encoding="utf-8")

    # --- view regeneration --------------------------------------------------

    def _regenerate_tasks_md(self) -> None:
        """Rewrite TASKS.md as a snapshot of the current SQLite state."""
        try:
            tasks = self.list_tasks()
            self._write_view(tasks)
        except OSError:
            # View generation must never break a write — silent best-effort.
            pass

    def _write_view(self, tasks: list[Task]) -> None:
        """Write the TASKS.md snapshot file. Best-effort; never raises."""
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
            lines: list[str] = [
                "# TASKS.md — Tarefas do projeto (gerado a partir de kanban.db)",
                "",
                "Status validos: TODO | READY | IN_PROGRESS | DONE | BLOCKED | FAILED",
                "",
                "*ATENCAO: este arquivo e regenerado automaticamente a partir do",
                "SQLite store em ~/.bauer/kanban. Edicoes manuais sao perdidas",
                "na proxima escrita.*",
                "",
                "---",
            ]
            for t in tasks:
                lines.append("")
                lines.append(f"## [{t.status}] {t.title}")
                lines.append(f"id: {t.id}")
                if t.created_at:
                    lines.append(f"criado: {t.created_at}")
                if t.priority:
                    lines.append(f"priority: {t.priority}")
                if t.assignee:
                    lines.append(f"assignee: {t.assignee}")
                if t.parent_id:
                    lines.append(f"parent: {t.parent_id}")
                if t.spec_id:
                    lines.append(f"spec: {t.spec_id}")
                # Surface the most-useful metadata in the view (dispatcher
                # state); skip noisy internal keys.
                for k in ("claim_id", "attempts", "last_error"):
                    if k in t.metadata and t.metadata[k]:
                        lines.append(f"{k}: {t.metadata[k]}")
                if t.description.strip():
                    lines.append("")
                    lines.append(t.description.strip())
                for c in t.comments:
                    lines.append(f"comment: {c.get('at','')} | {c.get('author','')} | {c.get('text','')}")
                lines.append("")
                lines.append("---")
            self.tasks_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _epoch_to_iso(epoch: float) -> str:
    """Convert a unix timestamp to ISO 8601 (UTC). Returns '' for 0/None."""
    if not epoch:
        return ""
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).replace(microsecond=0).isoformat()
    except (ValueError, OSError):
        return ""


def _single_line(value: str) -> str:
    """Collapse whitespace into single spaces and trim."""
    return re.sub(r"\s+", " ", str(value)).strip()
