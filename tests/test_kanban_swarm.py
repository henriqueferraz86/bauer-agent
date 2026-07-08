"""Tests for `bauer/kanban_swarm.py` — swarm DAG + blackboard IPC."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer import kanban_db as kb
from bauer.kanban_swarm import (
    MAX_WORKERS,
    SwarmCreated,
    blackboard_history,
    create_swarm,
    is_swarm_root,
    latest_blackboard,
    post_blackboard_update,
    swarm_summary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bauer_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))
    monkeypatch.delenv("BAUER_KANBAN_BOARD", raising=False)
    return tmp_path / "bauer-home"


@pytest.fixture
def basic_swarm(bauer_home: Path) -> SwarmCreated:
    """A 3-worker swarm — the common case."""
    return create_swarm(
        goal="Implement OAuth login",
        workers=["Auth API", "Login UI", "Tests"],
    )


# ---------------------------------------------------------------------------
# create_swarm — topology
# ---------------------------------------------------------------------------


def test_create_swarm_returns_full_id_set(basic_swarm: SwarmCreated):
    assert basic_swarm.root_id
    assert basic_swarm.verifier_id
    assert basic_swarm.synthesizer_id
    assert len(basic_swarm.worker_ids) == 3
    assert basic_swarm.goal == "Implement OAuth login"


def test_root_is_done_immediately(basic_swarm: SwarmCreated):
    """Root coordinates but doesn't run — should be in 'done' on creation."""
    with kb.connect() as conn:
        root = kb.get_task(conn, basic_swarm.root_id)
    assert root.status == "done"


def test_workers_are_ready_immediately(basic_swarm: SwarmCreated):
    """Workers can be claimed by the dispatcher right away."""
    with kb.connect() as conn:
        for wid in basic_swarm.worker_ids:
            assert kb.get_task(conn, wid).status == "ready"


def test_verifier_and_synthesizer_start_todo(basic_swarm: SwarmCreated):
    """They wait on dependencies — start in 'todo'."""
    with kb.connect() as conn:
        assert kb.get_task(conn, basic_swarm.verifier_id).status == "todo"
        assert kb.get_task(conn, basic_swarm.synthesizer_id).status == "todo"


def test_verifier_parents_are_all_workers(basic_swarm: SwarmCreated):
    """Verifier waits on EVERY worker before becoming ready."""
    with kb.connect() as conn:
        parents = sorted(kb.parents_of(conn, basic_swarm.verifier_id))
    assert parents == sorted(basic_swarm.worker_ids)


def test_synthesizer_parent_is_verifier(basic_swarm: SwarmCreated):
    """Synthesizer runs only after verifier passes."""
    with kb.connect() as conn:
        parents = kb.parents_of(conn, basic_swarm.synthesizer_id)
    assert parents == [basic_swarm.verifier_id]


def test_workers_have_no_sibling_dependencies(basic_swarm: SwarmCreated):
    """Parallel design: workers don't depend on each other."""
    with kb.connect() as conn:
        for wid in basic_swarm.worker_ids:
            assert kb.parents_of(conn, wid) == []


def test_swarm_created_event_is_recorded(basic_swarm: SwarmCreated):
    with kb.connect() as conn:
        events = kb.list_events(conn, basic_swarm.root_id)
    kinds = [e["kind"] for e in events]
    assert "swarm.created" in kinds


def test_recompute_ready_promotes_verifier_after_workers_done(basic_swarm: SwarmCreated):
    """The full state-machine wiring: workers DONE → verifier READY."""
    with kb.connect() as conn:
        # Simulate worker completion via direct CAS (no actual run).
        for wid in basic_swarm.worker_ids:
            kb.update_status(conn, wid, "done")
        promoted = kb.recompute_ready(conn)
    assert basic_swarm.verifier_id in promoted


def test_recompute_ready_holds_synthesizer_until_verifier(basic_swarm: SwarmCreated):
    """Synthesizer stays 'todo' until the verifier reports DONE."""
    with kb.connect() as conn:
        for wid in basic_swarm.worker_ids:
            kb.update_status(conn, wid, "done")
        kb.recompute_ready(conn)
        # Verifier is now ready but not done — synth must still wait.
        promoted_again = kb.recompute_ready(conn)
        assert basic_swarm.synthesizer_id not in promoted_again
        # Now mark verifier done — synth must promote.
        kb.update_status(conn, basic_swarm.verifier_id, "done")
        promoted_final = kb.recompute_ready(conn)
    assert basic_swarm.synthesizer_id in promoted_final


# ---------------------------------------------------------------------------
# create_swarm — validation
# ---------------------------------------------------------------------------


def test_create_swarm_empty_goal_rejected(bauer_home: Path):
    with pytest.raises(ValueError, match="goal"):
        create_swarm("", workers=["w"])


def test_create_swarm_no_workers_rejected(bauer_home: Path):
    with pytest.raises(ValueError, match="workers"):
        create_swarm("goal", workers=[])


def test_create_swarm_too_many_workers_rejected(bauer_home: Path):
    too_many = [f"worker {i}" for i in range(MAX_WORKERS + 2)]
    with pytest.raises(ValueError, match="workers"):
        create_swarm("goal", workers=too_many)


def test_create_swarm_filters_empty_worker_titles(bauer_home: Path):
    """Empty / whitespace-only worker titles are dropped before count check."""
    swarm = create_swarm("g", workers=["a", "  ", "b", ""])
    assert len(swarm.worker_ids) == 2


