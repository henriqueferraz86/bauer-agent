"""Tests for `bauer/kanban_specify.py` — LLM-promoted triage tasks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bauer import kanban_db as kb
from bauer.kanban_specify import (
    SpecifyOutcome,
    _coerce_body,
    _coerce_title,
    _extract_json,
    list_triage_ids,
    specify_task,
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
    """Seed a triage task and return its id."""
    with kb.connect() as conn:
        kb.init_db(conn)
        return kb.create_task(
            conn,
            "fix login bug",
            body="user reports login fails after refresh",
            status="triage",
        )


class _StubAuxClient:
    """Stand-in for the auxiliary text client. Yields a fixed response."""

    default_model = "stub-model"

    def __init__(self, response: str):
        self._response = response

    def chat_stream(self, model, messages):
        # Stream a few chunks so we exercise the join loop.
        mid = len(self._response) // 2
        yield self._response[:mid]
        yield self._response[mid:]


def _patch_aux(response: str | None):
    """Patch get_text_auxiliary_client to return our stub (or None pair)."""
    if response is None:
        client_pair = (None, None)
    else:
        client_pair = (_StubAuxClient(response), "stub-model")
    return patch(
        "bauer.kanban_specify.get_text_auxiliary_client",
        return_value=client_pair,
    )


# ---------------------------------------------------------------------------
# _extract_json — robust JSON extraction
# ---------------------------------------------------------------------------


def test_extract_json_plain():
    out = _extract_json('{"title": "X", "body": "Y"}')
    assert out == {"title": "X", "body": "Y"}


def test_extract_json_with_code_fence():
    out = _extract_json('```json\n{"title": "X"}\n```')
    assert out == {"title": "X"}


def test_extract_json_with_prose_before_and_after():
    raw = 'Sure! Here is the spec:\n{"title": "X", "body": "Y"}\nLet me know.'
    out = _extract_json(raw)
    assert out == {"title": "X", "body": "Y"}


def test_extract_json_empty_returns_none():
    assert _extract_json("") is None
    assert _extract_json("   ") is None


def test_extract_json_unparsable_returns_none():
    assert _extract_json("not JSON at all") is None
    assert _extract_json("{ broken json") is None


def test_extract_json_non_object_returns_none():
    """Arrays and primitives don't count — we only want objects."""
    assert _extract_json('[1, 2, 3]') is None
    assert _extract_json('"a string"') is None
    assert _extract_json('42') is None


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------


def test_coerce_title_strips_and_caps_at_80():
    long = "a" * 200
    out = _coerce_title(long, "fallback")
    assert len(out) == 80


def test_coerce_title_collapses_whitespace():
    assert _coerce_title("Add   auth   to  /api\n", "x") == "Add auth to /api"


def test_coerce_title_falls_back_on_empty():
    assert _coerce_title("", "fallback") == "fallback"
    assert _coerce_title("   ", "fallback") == "fallback"


def test_coerce_title_falls_back_on_wrong_type():
    assert _coerce_title(42, "fallback") == "fallback"
    assert _coerce_title(None, "fallback") == "fallback"


def test_coerce_body_passes_through_strings():
    assert _coerce_body("plain body", "fb") == "plain body"


def test_coerce_body_falls_back_on_empty():
    assert _coerce_body("   ", "fb") == "fb"


def test_coerce_body_falls_back_on_wrong_type():
    assert _coerce_body(None, "fb") == "fb"


# ---------------------------------------------------------------------------
# specify_task — success path
# ---------------------------------------------------------------------------


def test_specify_task_happy_path(bauer_home: Path, triage_task: str):
    """Auxiliary returns valid JSON; task transitions triage → todo."""
    aux_response = json.dumps({
        "title": "Fix login race after refresh",
        "body": "**Goal**\nLogin survives page refresh.\n\n**Approach**\n- check session cookie\n- retry token refresh\n\n**Acceptance criteria**\n- refreshing after login keeps user logged in\n\n**Out of scope**\n- password reset",
    })
    with _patch_aux(aux_response):
        outcome = specify_task(triage_task)

    assert outcome.ok is True
    assert outcome.task_id == triage_task
    assert "Fix login" in outcome.title
    assert "**Goal**" in outcome.body

    # DB state was updated.
    with kb.connect() as conn:
        task = kb.get_task(conn, triage_task)
        assert task.status == "todo"
        assert "Fix login" in task.title
        assert "**Goal**" in task.body


