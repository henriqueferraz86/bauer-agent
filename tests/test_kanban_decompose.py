"""Tests for `bauer/kanban_decompose.py` — LLM fan-out of complex tasks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bauer import kanban_db as kb
from bauer.kanban_decompose import (
    MAX_CHILDREN,
    DecomposeOutcome,
    _children_have_cycle,
    _validate,
    decompose_task,
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
def triage_task(bauer_home: Path) -> str:
    with kb.connect() as conn:
        kb.init_db(conn)
        return kb.create_task(conn, "build OAuth login",
                               body="users want SSO",
                               status="triage")


@pytest.fixture
def todo_task(bauer_home: Path) -> str:
    with kb.connect() as conn:
        kb.init_db(conn)
        return kb.create_task(conn, "ship dashboard",
                               body="visualise weekly numbers",
                               status="todo")


class _StubAuxClient:
    default_model = "stub-model"

    def __init__(self, response: str):
        self._response = response

    def chat_stream(self, model, messages):
        yield self._response


def _patch_aux(response: str | None):
    if response is None:
        client_pair = (None, None)
    else:
        client_pair = (_StubAuxClient(response), "stub-model")
    return patch(
        "bauer.kanban_decompose.get_text_auxiliary_client",
        return_value=client_pair,
    )


def _fanout_response(tasks: list[dict], rationale: str = "auto-decomp") -> str:
    return json.dumps({
        "fanout": True,
        "tasks": tasks,
        "rationale": rationale,
    })


def _three_independent_tasks() -> list[dict]:
    """Three tasks, no inter-dependencies — all parallel."""
    return [
        {"title": "set up auth provider", "body": "pick OIDC", "parents": []},
        {"title": "wire login UI",       "body": "form + button", "parents": []},
        {"title": "add tests",           "body": "happy + error paths", "parents": []},
    ]


def _two_then_one() -> list[dict]:
    """Two parallel then a verifier."""
    return [
        {"title": "set up auth provider", "body": "...", "parents": []},
        {"title": "wire login UI",       "body": "...", "parents": []},
        {"title": "verify integration",  "body": "...", "parents": [0, 1]},
    ]


# ---------------------------------------------------------------------------
# _validate
# ---------------------------------------------------------------------------


def test_validate_happy_fanout():
    parsed = json.loads(_fanout_response(_three_independent_tasks()))
    specs, rationale, fanout = _validate(parsed)
    assert fanout is True
    assert len(specs) == 3
    assert all("title" in s for s in specs)
    assert rationale == "auto-decomp"


def test_validate_demotes_fanout_when_only_one_task():
    """fanout=true + 1 task → fanout=false (degenerate)."""
    parsed = json.loads(_fanout_response([
        {"title": "do the thing", "body": "x", "parents": []},
    ]))
    _, _, fanout = _validate(parsed)
    assert fanout is False


def test_validate_truncates_excess_children():
    many = [
        {"title": f"task {i}", "body": "x", "parents": []}
        for i in range(MAX_CHILDREN + 3)
    ]
    parsed = json.loads(_fanout_response(many))
    specs, _, _ = _validate(parsed)
    assert len(specs) == MAX_CHILDREN


def test_validate_rejects_non_dict_response():
    with pytest.raises(ValueError):
        _validate([1, 2, 3])    # type: ignore[arg-type]


def test_validate_rejects_missing_tasks():
    with pytest.raises(ValueError, match="tasks"):
        _validate({"fanout": True, "tasks": []})


def test_validate_rejects_task_without_title():
    with pytest.raises(ValueError, match="title"):
        _validate({"fanout": True, "tasks": [{"body": "no title"}]})


def test_validate_rejects_non_integer_parent():
    with pytest.raises(ValueError, match="non-integer"):
        _validate({
            "fanout": True,
            "tasks": [
                {"title": "a", "parents": []},
                {"title": "b", "parents": ["abc"]},
            ],
        })


def test_validate_rejects_self_reference():
    with pytest.raises(ValueError, match="itself"):
        _validate({
            "fanout": True,
            "tasks": [
                {"title": "a", "parents": []},
                {"title": "b", "parents": [1]},   # references self
            ],
        })


def test_validate_rejects_out_of_range_parent():
    with pytest.raises(ValueError, match="out of range"):
        _validate({
            "fanout": True,
            "tasks": [
                {"title": "a", "parents": []},
                {"title": "b", "parents": [99]},
            ],
        })


def test_validate_accepts_singleton_with_fanout_false():
    """fanout=false + 1 task is the spec promotion path."""
    parsed = {
        "fanout": False,
        "tasks": [{"title": "atomic task", "parents": []}],
    }
    specs, _, fanout = _validate(parsed)
    assert fanout is False
    assert len(specs) == 1


# ---------------------------------------------------------------------------
# _children_have_cycle
# ---------------------------------------------------------------------------


def test_no_cycle_when_all_independent():
    specs = [{"title": "a", "parents": []}, {"title": "b", "parents": []}]
    assert _children_have_cycle(specs) is False


def test_cycle_detected_in_two_node_loop():
    specs = [{"title": "a", "parents": [1]}, {"title": "b", "parents": [0]}]
    assert _children_have_cycle(specs) is True


def test_no_cycle_in_diamond_dag():
    """0 → 1, 0 → 2, 1 → 3, 2 → 3 — valid DAG."""
    specs = [
        {"title": "a", "parents": []},
        {"title": "b", "parents": [0]},
        {"title": "c", "parents": [0]},
        {"title": "d", "parents": [1, 2]},
    ]
    assert _children_have_cycle(specs) is False


# ---------------------------------------------------------------------------
# decompose_task — happy paths
# ---------------------------------------------------------------------------


def test_decompose_creates_children_with_no_deps(bauer_home: Path, triage_task: str):
    """All-parallel decomposition: 3 children, no sibling links."""
    response = _fanout_response(_three_independent_tasks())
    with _patch_aux(response):
        outcome = decompose_task(triage_task)

    assert outcome.ok is True
    assert outcome.fanout is True
    assert len(outcome.child_ids) == 3

    with kb.connect() as conn:
        for cid in outcome.child_ids:
            child = kb.get_task(conn, cid)
            assert child.status == "todo"
        # Root has the children as parents (so it waits for them).
        parents_of_root = kb.parents_of(conn, triage_task)
        assert sorted(parents_of_root) == sorted(outcome.child_ids)


def test_decompose_wires_sibling_dependencies(bauer_home: Path, triage_task: str):
    """When parents=[0,1], child 2 lists 0 and 1 as direct parents."""
    response = _fanout_response(_two_then_one())
    with _patch_aux(response):
        outcome = decompose_task(triage_task)

    assert outcome.ok is True
    child_ids = outcome.child_ids
    with kb.connect() as conn:
        # Child 2 has children 0 and 1 as parents.
        c2_parents = kb.parents_of(conn, child_ids[2])
        assert set(c2_parents) == {child_ids[0], child_ids[1]}
        # Children 0 and 1 have no sibling parents.
        assert kb.parents_of(conn, child_ids[0]) == []
        assert kb.parents_of(conn, child_ids[1]) == []
        # The root waits ONLY on the verifier (the only leaf).
        root_parents = kb.parents_of(conn, triage_task)
        assert root_parents == [child_ids[2]]


def test_decompose_audit_trail(bauer_home: Path, triage_task: str):
    response = _fanout_response(_three_independent_tasks())
    with _patch_aux(response):
        decompose_task(triage_task, author="planner-test")

    with kb.connect() as conn:
        events = kb.list_events(conn, triage_task)
        comments = kb.list_comments(conn, triage_task)
    kinds = [e["kind"] for e in events]
    assert "task.decomposed" in kinds
    assert any("planner-test" in c.get("author", "") for c in comments)


def test_decompose_works_on_todo_status(bauer_home: Path, todo_task: str):
    """Tasks in 'todo' can also be decomposed."""
    response = _fanout_response(_three_independent_tasks())
    with _patch_aux(response):
        outcome = decompose_task(todo_task)
    assert outcome.ok is True
    assert len(outcome.child_ids) == 3


def test_decompose_fanout_false_promotes_in_place(bauer_home: Path, triage_task: str):
    """fanout=false: parent's title/body updated, no children created."""
    response = json.dumps({
        "fanout": False,
        "tasks": [{"title": "Atomic OAuth", "body": "do the OAuth"}],
        "rationale": "single team, no real subtasks",
    })
    with _patch_aux(response):
        outcome = decompose_task(triage_task)

    assert outcome.ok is True
    assert outcome.fanout is False
    assert outcome.child_ids == []

    with kb.connect() as conn:
        root = kb.get_task(conn, triage_task)
        assert root.title == "Atomic OAuth"
        assert root.body == "do the OAuth"
        assert root.status == "todo"   # promoted out of triage


