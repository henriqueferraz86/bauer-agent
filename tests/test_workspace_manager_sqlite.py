"""Tests for `bauer/workspace_manager_sqlite.py` — SQLite-backed WorkspaceManager."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.workspace_manager import Task, WorkspaceError
from bauer.workspace_manager_sqlite import WorkspaceManagerSqlite


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bauer_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))
    monkeypatch.delenv("BAUER_KANBAN_BOARD", raising=False)
    return tmp_path / "bauer-home"


@pytest.fixture
def wm(tmp_path: Path, bauer_home: Path) -> WorkspaceManagerSqlite:
    """A WorkspaceManagerSqlite anchored to a per-test workspace."""
    workspace = tmp_path / "workspace"
    manager = WorkspaceManagerSqlite(workspace)
    manager.init_project("TestProject")
    return manager


# ---------------------------------------------------------------------------
# init_project
# ---------------------------------------------------------------------------


def test_init_project_creates_files(tmp_path: Path, bauer_home: Path):
    workspace = tmp_path / "workspace"
    manager = WorkspaceManagerSqlite(workspace)
    created = manager.init_project("X", "description")
    names = {p.name for p in created}
    assert "PROJECT.md" in names
    assert "TASKS.md" in names
    assert workspace.exists()


def test_init_project_is_idempotent(wm: WorkspaceManagerSqlite):
    """Calling init_project again doesn't recreate existing files."""
    initial = wm.init_project("AnotherName")
    # PROJECT.md and TASKS.md already exist from the fixture — should return [].
    assert initial == []


# ---------------------------------------------------------------------------
# add_task / get_task / list_tasks
# ---------------------------------------------------------------------------


def test_add_task_returns_task_with_zero_padded_id(wm: WorkspaceManagerSqlite):
    task = wm.add_task("My task")
    assert task.id == "001"
    assert task.title == "My task"
    # Default alinhado ao WorkspaceManager legado (#10-C) — era "TODO".
    assert task.status == "READY"
    assert task.priority == "medium"


def test_add_task_increments_id(wm: WorkspaceManagerSqlite):
    a = wm.add_task("first")
    b = wm.add_task("second")
    c = wm.add_task("third")
    assert [a.id, b.id, c.id] == ["001", "002", "003"]


def test_add_task_with_all_fields(wm: WorkspaceManagerSqlite):
    task = wm.add_task(
        "Feature X",
        description="Body",
        spec_id="auth-v2",
        status="READY",
        priority="high",
        assignee="alice",
        metadata={"run_id": "abc-123"},
    )
    assert task.status == "READY"
    assert task.priority == "high"
    assert task.assignee == "alice"
    assert task.spec_id == "auth-v2"
    assert task.description == "Body"
    # Metadata round-trips through events into the metadata dict
    assert task.metadata.get("run_id") == "abc-123"


def test_add_task_with_parent_links_correctly(wm: WorkspaceManagerSqlite):
    parent = wm.add_task("parent")
    child = wm.add_task("child", parent_id=parent.id)
    assert child.parent_id == parent.id


def test_add_task_invalid_status_raises(wm: WorkspaceManagerSqlite):
    with pytest.raises(WorkspaceError, match="Status invalido"):
        wm.add_task("X", status="WRONG")


def test_add_task_empty_title_raises(wm: WorkspaceManagerSqlite):
    with pytest.raises(WorkspaceError, match="title"):
        wm.add_task("")


def test_list_tasks_returns_all_in_priority_order(wm: WorkspaceManagerSqlite):
    a = wm.add_task("low task", priority="low")
    b = wm.add_task("crit task", priority="critical")
    c = wm.add_task("med task", priority="medium")
    tasks = wm.list_tasks()
    assert [t.id for t in tasks] == [b.id, c.id, a.id]


def test_get_task_normalizes_id(wm: WorkspaceManagerSqlite):
    """get_task accepts plain numbers and 'T0001' aliases."""
    wm.add_task("X")
    assert wm.get_task("1").id == "001"
    assert wm.get_task("001").id == "001"
    assert wm.get_task("T0001").id == "001"


def test_get_task_missing_raises(wm: WorkspaceManagerSqlite):
    with pytest.raises(WorkspaceError, match="nao encontrada"):
        wm.get_task("999")


# ---------------------------------------------------------------------------
# update_task_status — CAS + status mapping
# ---------------------------------------------------------------------------


def test_update_task_status_transitions(wm: WorkspaceManagerSqlite):
    task = wm.add_task("X")
    updated = wm.update_task_status(task.id, "IN_PROGRESS")
    assert updated.status == "IN_PROGRESS"
    fetched = wm.get_task(task.id)
    assert fetched.status == "IN_PROGRESS"


def test_update_task_status_to_done(wm: WorkspaceManagerSqlite):
    task = wm.add_task("X")
    wm.update_task_status(task.id, "READY")
    wm.update_task_status(task.id, "IN_PROGRESS")
    final = wm.update_task_status(task.id, "DONE")
    assert final.status == "DONE"


def test_update_task_status_invalid_raises(wm: WorkspaceManagerSqlite):
    task = wm.add_task("X")
    with pytest.raises(WorkspaceError, match="Status invalido"):
        wm.update_task_status(task.id, "BOGUS")


def test_update_task_status_missing_task_raises(wm: WorkspaceManagerSqlite):
    with pytest.raises(WorkspaceError, match="nao encontrada"):
        wm.update_task_status("999", "DONE")


