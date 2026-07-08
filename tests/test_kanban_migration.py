"""Tests for `bauer/kanban_migration.py` — TASKS.md → SQLite."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer import kanban_db as kb
from bauer.kanban_migration import (
    MigrationReport,
    ParsedTask,
    migrate_tasks_md,
    read_tasks_md,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bauer_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))
    monkeypatch.delenv("BAUER_KANBAN_BOARD", raising=False)
    return tmp_path / "bauer-home"


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Parser — read_tasks_md
# ---------------------------------------------------------------------------


def test_parser_minimal_task(tmp_path: Path):
    md = _write(
        tmp_path / "TASKS.md",
        """# Header
---

## [TODO] My Task
id: 001
criado: 2026-05-31

---
""",
    )
    tasks = read_tasks_md(md)
    assert len(tasks) == 1
    t = tasks[0]
    assert t.id == "001"
    assert t.status == "TODO"
    assert t.title == "My Task"
    assert t.created_at == "2026-05-31"


def test_parser_multiple_tasks(tmp_path: Path):
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] First
id: 001

---

## [DONE] Second
id: 002

---

## [BLOCKED] Third
id: 003

---
""",
    )
    tasks = read_tasks_md(md)
    assert [t.id for t in tasks] == ["001", "002", "003"]
    assert [t.status for t in tasks] == ["TODO", "DONE", "BLOCKED"]


def test_parser_collects_metadata(tmp_path: Path):
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] X
id: 001
priority: high
assignee: alice
spec: auth-v2
parent: 099
claim_id: abc-123

---
""",
    )
    tasks = read_tasks_md(md)
    t = tasks[0]
    assert t.metadata["priority"] == "high"
    assert t.metadata["assignee"] == "alice"
    assert t.metadata["spec_id"] == "auth-v2"  # `spec:` mapped to `spec_id`
    assert t.metadata["parent"] == "099"
    assert t.metadata["claim_id"] == "abc-123"


def test_parser_description_and_comments(tmp_path: Path):
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] X
id: 001
priority: medium

Body paragraph one.
Body paragraph two.

- comment alpha
- comment beta

---
""",
    )
    t = read_tasks_md(md)[0]
    assert "Body paragraph one." in t.description
    assert "Body paragraph two." in t.description
    assert t.comments == ["comment alpha", "comment beta"]


def test_parser_handles_missing_id(tmp_path: Path):
    """Tasks without an `id:` line are silently dropped."""
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] Has ID
id: 001

---

## [TODO] No ID
priority: high

---
""",
    )
    tasks = read_tasks_md(md)
    assert [t.id for t in tasks] == ["001"]


def test_parser_handles_missing_file(tmp_path: Path):
    assert read_tasks_md(tmp_path / "nope.md") == []


def test_parser_handles_trailing_task_without_divider(tmp_path: Path):
    """Last task may have no `---` divider — still captured."""
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] Alpha
id: 001
""",
    )
    tasks = read_tasks_md(md)
    assert [t.id for t in tasks] == ["001"]


def test_parser_uppercase_status_preserved(tmp_path: Path):
    """Parser keeps original UPPERCASE; migrator does the lowercase conversion."""
    md = _write(
        tmp_path / "TASKS.md",
        """## [IN_PROGRESS] X
id: 001
---
""",
    )
    t = read_tasks_md(md)[0]
    assert t.status == "IN_PROGRESS"


# ---------------------------------------------------------------------------
# Migrator — migrate_tasks_md
# ---------------------------------------------------------------------------


def test_migrate_creates_tasks_with_lowercase_status(tmp_path: Path, bauer_home: Path):
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] T1
id: 001
---
## [IN_PROGRESS] T2
id: 002
---
## [DONE] T3
id: 003
---
""",
    )
    report = migrate_tasks_md(md)
    assert report.inserted == ["001", "002", "003"]
    assert report.errors == []

    with kb.connect() as conn:
        statuses = {t.id: t.status for t in kb.list_tasks(conn)}
    # UPPERCASE → lowercase via the mapping table
    assert statuses["001"] == "todo"
    assert statuses["002"] == "running"   # IN_PROGRESS maps to 'running'
    assert statuses["003"] == "done"


def test_migrate_preserves_ids(tmp_path: Path, bauer_home: Path):
    """TASKS.md `id: 001` becomes the SQLite row id verbatim."""
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] X
id: 042
---
""",
    )
    migrate_tasks_md(md)
    with kb.connect() as conn:
        task = kb.get_task(conn, "042")
        assert task.id == "042"