# ---------------------------------------------------------------------------
# decompose_task — failure paths
# ---------------------------------------------------------------------------


def test_decompose_missing_id(bauer_home: Path):
    with _patch_aux("ignored"):
        outcome = decompose_task("t_nope")
    assert outcome.ok is False
    assert outcome.reason == "task_not_found"


def test_decompose_wrong_status_running(bauer_home: Path):
    with kb.connect() as conn:
        kb.init_db(conn)
        tid = kb.create_task(conn, "running task", status="running")
    with _patch_aux("ignored"):
        outcome = decompose_task(tid)
    assert outcome.ok is False
    assert "wrong_status" in outcome.reason


def test_decompose_wrong_status_done(bauer_home: Path):
    with kb.connect() as conn:
        kb.init_db(conn)
        tid = kb.create_task(conn, "done task", status="done")
    with _patch_aux("ignored"):
        outcome = decompose_task(tid)
    assert outcome.ok is False
    assert "wrong_status" in outcome.reason


def test_decompose_aux_unavailable(bauer_home: Path, triage_task: str):
    with _patch_aux(None):
        outcome = decompose_task(triage_task)
    assert outcome.ok is False
    assert outcome.reason == "auxiliary_unavailable"
    # Task untouched.
    with kb.connect() as conn:
        assert kb.get_task(conn, triage_task).status == "triage"