def test_create_swarm_custom_role_titles(bauer_home: Path):
    swarm = create_swarm(
        "g", workers=["w"], verifier="Audit", synthesizer="Publish",
    )
    with kb.connect() as conn:
        assert kb.get_task(conn, swarm.verifier_id).title == "Audit"
        assert kb.get_task(conn, swarm.synthesizer_id).title == "Publish"


def test_create_swarm_assignees_applied(bauer_home: Path):
    swarm = create_swarm(
        "g",
        workers=["w"],
        worker_assignee="dev",
        verifier_assignee="qa",
        synthesizer_assignee="docs",
    )
    with kb.connect() as conn:
        assert kb.get_task(conn, swarm.worker_ids[0]).assignee == "dev"
        assert kb.get_task(conn, swarm.verifier_id).assignee == "qa"
        assert kb.get_task(conn, swarm.synthesizer_id).assignee == "docs"


# ---------------------------------------------------------------------------
# Blackboard IPC
# ---------------------------------------------------------------------------


def test_post_blackboard_update_returns_comment_rowid(basic_swarm: SwarmCreated):
    rowid = post_blackboard_update(basic_swarm.root_id, key="api", value="v1")
    assert rowid > 0


def test_post_blackboard_empty_key_rejected(basic_swarm: SwarmCreated):
    with pytest.raises(ValueError, match="key"):
        post_blackboard_update(basic_swarm.root_id, key="", value=1)


def test_post_blackboard_non_json_value_rejected(basic_swarm: SwarmCreated):
    """Non-serialisable values surface as ValueError."""
    class _NotJsonable:
        pass
    with pytest.raises(ValueError):
        post_blackboard_update(basic_swarm.root_id,
                                key="x", value=_NotJsonable())


def test_latest_blackboard_round_trips_simple_values(basic_swarm: SwarmCreated):
    post_blackboard_update(basic_swarm.root_id, key="api", value="https://api/")
    post_blackboard_update(basic_swarm.root_id, key="port", value=8080)
    snap = latest_blackboard(basic_swarm.root_id)
    assert snap == {"api": "https://api/", "port": 8080}


def test_latest_blackboard_last_writer_wins(basic_swarm: SwarmCreated):
    post_blackboard_update(basic_swarm.root_id, key="state", value="draft")
    post_blackboard_update(basic_swarm.root_id, key="state", value="published")
    snap = latest_blackboard(basic_swarm.root_id)
    assert snap["state"] == "published"


def test_latest_blackboard_handles_nested_values(basic_swarm: SwarmCreated):
    payload = {"endpoints": ["a", "b"], "timeout": 30}
    post_blackboard_update(basic_swarm.root_id, key="api_spec", value=payload)
    snap = latest_blackboard(basic_swarm.root_id)
    assert snap["api_spec"] == payload


def test_latest_blackboard_ignores_regular_comments(basic_swarm: SwarmCreated):
    """Plain user comments don't pollute the blackboard."""
    with kb.connect() as conn:
        kb.add_comment(conn, basic_swarm.root_id,
                        "just a note", author="user")
    snap = latest_blackboard(basic_swarm.root_id)
    assert snap == {}


def test_blackboard_history_preserves_order(basic_swarm: SwarmCreated):
    post_blackboard_update(basic_swarm.root_id, key="step", value=1)
    post_blackboard_update(basic_swarm.root_id, key="step", value=2)
    post_blackboard_update(basic_swarm.root_id, key="step", value=3)
    history = blackboard_history(basic_swarm.root_id)
    assert [h["value"] for h in history] == [1, 2, 3]


def test_blackboard_author_is_recorded(basic_swarm: SwarmCreated):
    post_blackboard_update(basic_swarm.root_id, key="x", value=1,
                            author="worker-2")
    history = blackboard_history(basic_swarm.root_id)
    assert any(h["author"] == "worker-2" for h in history)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def test_is_swarm_root_true_for_root(basic_swarm: SwarmCreated):
    assert is_swarm_root(basic_swarm.root_id) is True


def test_is_swarm_root_false_for_worker(basic_swarm: SwarmCreated):
    assert is_swarm_root(basic_swarm.worker_ids[0]) is False


def test_is_swarm_root_false_for_plain_task(bauer_home: Path):
    with kb.connect() as conn:
        kb.init_db(conn)
        tid = kb.create_task(conn, "plain", status="todo")
    assert is_swarm_root(tid) is False


def test_swarm_summary_returns_full_snapshot(basic_swarm: SwarmCreated):
    post_blackboard_update(basic_swarm.root_id, key="api", value="v1")
    snap = swarm_summary(basic_swarm.root_id)
    assert snap["goal"] == "Implement OAuth login"
    assert len(snap["workers"]) == 3
    assert snap["verifier"]["id"] == basic_swarm.verifier_id
    assert snap["synthesizer"]["id"] == basic_swarm.synthesizer_id
    assert snap["blackboard"] == {"api": "v1"}


def test_swarm_summary_non_swarm_returns_error(bauer_home: Path):
    """Calling on a non-swarm task surfaces a structured error."""
    with kb.connect() as conn:
        kb.init_db(conn)
        tid = kb.create_task(conn, "plain", status="todo")
    snap = swarm_summary(tid)
    assert "error" in snap
