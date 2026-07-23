"""Migrate legacy TASKS.md → kanban_db SQLite store.

Parses the markdown-based task list used by `bauer.workspace_manager` and
writes each task (plus comments and metadata) into the SQLite kernel from
`bauer.kanban_db`. The migration is idempotent — running it twice on the
same source produces the same destination state.

Strategy:
  - Task IDs preserved verbatim ("001", "002", ...) via `kanban_db.create_task(
    ..., task_id="001")`. Future references in source code or scripts keep
    working.
  - Status names mapped UPPERCASE → lowercase (TODO → todo, IN_PROGRESS →
    running, ...). FAILED keeps the same semantics.
  - Comments: each Markdown bullet under a task is inserted as a `task_comments`
    row with the original timestamp if present.
  - Metadata: priority/assignee/parent become first-class columns; remaining
    keys (claim_id, claim_expires, run_id, etc.) become `task_events` of kind
    `legacy_metadata` so the old dispatcher state isn't lost.
  - Parent/child links: `parent: <id>` metadata becomes a row in `task_links`.

Public surface:
  migrate_tasks_md(tasks_md_path, board=None) → MigrationReport
  read_tasks_md(path) → list[ParsedTask]   (parser; useful for tests)

CLI integration ships in a follow-up commit: `bauer kanban migrate [--board X]`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import kanban_db as kb


# Maps WorkspaceManager statuses → kanban_db statuses.
# Both schemas have FAILED; the rest are renamed to match Hermes conventions.
_STATUS_MAP: dict[str, str] = {
    "TODO":        kb.STATUS_TODO,
    "READY":       kb.STATUS_READY,
    "IN_PROGRESS": kb.STATUS_RUNNING,
    "DONE":        kb.STATUS_DONE,
    "BLOCKED":     kb.STATUS_BLOCKED,
    "FAILED":      kb.STATUS_FAILED,
}

# Reuse the same regexes as workspace_manager — they're the source of truth.
_HEADING_RE = re.compile(r"^## \[([A-Z_]+)\] (.+)$")
_ID_RE = re.compile(r"^id:\s*(\S+)\s*$")
_METADATA_RE = re.compile(r"^([a-z_][a-z0-9_]*)\s*:\s*(.+)$")
_COMMENT_BULLET_RE = re.compile(r"^[\-\*]\s+(.*)$")
# Linha de comentário emitida por WorkspaceManager.add_task_comment:
#   "comment: <iso> | <author> | <texto>"  (o writer REAL — não usa bullets).
_COMMENT_LINE_RE = re.compile(r"^comment:\s*(.+)$")


# ---------------------------------------------------------------------------
# Parser — TASKS.md → ParsedTask records
# ---------------------------------------------------------------------------


@dataclass
class ParsedTask:
    """One task parsed out of TASKS.md, ready to write to SQLite."""
    id: str
    status: str               # Original UPPERCASE status (TODO, ...)
    title: str
    description: str = ""
    created_at: str = ""      # ISO 8601 string from `criado:`
    metadata: dict[str, str] = field(default_factory=dict)
    comments: list[str] = field(default_factory=list)


def read_tasks_md(path: Path | str) -> list[ParsedTask]:
    """Parse a TASKS.md file into a list of ParsedTask. Empty list if missing.

    The format mirrors what `workspace_manager.WorkspaceManager.add_task()`
    writes:

        ## [TODO] Task title
        id: 001
        criado: 2026-05-31
        priority: high
        spec: auth-v2

        Description paragraph (optional).

        - comment one
        - comment two

        ---

    The parser is tolerant: unknown metadata keys go into `metadata`, blank
    lines separate description from comments, and the `---` divider is
    optional (lines after a heading until the next heading belong to the
    current task).
    """
    p = Path(path)
    if not p.exists():
        return []

    raw = p.read_text(encoding="utf-8", errors="replace")
    tasks: list[ParsedTask] = []
    current: ParsedTask | None = None
    in_metadata_block = True   # right after heading we expect key: value pairs

    for raw_line in raw.splitlines():
        line = raw_line.rstrip()

        # Section divider — close out the current task.
        if line.strip() == "---":
            if current and current.id:
                tasks.append(current)
            current = None
            continue

        # New task heading.
        head = _HEADING_RE.match(line.strip())
        if head:
            if current and current.id:
                tasks.append(current)
            status, title = head.group(1), head.group(2).strip()
            current = ParsedTask(id="", status=status, title=title)
            in_metadata_block = True
            continue

        if current is None:
            # Lines before the first heading are file header — ignore.
            continue

        # Blank line ends the metadata block, starts the description / comments.
        if not line.strip():
            in_metadata_block = False
            continue

        # Comentário no formato que o WorkspaceManager REAL escreve, em
        # qualquer região do bloco: "comment: <iso> | <author> | <texto>".
        # Precisa vir ANTES do bloco de metadata — senão _METADATA_RE captura
        # 'comment' como chave; e ANTES da prosa — senão vaza para description
        # (era o bug #10-A). Preserva só o <texto> (split maxsplit=2 mantém '|'
        # que exista no próprio texto).
        comment_line = _COMMENT_LINE_RE.match(line.strip())
        if comment_line:
            segs = comment_line.group(1).split("|", 2)
            text = (segs[2] if len(segs) == 3 else segs[-1]).strip()
            if text:
                current.comments.append(text)
            in_metadata_block = False
            continue

        if in_metadata_block:
            id_match = _ID_RE.match(line)
            if id_match:
                current.id = id_match.group(1).strip()
                continue
            meta_match = _METADATA_RE.match(line)
            if meta_match:
                key, value = meta_match.group(1).strip(), meta_match.group(2).strip()
                if key == "criado":
                    current.created_at = value
                elif key == "spec":
                    # Map workspace_manager's `spec:` to kanban_db's spec_id col.
                    current.metadata["spec_id"] = value
                elif key == "parent":
                    current.metadata["parent"] = value
                else:
                    current.metadata[key] = value
                continue
            # Non-metadata line inside the metadata block — treat as start of
            # description (fall through).
            in_metadata_block = False

        # Description or comment region.
        bullet = _COMMENT_BULLET_RE.match(line.strip())
        if bullet:
            current.comments.append(bullet.group(1).strip())
            continue

        # Plain prose — accumulate as description.
        if current.description:
            current.description += "\n" + line
        else:
            current.description = line

    # Flush trailing task without a closing `---` divider.
    if current and current.id:
        tasks.append(current)

    # Normalise: trim trailing whitespace from descriptions and drop comments
    # that ended up empty.
    for t in tasks:
        t.description = t.description.strip()
        t.comments = [c for c in t.comments if c.strip()]

    return tasks


# ---------------------------------------------------------------------------
# Writer — ParsedTask → kanban_db
# ---------------------------------------------------------------------------


@dataclass
class MigrationReport:
    inserted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)    # task already present
    links: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.inserted) + len(self.skipped) + len(self.errors)

    def summary(self) -> str:
        return (
            f"{len(self.inserted)} inserted, {len(self.skipped)} already present, "
            f"{len(self.links)} parent/child links, {len(self.errors)} errors"
        )


def migrate_tasks_md(
    tasks_md_path: Path | str,
    *,
    board: str | None = None,
) -> MigrationReport:
    """Read `tasks_md_path` and write everything into the kanban_db board.

    Idempotent: tasks already present in the DB (matched by id) are skipped,
    not duplicated or overwritten. Links and comments are also dedup-friendly
    (links via INSERT OR IGNORE; comments are append-only — re-running the
    migration adds them again, so the caller should only do that knowingly).

    Args:
        tasks_md_path: Path to a TASKS.md file.
        board: kanban_db board name. None → active board.

    Returns:
        MigrationReport with counts and per-task error messages.
    """
    parsed = read_tasks_md(tasks_md_path)
    report = MigrationReport()
    if not parsed:
        return report

    with kb.connect(board) as conn:
        kb.init_db(conn)

        # Pass 1 — insert tasks. Skip those already in the DB.
        for task in parsed:
            if not task.id or not task.title:
                report.errors.append((task.id or "(no-id)", "missing id or title"))
                continue
            if kb.get_task_or_none(conn, task.id) is not None:
                report.skipped.append(task.id)
                continue
            try:
                _insert_one(conn, task)
                report.inserted.append(task.id)
            except kb.KanbanDbError as exc:
                report.errors.append((task.id, str(exc)))

        # Pass 2 — link parent/child relationships. Done in a second pass so
        # parents always exist by the time we link to them (parser order may
        # not match dependency order).
        for task in parsed:
            parent = task.metadata.get("parent", "").strip()
            if not parent:
                continue
            if parent == task.id:
                continue
            if kb.get_task_or_none(conn, parent) is None:
                report.errors.append((task.id, f"parent {parent!r} not found"))
                continue
            try:
                if kb.link_tasks(conn, parent, task.id):
                    report.links.append((parent, task.id))
            except (kb.CycleError, kb.KanbanDbError) as exc:
                report.errors.append((task.id, f"link failed: {exc}"))

    return report


def _insert_one(conn, task: ParsedTask) -> None:
    """Write a single ParsedTask into kanban_db tables.

    Splits the work:
      1. Map status (UPPERCASE → lowercase) with a safe default.
      2. Pull `priority`, `assignee`, `spec_id` out of metadata into columns.
      3. Convert `criado` into `created_at` (best-effort ISO parse).
      4. Bulk-insert remaining metadata as `legacy_metadata` events.
      5. Insert each comment as a task_comments row.
    """
    status = _STATUS_MAP.get(task.status, kb.STATUS_TODO)
    metadata = dict(task.metadata)

    priority = metadata.pop("priority", "medium")
    assignee = metadata.pop("assignee", "")
    spec_id = metadata.pop("spec_id", "")
    metadata.pop("parent", None)   # handled in pass 2 above

    # `criado` field can be "2026-05-31" or full ISO; we don't override the
    # default created_at on the row (now()) but record the original date as
    # a comment so the timeline isn't lost.
    legacy_created = task.created_at

    kb.create_task(
        conn,
        task.title,
        body=task.description,
        status=status,
        assignee=assignee,
        priority=priority,
        spec_id=spec_id,
        task_id=task.id,
    )

    # Surface remaining metadata as a single audit event — keeps the legacy
    # dispatcher state queryable without polluting columns.
    if metadata:
        kb.add_event(conn, task.id, kind="legacy_metadata", payload=metadata)

    if legacy_created:
        kb.add_event(
            conn, task.id, kind="legacy_created_at",
            payload={"value": legacy_created},
        )

    for comment in task.comments:
        kb.add_comment(conn, task.id, comment, author="legacy-md")