def test_specify_task_creates_audit_trail(bauer_home: Path, triage_task: str):
    """Successful specify writes both an event and a comment."""
    aux_response = json.dumps({"title": "Spec'd title", "body": "Spec'd body"})
    with _patch_aux(aux_response):
        specify_task(triage_task, author="test-runner")

    with kb.connect() as conn:
        events = kb.list_events(conn, triage_task)
        comments = kb.list_comments(conn, triage_task)

    kinds = [e["kind"] for e in events]
    assert "task.specified" in kinds
    assert any("test-runner" in c.get("author", "") for c in comments)


# ---------------------------------------------------------------------------
# specify_task — guarded failure paths
# ---------------------------------------------------------------------------


def test_specify_task_missing_id(bauer_home: Path):
    with _patch_aux("ignored"):
        outcome = specify_task("t_doesnotexist")
    assert outcome.ok is False
    assert outcome.reason == "task_not_found"


def test_specify_task_not_in_triage_is_noop(bauer_home: Path):
    """A task already in 'todo' returns ok=True with reason='not_triage'."""
    with kb.connect() as conn:
        kb.init_db(conn)
        tid = kb.create_task(conn, "already-todo", status="todo")
    with _patch_aux("ignored"):
        outcome = specify_task(tid)
    assert outcome.ok is True
    assert outcome.reason == "not_triage"


def test_specify_task_aux_unavailable(bauer_home: Path, triage_task: str):
    """When the auxiliary slot returns None, the task is left untouched."""
    with _patch_aux(None):
        outcome = specify_task(triage_task)
    assert outcome.ok is False
    assert outcome.reason == "auxiliary_unavailable"
    with kb.connect() as conn:
        # Task stayed in triage, body intact.
        assert kb.get_task(conn, triage_task).status == "triage"


def test_specify_task_invalid_json_response(bauer_home: Path, triage_task: str):
    """When the LLM returns prose instead of JSON, we mark it as failure."""
    with _patch_aux("Sorry, I cannot do that."):
        outcome = specify_task(triage_task)
    assert outcome.ok is False
    assert outcome.reason == "llm_invalid_json"
    with kb.connect() as conn:
        # Untouched.
        assert kb.get_task(conn, triage_task).status == "triage"


def test_specify_task_partial_response_uses_fallback(bauer_home: Path,
                                                     triage_task: str):
    """If the LLM omits 'body', we keep the original body."""
    with kb.connect() as conn:
        original = kb.get_task(conn, triage_task)
    aux_response = json.dumps({"title": "New title"})   # body missing
    with _patch_aux(aux_response):
        outcome = specify_task(triage_task)

    assert outcome.ok is True
    assert outcome.title == "New title"
    assert outcome.body == original.body   # preserved


def test_specify_task_response_with_code_fence(bauer_home: Path, triage_task: str):
    """LLMs that wrap JSON in ```json are still understood."""
    payload = json.dumps({"title": "Fenced title", "body": "Fenced body"})
    aux_response = f"```json\n{payload}\n```"
    with _patch_aux(aux_response):
        outcome = specify_task(triage_task)
    assert outcome.ok is True
    assert outcome.title == "Fenced title"


def test_specify_task_caps_title_at_80_chars(bauer_home: Path, triage_task: str):
    long = "Very long title " * 20
    aux_response = json.dumps({"title": long, "body": "ok"})
    with _patch_aux(aux_response):
        outcome = specify_task(triage_task)
    assert outcome.ok is True
    assert len(outcome.title) <= 80


# ---------------------------------------------------------------------------
# list_triage_ids — convenience for CLI flows
# ---------------------------------------------------------------------------


def test_list_triage_ids_returns_only_triage_tasks(bauer_home: Path):
    with kb.connect() as conn:
        kb.init_db(conn)
        t1 = kb.create_task(conn, "first", status="triage")
        t2 = kb.create_task(conn, "second", status="todo")
        t3 = kb.create_task(conn, "third", status="triage")

    ids = list_triage_ids()
    assert t1 in ids
    assert t3 in ids
    assert t2 not in ids


def test_list_triage_ids_empty_board(bauer_home: Path):
    with kb.connect() as conn:
        kb.init_db(conn)
    assert list_triage_ids() == []
