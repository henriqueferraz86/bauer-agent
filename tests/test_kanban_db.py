"""Tests for `bauer/kanban_db.py` — SQLite kanban kernel."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from bauer import kanban_db as kb


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bauer_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate the test from the user's real ~/.bauer directory."""
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))
    monkeypatch.delenv("BAUER_KANBAN_BOARD", raising=False)
    return tmp_path / "bauer-home"


@pytest.fixture
def conn(bauer_home: Path):
    """Connection to a fresh, initialised DB for the default board."""
    with kb.connect() as c:
        kb.init_db(c)
        yield c


# ---------------------------------------------------------------------------
# Schema / init
# ---------------------------------------------------------------------------


def test_init_db_idempotent(bauer_home: Path):
    """Calling init_db twice doesn't raise — every CREATE is IF NOT EXISTS."""
    with kb.connect() as c:
        kb.init_db(c)
        kb.init_db(c)
        assert kb.schema_version(c) == kb.SCHEMA_VERSION


def test_schema_version_zero_when_uninit(bauer_home: Path):
    """A connection to a bare DB reports version 0 (no schema_meta yet)."""
    with kb.connect() as c:
        assert kb.schema_version(c) == 0


def test_board_path_creates_directory(bauer_home: Path):
    p = kb.board_path("my-project")
    assert p.parent.exists()
    assert "my-project" in str(p)


def test_board_path_slugifies(bauer_home: Path):
    """Invalid filename chars get replaced with underscores."""
    p = kb.board_path("my project/sub")
    assert "my_project_sub" in str(p)