def test_decompose_invalid_json(bauer_home: Path, triage_task: str):
    with _patch_aux("Sorry, can't decompose."):
        outcome = decompose_task(triage_task)
    assert outcome.ok is False
    assert outcome.reason == "llm_invalid_json"


def test_decompose_invalid_structure(bauer_home: Path, triage_task: str):
    """JSON parsed but didn't validate (e.g. missing title)."""
    response = json.dumps({
        "fanout": True,
        "tasks": [{"body": "no title here", "parents": []}],
    })
    with _patch_aux(response):
        outcome = decompose_task(triage_task)
    assert outcome.ok is False
    assert outcome.reason.startswith("llm_invalid_structure")


def test_decompose_cycle_detected(bauer_home: Path, triage_task: str):
    """LLM-proposed cycle is caught before DB writes."""
    response = json.dumps({
        "fanout": True,
        "tasks": [
            {"title": "a", "parents": [1]},
            {"title": "b", "parents": [0]},
        ],
    })
    with _patch_aux(response):
        outcome = decompose_task(triage_task)
    assert outcome.ok is False
    assert outcome.reason == "cycle_detected"
    # No children were created.
    with kb.connect() as conn:
        assert kb.list_tasks(conn, parent_id=triage_task) == []


# ---------------------------------------------------------------------------
# Cycle-detection regression: the diamond DAG is OK
# ---------------------------------------------------------------------------


def test_decompose_diamond_dag_succeeds(bauer_home: Path, triage_task: str):
    """0 → 1, 0 → 2, 1 → 3, 2 → 3 — diamond, no cycle."""
    response = json.dumps({
        "fanout": True,
        "tasks": [
            {"title": "scope",  "parents": []},
            {"title": "build",  "parents": [0]},
            {"title": "review", "parents": [0]},
            {"title": "ship",   "parents": [1, 2]},
        ],
    })
    with _patch_aux(response):
        outcome = decompose_task(triage_task)
    assert outcome.ok is True
    assert len(outcome.child_ids) == 4

    with kb.connect() as conn:
        # The single leaf (child 3) is the root's parent — the other three
        # have outgoing edges, so they're not leaves.
        root_parents = kb.parents_of(conn, triage_task)
        assert root_parents == [outcome.child_ids[3]]