# ---------------------------------------------------------------------------
# update_task_metadata
# ---------------------------------------------------------------------------


def test_update_metadata_priority_and_assignee(wm: WorkspaceManagerSqlite):
    task = wm.add_task("X")
    updated = wm.update_task_metadata(task.id, priority="critical", assignee="bob")
    assert updated.priority == "critical"
    assert updated.assignee == "bob"


def test_update_metadata_changes_parent(wm: WorkspaceManagerSqlite):
    parent_a = wm.add_task("A")
    parent_b = wm.add_task("B")
    child = wm.add_task("C", parent_id=parent_a.id)

    updated = wm.update_task_metadata(child.id, parent_id=parent_b.id)
    assert updated.parent_id == parent_b.id


def test_update_metadata_clears_parent(wm: WorkspaceManagerSqlite):
    parent = wm.add_task("P")
    child = wm.add_task("C", parent_id=parent.id)
    updated = wm.update_task_metadata(child.id, parent_id="")
    assert updated.parent_id == ""


def test_update_metadata_arbitrary_keys(wm: WorkspaceManagerSqlite):
    task = wm.add_task("X")
    updated = wm.update_task_metadata(task.id, metadata={"run_id": "xyz"})
    assert updated.metadata.get("run_id") == "xyz"


# ---------------------------------------------------------------------------
# add_task_comment
# ---------------------------------------------------------------------------


def test_add_task_comment_appears_in_task(wm: WorkspaceManagerSqlite):
    task = wm.add_task("X")
    wm.add_task_comment(task.id, "first remark", author="alice")
    wm.add_task_comment(task.id, "second remark", author="bob")
    fetched = wm.get_task(task.id)
    bodies = [c["text"] for c in fetched.comments]
    assert bodies == ["first remark", "second remark"]
    assert fetched.comments[0]["author"] == "alice"
    assert fetched.comments[0]["at"]    # ISO timestamp populated


def test_add_task_comment_rejects_empty(wm: WorkspaceManagerSqlite):
    task = wm.add_task("X")
    with pytest.raises(WorkspaceError, match="vazio"):
        wm.add_task_comment(task.id, "   ")


def test_add_task_comment_missing_task_raises(wm: WorkspaceManagerSqlite):
    with pytest.raises(WorkspaceError, match="nao encontrada"):
        wm.add_task_comment("999", "hi")


# ---------------------------------------------------------------------------
# Status mapping — UPPERCASE <-> lowercase
# ---------------------------------------------------------------------------


def test_uppercase_in_lowercase_in_db(wm: WorkspaceManagerSqlite):
    """API uses UPPERCASE; storage uses lowercase. Round-trip preserves it."""
    task = wm.add_task("X", status="IN_PROGRESS")
    assert task.status == "IN_PROGRESS"
    # Re-fetch from DB; mapping converts running -> IN_PROGRESS.
    again = wm.get_task(task.id)
    assert again.status == "IN_PROGRESS"


def test_all_statuses_round_trip(wm: WorkspaceManagerSqlite):
    statuses = ["TODO", "READY", "IN_PROGRESS", "DONE", "BLOCKED", "FAILED"]
    ids = []
    for s in statuses:
        t = wm.add_task(f"task-{s}", status=s)
        ids.append(t.id)
    out = {t.id: t.status for t in wm.list_tasks()}
    for tid, expected in zip(ids, statuses):
        assert out[tid] == expected


# ---------------------------------------------------------------------------
# TASKS.md view regeneration
# ---------------------------------------------------------------------------


def test_view_file_regenerated_after_add(wm: WorkspaceManagerSqlite):
    wm.add_task("My visible task")
    content = wm.tasks_file.read_text(encoding="utf-8")
    assert "My visible task" in content
    assert "001" in content
    assert "gerado" in content   # the autogenerated header markers


def test_view_file_regenerated_after_status_change(wm: WorkspaceManagerSqlite):
    task = wm.add_task("X")
    wm.update_task_status(task.id, "DONE")
    content = wm.tasks_file.read_text(encoding="utf-8")
    assert "[DONE]" in content


def test_view_can_be_disabled(tmp_path: Path, bauer_home: Path):
    """regenerate_view=False skips the TASKS.md regeneration."""
    workspace = tmp_path / "workspace"
    manager = WorkspaceManagerSqlite(workspace, regenerate_view=False)
    manager.init_project("X")
    # Initial init_project still writes the empty view; clear it.
    manager.tasks_file.write_text("# Manually edited", encoding="utf-8")
    manager.add_task("Should not overwrite")
    # Without view regeneration, the manual content survives.
    assert manager.tasks_file.read_text(encoding="utf-8") == "# Manually edited"


# ---------------------------------------------------------------------------
# Multi-board isolation
# ---------------------------------------------------------------------------


def test_boards_are_isolated(tmp_path: Path, bauer_home: Path):
    """Two managers pointed at different boards see different task lists."""
    ws_a = tmp_path / "ws-a"
    ws_b = tmp_path / "ws-b"
    a = WorkspaceManagerSqlite(ws_a, board="alpha")
    b = WorkspaceManagerSqlite(ws_b, board="beta")
    a.init_project("Alpha")
    b.init_project("Beta")

    a.add_task("alpha task")
    b.add_task("beta task")

    assert [t.title for t in a.list_tasks()] == ["alpha task"]
    assert [t.title for t in b.list_tasks()] == ["beta task"]