def test_board_path_env_override(bauer_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BAUER_KANBAN_BOARD", "from-env")
    p = kb.board_path()
    assert "from-env" in str(p)


# ---------------------------------------------------------------------------
# create_task / get_task
# ---------------------------------------------------------------------------


def test_create_task_basic(conn):
    tid = kb.create_task(conn, "Implement X")
    task = kb.get_task(conn, tid)
    assert task.title == "Implement X"
    assert task.status == "todo"
    assert task.priority == "medium"
    assert task.created_at > 0


def test_create_task_with_all_fields(conn):
    tid = kb.create_task(
        conn,
        "Feature Y",
        body="**Goal**: ship the thing",
        status="triage",
        assignee="coder",
        priority="high",
        max_retries=5,
        skills=["python", "sqlite"],
        spec_id="auth-v2",
    )
    task = kb.get_task(conn, tid)
    assert task.body.startswith("**Goal**")
    assert task.status == "triage"
    assert task.assignee == "coder"
    assert task.priority == "high"
    assert task.max_retries == 5
    assert task.skills == ["python", "sqlite"]
    assert task.spec_id == "auth-v2"


def test_create_task_empty_title_rejected(conn):
    with pytest.raises(kb.KanbanDbError, match="title"):
        kb.create_task(conn, "")


def test_create_task_invalid_status_rejected(conn):
    with pytest.raises(kb.KanbanDbError, match="Invalid status"):
        kb.create_task(conn, "X", status="bogus")


def test_create_task_invalid_workspace_kind_rejected(conn):
    with pytest.raises(kb.KanbanDbError, match="workspace_kind"):
        kb.create_task(conn, "X", workspace_kind="nope")


def test_get_task_not_found(conn):
    with pytest.raises(kb.KanbanDbError, match="not found"):
        kb.get_task(conn, "t_doesnotexist")


def test_get_task_or_none(conn):
    assert kb.get_task_or_none(conn, "t_nope") is None
    tid = kb.create_task(conn, "X")
    assert kb.get_task_or_none(conn, tid) is not None


def test_create_task_with_explicit_id(conn):
    """Caller-supplied IDs are preserved (for migrations)."""
    tid = kb.create_task(conn, "X", task_id="t_custom_id")
    assert tid == "t_custom_id"
    assert kb.get_task(conn, "t_custom_id").id == "t_custom_id"


def test_create_task_normalizes_invalid_priority(conn):
    """Invalid priority falls back to 'medium' silently."""
    tid = kb.create_task(conn, "X", priority="ultra-mega-high")
    assert kb.get_task(conn, tid).priority == "medium"


# ---------------------------------------------------------------------------
# list_tasks — filters + ordering
# ---------------------------------------------------------------------------


def test_list_tasks_orders_by_priority_then_created(conn):
    a = kb.create_task(conn, "low task", priority="low")
    time.sleep(0.01)
    b = kb.create_task(conn, "critical task", priority="critical")
    time.sleep(0.01)
    c = kb.create_task(conn, "high task", priority="high")
    out = kb.list_tasks(conn)
    assert [t.id for t in out] == [b, c, a]


def test_list_tasks_filter_by_status(conn):
    a = kb.create_task(conn, "alpha", status="todo")
    b = kb.create_task(conn, "beta", status="done")
    assert {t.id for t in kb.list_tasks(conn, status="done")} == {b}


def test_list_tasks_filter_by_assignee(conn):
    a = kb.create_task(conn, "a", assignee="alice")
    b = kb.create_task(conn, "b", assignee="bob")
    out = kb.list_tasks(conn, assignee="bob")
    assert [t.id for t in out] == [b]


def test_list_tasks_filter_by_parent_id(conn):
    """parent_id filter joins task_links and returns direct children only."""
    root = kb.create_task(conn, "root")
    c1 = kb.create_task(conn, "child1")
    c2 = kb.create_task(conn, "child2")
    other = kb.create_task(conn, "unrelated")
    kb.link_tasks(conn, root, c1)
    kb.link_tasks(conn, root, c2)
    out = kb.list_tasks(conn, parent_id=root)
    assert {t.id for t in out} == {c1, c2}


# ---------------------------------------------------------------------------
# Status transitions / CAS
# ---------------------------------------------------------------------------


def test_update_status_simple(conn):
    tid = kb.create_task(conn, "X")
    assert kb.update_status(conn, tid, "ready")
    assert kb.get_task(conn, tid).status == "ready"


def test_update_status_cas_rejects_mismatch(conn):
    """expected_status='running' fails when row is 'todo'."""
    tid = kb.create_task(conn, "X")
    assert not kb.update_status(conn, tid, "done", expected_status="running")
    assert kb.get_task(conn, tid).status == "todo"


def test_update_status_invalid_rejected(conn):
    tid = kb.create_task(conn, "X")
    with pytest.raises(kb.KanbanDbError, match="Invalid status"):
        kb.update_status(conn, tid, "bogus")


# ---------------------------------------------------------------------------
# Claim / heartbeat — the CAS dance
# ---------------------------------------------------------------------------


def test_claim_task_only_works_on_ready(conn):
    """Cannot claim a todo task — must be promoted to ready first."""
    tid = kb.create_task(conn, "X", status="todo")
    assert kb.claim_task(conn, tid) is None
    kb.update_status(conn, tid, "ready", expected_status="todo")
    lock = kb.claim_task(conn, tid)
    assert lock is not None
    assert kb.get_task(conn, tid).status == "running"


def test_claim_task_second_claim_loses_race(conn):
    """Once claimed, a second claim attempt observes 0 affected rows."""
    tid = kb.create_task(conn, "X", status="ready")
    lock1 = kb.claim_task(conn, tid)
    assert lock1 is not None
    lock2 = kb.claim_task(conn, tid)
    assert lock2 is None


def test_concurrent_claims_at_most_one_wins(bauer_home: Path):
    """Stress test: 10 threads race to claim the same task; exactly one wins."""
    with kb.connect() as c:
        kb.init_db(c)
        tid = kb.create_task(c, "X", status="ready")

    results: list[str | None] = []
    lock = threading.Lock()

    def attempt():
        with kb.connect() as c:
            won = kb.claim_task(c, tid)
            with lock:
                results.append(won)

    threads = [threading.Thread(target=attempt) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"expected exactly 1 winner, got {len(winners)}"


def test_heartbeat_extends_ttl(conn):
    tid = kb.create_task(conn, "X", status="ready")
    lock = kb.claim_task(conn, tid, ttl_s=60)
    assert lock is not None
    before = kb.get_task(conn, tid).claim_expires
    time.sleep(0.05)
    assert kb.heartbeat(conn, tid, claim_lock=lock, ttl_s=600)
    after = kb.get_task(conn, tid).claim_expires
    assert after > before


def test_heartbeat_rejects_stale_lock(conn):
    """Worker with the wrong lock cannot heartbeat — protects against reclaim races."""
    tid = kb.create_task(conn, "X", status="ready")
    kb.claim_task(conn, tid)
    assert not kb.heartbeat(conn, tid, claim_lock="wrong-lock")


# ---------------------------------------------------------------------------
# Complete / fail with retry budget
# ---------------------------------------------------------------------------


def test_complete_task_marks_done_and_records_run(conn):
    tid = kb.create_task(conn, "X", status="ready")
    kb.claim_task(conn, tid)
    assert kb.complete_task(conn, tid, summary="done deal")
    task = kb.get_task(conn, tid)
    assert task.status == "done"
    assert task.completed_at > 0
    runs = kb.list_runs(conn, tid)
    assert len(runs) == 1
    assert runs[0]["outcome"] == "success"


def test_complete_task_requires_running(conn):
    """Cannot complete a task that's not in 'running' (CAS)."""
    tid = kb.create_task(conn, "X", status="todo")
    assert not kb.complete_task(conn, tid, summary="too early")


def test_fail_task_retries_within_budget(conn):
    """First failure with max_retries=2 goes back to ready (not failed)."""
    tid = kb.create_task(conn, "X", status="ready", max_retries=2)
    kb.claim_task(conn, tid)
    new_status = kb.fail_task(conn, tid, error="boom")
    assert new_status == "ready"
    t = kb.get_task(conn, tid)
    assert t.status == "ready"
    assert t.consecutive_failures == 1
    assert t.last_failure_error == "boom"


def test_fail_task_exhausts_budget_to_failed(conn):
    """After max_retries failures, status flips to 'failed' permanently."""
    tid = kb.create_task(conn, "X", status="ready", max_retries=2)
    # Fail once → back to ready
    kb.claim_task(conn, tid)
    kb.fail_task(conn, tid, error="err1")
    # Fail twice — budget exhausted
    kb.claim_task(conn, tid)
    new_status = kb.fail_task(conn, tid, error="err2")
    assert new_status == "failed"
    assert kb.get_task(conn, tid).status == "failed"


# ---------------------------------------------------------------------------
# Reclaim stale
# ---------------------------------------------------------------------------


def test_reclaim_stale_returns_expired(conn):
    """Tasks whose claim_expires < now go back to ready."""
    tid = kb.create_task(conn, "X", status="ready")
    # Claim with a very short TTL (30s minimum enforced; we patch the row directly).
    kb.claim_task(conn, tid, ttl_s=30)
    # Force the claim into the past.
    conn.execute(
        "UPDATE tasks SET claim_expires = ? WHERE id = ?",
        (time.time() - 1, tid),
    )
    reclaimed = kb.reclaim_stale(conn)
    assert tid in reclaimed
    assert kb.get_task(conn, tid).status == "ready"


def test_reclaim_stale_leaves_active_alone(conn):
    """A task with a still-valid claim is not reclaimed."""
    tid = kb.create_task(conn, "X", status="ready")
    kb.claim_task(conn, tid, ttl_s=600)
    reclaimed = kb.reclaim_stale(conn)
    assert tid not in reclaimed
    assert kb.get_task(conn, tid).status == "running"


# ---------------------------------------------------------------------------
# Links + Kahn's cycle detection
# ---------------------------------------------------------------------------


def test_link_tasks_basic(conn):
    a = kb.create_task(conn, "A")
    b = kb.create_task(conn, "B")
    assert kb.link_tasks(conn, a, b)
    assert kb.children_of(conn, a) == [b]
    assert kb.parents_of(conn, b) == [a]


def test_link_tasks_idempotent(conn):
    a = kb.create_task(conn, "A")
    b = kb.create_task(conn, "B")
    kb.link_tasks(conn, a, b)
    # Second add is no-op (INSERT OR IGNORE)
    assert not kb.link_tasks(conn, a, b)


def test_link_tasks_self_link_rejected(conn):
    a = kb.create_task(conn, "A")
    with pytest.raises(kb.CycleError, match="Self-link"):
        kb.link_tasks(conn, a, a)


def test_link_tasks_missing_parent_rejected(conn):
    b = kb.create_task(conn, "B")
    with pytest.raises(kb.KanbanDbError, match="not found"):
        kb.link_tasks(conn, "t_nope", b)


def test_link_tasks_cycle_rejected(conn):
    a = kb.create_task(conn, "A")
    b = kb.create_task(conn, "B")
    c = kb.create_task(conn, "C")
    kb.link_tasks(conn, a, b)
    kb.link_tasks(conn, b, c)
    with pytest.raises(kb.CycleError, match="cycle"):
        kb.link_tasks(conn, c, a)


def test_link_tasks_diamond_dag_ok(conn):
    """Diamond shape is not a cycle: A→B, A→C, B→D, C→D."""
    a = kb.create_task(conn, "A")
    b = kb.create_task(conn, "B")
    c = kb.create_task(conn, "C")
    d = kb.create_task(conn, "D")
    assert kb.link_tasks(conn, a, b)
    assert kb.link_tasks(conn, a, c)
    assert kb.link_tasks(conn, b, d)
    assert kb.link_tasks(conn, c, d)


def test_unlink_tasks(conn):
    a = kb.create_task(conn, "A")
    b = kb.create_task(conn, "B")
    kb.link_tasks(conn, a, b)
    assert kb.unlink_tasks(conn, a, b)
    assert kb.children_of(conn, a) == []


# ---------------------------------------------------------------------------
# Comments / events / runs
# ---------------------------------------------------------------------------


def test_add_comment(conn):
    tid = kb.create_task(conn, "X")
    rid = kb.add_comment(conn, tid, "first comment", author="alice")
    assert rid > 0
    comments = kb.list_comments(conn, tid)
    assert len(comments) == 1
    assert comments[0]["body"] == "first comment"
    assert comments[0]["author"] == "alice"


def test_add_event_with_dict_payload(conn):
    tid = kb.create_task(conn, "X")
    kb.add_event(conn, tid, kind="claim", payload={"runner": "bob"})
    events = kb.list_events(conn, tid)
    assert len(events) == 1
    assert events[0]["kind"] == "claim"
    assert events[0]["payload"] == {"runner": "bob"}


def test_start_run_creates_open_row(conn):
    tid = kb.create_task(conn, "X")
    run_id = kb.start_run(conn, tid, profile="default")
    runs = kb.list_runs(conn, tid)
    assert len(runs) == 1
    assert runs[0]["run_id"] == run_id
    assert runs[0]["ended_at"] is None


# ---------------------------------------------------------------------------
# Promote (recompute_ready) + dispatch_once
# ---------------------------------------------------------------------------


def test_recompute_ready_promotes_parentless(conn):
    """A task with no parents goes from todo to ready on the first tick."""
    tid = kb.create_task(conn, "X", status="todo")
    promoted = kb.recompute_ready(conn)
    assert tid in promoted
    assert kb.get_task(conn, tid).status == "ready"


def test_recompute_ready_waits_for_parent(conn):
    """A child with an unfinished parent stays in todo."""
    parent = kb.create_task(conn, "parent", status="todo")
    child = kb.create_task(conn, "child", status="todo")
    kb.link_tasks(conn, parent, child)
    kb.recompute_ready(conn)
    # parent becomes ready, child stays todo (parent not done)
    assert kb.get_task(conn, parent).status == "ready"
    assert kb.get_task(conn, child).status == "todo"
    # Mark parent done → child becomes ready
    kb.update_status(conn, parent, "done")
    kb.recompute_ready(conn)
    assert kb.get_task(conn, child).status == "ready"


def test_dispatch_once_claims_one_by_default(conn):
    """max_spawn=1 (default) claims at most one task per tick."""
    a = kb.create_task(conn, "A", status="ready", priority="high")
    b = kb.create_task(conn, "B", status="ready", priority="medium")
    result = kb.dispatch_once(conn)
    assert len(result.claimed) == 1
    # A goes first (higher priority)
    assert result.claimed == [a]
    assert kb.get_task(conn, a).status == "running"
    assert kb.get_task(conn, b).status == "ready"


def test_dispatch_once_max_in_progress_caps(conn):
    """max_in_progress prevents spawning when too many tasks already running."""
    a = kb.create_task(conn, "A", status="running")
    b = kb.create_task(conn, "B", status="ready")
    result = kb.dispatch_once(conn, max_spawn=5, max_in_progress=1)
    assert result.claimed == []  # cap respected


def test_dispatch_once_promotes_and_claims_in_one_call(conn):
    """Single tick: todo→ready (recompute) followed by ready→running (claim)."""
    tid = kb.create_task(conn, "X", status="todo")
    result = kb.dispatch_once(conn, max_spawn=1)
    assert tid in result.promoted
    assert tid in result.claimed
    assert kb.get_task(conn, tid).status == "running"


# ---------------------------------------------------------------------------
# Multi-board
# ---------------------------------------------------------------------------


def test_list_boards_returns_existing(bauer_home: Path):
    """list_boards reads the filesystem; brand-new boards appear after init."""
    with kb.connect("alpha") as c:
        kb.init_db(c)
    with kb.connect("beta") as c:
        kb.init_db(c)
    boards = kb.list_boards()
    assert "alpha" in boards
    assert "beta" in boards


def test_active_board_marker_round_trip(bauer_home: Path):
    kb.set_active_board("myboard")
    assert kb.get_active_board() == "myboard"


def test_active_board_env_overrides_marker(bauer_home: Path,
                                            monkeypatch: pytest.MonkeyPatch):
    kb.set_active_board("from-marker")
    monkeypatch.setenv("BAUER_KANBAN_BOARD", "from-env")
    assert kb.get_active_board() == "from-env"


def test_multi_board_isolation(bauer_home: Path):
    """Tasks created in board A are invisible from board B."""
    with kb.connect("alpha") as c:
        kb.init_db(c)
        a_task = kb.create_task(c, "alpha task")
    with kb.connect("beta") as c:
        kb.init_db(c)
        beta_tasks = kb.list_tasks(c)
        assert a_task not in [t.id for t in beta_tasks]
        # Even creating something in beta doesn't leak
        b_task = kb.create_task(c, "beta task")
    with kb.connect("alpha") as c:
        alpha_tasks = kb.list_tasks(c)
        assert b_task not in [t.id for t in alpha_tasks]
        assert a_task in [t.id for t in alpha_tasks]