def test_migrate_idempotent(tmp_path: Path, bauer_home: Path):
    """Re-running migration skips existing tasks instead of duplicating."""
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] X
id: 001
---
""",
    )
    r1 = migrate_tasks_md(md)
    r2 = migrate_tasks_md(md)
    assert r1.inserted == ["001"]
    assert r2.inserted == []
    assert r2.skipped == ["001"]


def test_migrate_creates_parent_links(tmp_path: Path, bauer_home: Path):
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] Parent
id: 001
---
## [TODO] Child
id: 002
parent: 001
---
""",
    )
    report = migrate_tasks_md(md)
    assert ("001", "002") in report.links

    with kb.connect() as conn:
        assert kb.children_of(conn, "001") == ["002"]
        assert kb.parents_of(conn, "002") == ["001"]


def test_migrate_handles_self_parent_silently(tmp_path: Path, bauer_home: Path):
    """A task that lists itself as parent shouldn't break the migration."""
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] X
id: 001
parent: 001
---
""",
    )
    report = migrate_tasks_md(md)
    assert "001" in report.inserted
    # No link created and no error logged (we skip self-parents).
    assert report.links == []


def test_migrate_reports_missing_parent(tmp_path: Path, bauer_home: Path):
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] Orphan
id: 001
parent: 999
---
""",
    )
    report = migrate_tasks_md(md)
    assert any("999" in err[1] for err in report.errors)


def test_migrate_persists_priority_and_assignee(tmp_path: Path, bauer_home: Path):
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] X
id: 001
priority: critical
assignee: alice
spec: auth-v2
---
""",
    )
    migrate_tasks_md(md)
    with kb.connect() as conn:
        task = kb.get_task(conn, "001")
        assert task.priority == "critical"
        assert task.assignee == "alice"
        assert task.spec_id == "auth-v2"


def test_migrate_persists_comments(tmp_path: Path, bauer_home: Path):
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] X
id: 001
priority: medium

- first thought
- second thought
- third thought

---
""",
    )
    migrate_tasks_md(md)
    with kb.connect() as conn:
        comments = kb.list_comments(conn, "001")
        bodies = [c["body"] for c in comments]
        assert "first thought" in bodies
        assert "second thought" in bodies
        assert "third thought" in bodies
        # All carry the legacy author marker so they're distinguishable.
        assert all(c["author"] == "legacy-md" for c in comments)


def test_migrate_records_legacy_metadata_event(tmp_path: Path, bauer_home: Path):
    """Unknown / dispatcher metadata becomes a `legacy_metadata` event row."""
    md = _write(
        tmp_path / "TASKS.md",
        """## [IN_PROGRESS] X
id: 001
priority: high
claim_id: old-uuid
worker_pid: 12345
---
""",
    )
    migrate_tasks_md(md)
    with kb.connect() as conn:
        events = kb.list_events(conn, "001")
        kinds = [e["kind"] for e in events]
        assert "legacy_metadata" in kinds
        # Payload survives intact.
        meta_event = next(e for e in events if e["kind"] == "legacy_metadata")
        assert meta_event["payload"].get("claim_id") == "old-uuid"
        assert meta_event["payload"].get("worker_pid") == "12345"


def test_migrate_records_created_at_event(tmp_path: Path, bauer_home: Path):
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] X
id: 001
criado: 2026-05-31
---
""",
    )
    migrate_tasks_md(md)
    with kb.connect() as conn:
        events = kb.list_events(conn, "001")
        ts_event = next((e for e in events if e["kind"] == "legacy_created_at"), None)
        assert ts_event is not None
        assert ts_event["payload"].get("value") == "2026-05-31"


def test_migrate_empty_file(tmp_path: Path, bauer_home: Path):
    md = _write(tmp_path / "TASKS.md", "# Just a header\n")
    report = migrate_tasks_md(md)
    assert report.total == 0


def test_migrate_isolates_to_specified_board(tmp_path: Path, bauer_home: Path):
    md = _write(
        tmp_path / "TASKS.md",
        """## [TODO] On Alpha
id: 001
---
""",
    )
    migrate_tasks_md(md, board="alpha")
    with kb.connect("alpha") as conn:
        assert kb.get_task_or_none(conn, "001") is not None
    with kb.connect("beta") as conn:
        kb.init_db(conn)
        assert kb.get_task_or_none(conn, "001") is None
