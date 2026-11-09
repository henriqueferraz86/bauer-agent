"""Sprint 65 (plano 065): contrato de escopo de workspace + feedback estruturado."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.memory_feedback import FeedbackStore, record_turn_feedback, snapshot_last_turn
from bauer.memory_scope import (
    ScopeViolationError,
    resolve_workspace_scope,
    sanitize_text,
    validate_scope_kind,
)
from bauer.sqlite_session_store import SqliteSessionStore

SECRET = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX1234567890"


@pytest.fixture
def two_workspaces(tmp_path: Path) -> tuple[Path, Path]:
    a = tmp_path / "ws-a"
    b = tmp_path / "ws-b"
    a.mkdir()
    b.mkdir()
    return a, b


def _store(sessions: Path, workspace: Path | None) -> SqliteSessionStore:
    return SqliteSessionStore(sessions, semantic_indexing_enabled=False, workspace=workspace)


# --- contrato de escopo -----------------------------------------------------


def test_workspace_scope_id_is_stable_and_path_based(two_workspaces):
    a, b = two_workspaces
    first = resolve_workspace_scope(a)
    again = resolve_workspace_scope(str(a / "." ))
    other = resolve_workspace_scope(b)
    assert first is not None and again is not None and other is not None
    assert first.workspace_id == again.workspace_id
    assert first.workspace_id != other.workspace_id
    assert first.workspace_id.startswith("ws-")
    assert first.memory_dir == Path(first.path) / "memory"


@pytest.mark.parametrize("bad", [None, "", "   ", MagicMock(), 42])
def test_workspace_scope_rejects_non_paths(bad):
    assert resolve_workspace_scope(bad) is None


def test_company_and_user_scopes_are_rejected():
    assert validate_scope_kind("workspace") == "workspace"
    with pytest.raises(ScopeViolationError, match="company"):
        validate_scope_kind("company")
    with pytest.raises(ScopeViolationError, match="user"):
        validate_scope_kind("user")


def test_require_same_blocks_cross_workspace(two_workspaces):
    a, b = two_workspaces
    scope_a = resolve_workspace_scope(a)
    scope_b = resolve_workspace_scope(b)
    assert scope_a is not None and scope_b is not None
    scope_a.require_same(scope_a.workspace_id)
    with pytest.raises(ScopeViolationError):
        scope_a.require_same(scope_b.workspace_id)


def test_sanitize_text_redacts_before_truncating():
    result = sanitize_text(f"minha chave é {SECRET} ok", limit=20)
    assert SECRET not in result.text
    assert result.redacted is True
    assert result.sensitive is False
    assert result.decision == "redacted"
    assert sanitize_text("texto comum").decision == "clean"


# --- proveniência das sessões ---------------------------------------------


def test_new_session_is_stamped_with_workspace(tmp_path, two_workspaces):
    a, _ = two_workspaces
    store = _store(tmp_path / "sessions", a)
    store.save("s1", [{"role": "user", "content": "oi"}])
    prov = store.get_provenance("s1")
    scope = resolve_workspace_scope(a)
    assert prov is not None and scope is not None
    assert prov["workspace_id"] == scope.workspace_id
    assert prov["workspace_conflict"] == 0
    assert [s["session_id"] for s in store.list_sessions_for_workspace(scope.workspace_id)] == ["s1"]


def test_sessions_never_leak_across_workspaces(tmp_path, two_workspaces):
    a, b = two_workspaces
    sessions = tmp_path / "sessions"
    _store(sessions, a).save("sa", [{"role": "user", "content": "a"}])
    _store(sessions, b).save("sb", [{"role": "user", "content": "b"}])
    scope_a = resolve_workspace_scope(a)
    scope_b = resolve_workspace_scope(b)
    assert scope_a is not None and scope_b is not None
    reader = _store(sessions, None)
    assert [s["session_id"] for s in reader.list_sessions_for_workspace(scope_a.workspace_id)] == ["sa"]
    assert [s["session_id"] for s in reader.list_sessions_for_workspace(scope_b.workspace_id)] == ["sb"]


def test_session_resumed_from_other_workspace_becomes_ineligible(tmp_path, two_workspaces):
    a, b = two_workspaces
    sessions = tmp_path / "sessions"
    _store(sessions, a).save("s1", [{"role": "user", "content": "a"}])
    _store(sessions, b).save("s1", [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}])
    scope_a = resolve_workspace_scope(a)
    scope_b = resolve_workspace_scope(b)
    assert scope_a is not None and scope_b is not None
    reader = _store(sessions, None)
    assert reader.list_sessions_for_workspace(scope_a.workspace_id) == []
    assert reader.list_sessions_for_workspace(scope_b.workspace_id) == []
    assert reader.provenance_summary(scope_a.workspace_id)["conflict"] == 1


def test_session_saved_without_workspace_after_stamp_is_conflict(tmp_path, two_workspaces):
    a, _ = two_workspaces
    sessions = tmp_path / "sessions"
    _store(sessions, a).save("s1", [{"role": "user", "content": "a"}])
    _store(sessions, None).save("s1", [{"role": "user", "content": "x"}])
    scope_a = resolve_workspace_scope(a)
    assert scope_a is not None
    assert _store(sessions, a).list_sessions_for_workspace(scope_a.workspace_id) == []


def test_historic_session_never_gains_provenance(tmp_path, two_workspaces):
    a, _ = two_workspaces
    sessions = tmp_path / "sessions"
    _store(sessions, None).save("old", [{"role": "user", "content": "antiga"}])
    store_a = _store(sessions, a)
    store_a.save("old", [{"role": "user", "content": "antiga"}, {"role": "user", "content": "nova"}])
    scope_a = resolve_workspace_scope(a)
    assert scope_a is not None
    prov = store_a.get_provenance("old")
    assert prov is not None and prov["workspace_id"] is None
    assert store_a.list_sessions_for_workspace(scope_a.workspace_id) == []
    assert store_a.provenance_summary(scope_a.workspace_id)["no_provenance"] == 1


def test_pre_existing_database_is_migrated_without_provenance(tmp_path, two_workspaces):
    a, _ = two_workspaces
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    conn = sqlite3.connect(sessions / "sessions.db")
    conn.executescript(
        """
        CREATE TABLE sessions (session_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, message_count INTEGER NOT NULL DEFAULT 0,
            summary TEXT NOT NULL DEFAULT '');
        CREATE TABLE messages (rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
            idx INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, ts TEXT NOT NULL);
        INSERT INTO sessions VALUES ('legacy', '2026-01-01', '2026-01-01', 1, '');
        INSERT INTO messages(session_id, idx, role, content, ts)
            VALUES ('legacy', 0, 'user', 'velha', '2026-01-01');
        """
    )
    conn.commit()
    conn.close()
    store = _store(sessions, a)
    scope_a = resolve_workspace_scope(a)
    assert scope_a is not None
    assert store.load("legacy") == [{"role": "user", "content": "velha"}]
    prov = store.get_provenance("legacy")
    assert prov is not None and prov["workspace_id"] is None
    assert store.list_sessions_for_workspace(scope_a.workspace_id) == []


# --- feedback estruturado --------------------------------------------------


def test_feedback_record_is_redacted_and_scoped(two_workspaces):
    a, _ = two_workspaces
    store = FeedbackStore.for_workspace(a)
    assert store is not None
    rec = store.record(
        rating="negativo",
        user_text=f"use a chave {SECRET} para deploy",
        assistant_text="feito",
        session_id="s1",
        turn_index=3,
        tool_names=["run_command", "run_command", "read_file"],
        model="m",
        machine_id="box",
        reason="muito verboso",
    )
    assert rec.rating == "negative"
    assert rec.workspace_id == store.scope.workspace_id
    assert rec.redaction == "redacted"
    assert SECRET not in rec.user_excerpt
    assert rec.tool_names == ["run_command", "read_file"]
    assert rec.user_digest and len(rec.user_digest) == 64
    raw = store.db_path.read_bytes()
    assert SECRET.encode() not in raw
    assert store.list(rating="negative")[0].feedback_id == rec.feedback_id


def test_feedback_invalid_session_id_is_dropped(two_workspaces):
    a, _ = two_workspaces
    store = FeedbackStore.for_workspace(a)
    assert store is not None
    rec = store.record(rating="positive", session_id="../../etc/passwd")
    assert rec.session_id == ""


def test_feedback_rejects_unknown_rating(two_workspaces):
    a, _ = two_workspaces
    store = FeedbackStore.for_workspace(a)
    assert store is not None
    with pytest.raises(ValueError):
        store.record(rating="maybe")


def test_feedback_never_crosses_workspaces(two_workspaces):
    a, b = two_workspaces
    store_a = FeedbackStore.for_workspace(a)
    store_b = FeedbackStore.for_workspace(b)
    assert store_a is not None and store_b is not None
    store_a.record(rating="negative", user_text="só de A", session_id="s1")
    assert store_b.list() == []
    # Mesmo copiando o banco de A para dentro de B, as linhas não pertencem a B.
    store_b.scope.ensure_state_dir()
    shutil.copy(store_a.db_path, store_b.db_path)
    assert store_b.list() == []
    assert store_b.get(store_a.list()[0].feedback_id) is None


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="sem symlink")
def test_feedback_store_refuses_symlinked_db(two_workspaces, tmp_path):
    a, _ = two_workspaces
    store = FeedbackStore.for_workspace(a)
    assert store is not None
    store.scope.ensure_state_dir()
    outside = tmp_path / "outside.sqlite3"
    outside.write_bytes(b"")
    try:
        store.db_path.symlink_to(outside)
    except OSError:
        pytest.skip("symlink indisponível nesta plataforma")
    with pytest.raises(ScopeViolationError):
        store.record(rating="positive")


def test_snapshot_last_turn_collects_tools_and_index():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "primeira"},
        {"role": "assistant", "content": "r1"},
        {"role": "user", "content": "segunda"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read_file"}}]},
        {"role": "tool", "name": "read_file", "content": "conteúdo cru"},
        {"role": "assistant", "content": "resposta final"},
    ]
    turn = snapshot_last_turn(messages)
    assert turn.turn_index == 2
    assert turn.user_text == "segunda"
    assert turn.assistant_text == "resposta final"
    assert turn.tool_names == ["read_file"]


def test_record_turn_feedback_ignores_invalid_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert record_turn_feedback(MagicMock(), rating="positive", messages=[], session_id="s") is None
    assert list(tmp_path.iterdir()) == []


def test_thumbsdown_in_agent_loop_records_structured_feedback(tmp_path, monkeypatch):
    """/thumbsdown <motivo> grava o sinal estruturado no workspace do router."""
    from rich.console import Console

    from bauer.agent import run_agent_session
    from bauer.tool_router import ToolRouter

    monkeypatch.chdir(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    router = ToolRouter(workspace=ws)
    client = MagicMock()
    client.chat_stream.side_effect = lambda *a, **k: iter(["Resposta longa demais."])
    with patch("builtins.input", side_effect=[
        f"meu token {SECRET}", "/thumbsdown muito verboso", EOFError,
    ]):
        run_agent_session(client, "test-model", 4096, Console(), router, session_id="sess-1")

    store = FeedbackStore.for_workspace(ws)
    assert store is not None
    records = store.list()
    assert len(records) == 1
    rec = records[0]
    assert rec.rating == "negative"
    assert rec.reason == "muito verboso"
    assert rec.session_id == "sess-1"
    assert rec.turn_index == 1
    assert SECRET not in rec.user_excerpt
    feedback_md = tmp_path / "memory" / "FEEDBACK.md"
    if feedback_md.exists():
        assert SECRET not in feedback_md.read_text(encoding="utf-8")
