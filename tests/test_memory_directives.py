"""Sprint 68 (plano 065): diretivas de feedback com aprovação humana."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bauer.memory_directives import (
    DirectiveError,
    DirectiveStore,
    directives_prompt_block,
    fingerprint,
)
from bauer.memory_feedback import FeedbackStore

NOW = datetime.now(timezone.utc)
SECRET = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWX1234567890"


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


def _signal(workspace: Path, session: str, *, rating: str = "negative",
            reason: str = "respostas longas demais", days_ago: float = 0, **kw):
    store = FeedbackStore.for_workspace(workspace)
    assert store is not None
    created = (NOW - timedelta(days=days_ago)).isoformat()
    return store.record(rating=rating, session_id=session, reason=reason,
                        created_at=created, **kw)


def _store(workspace: Path) -> DirectiveStore:
    store = DirectiveStore.for_workspace(workspace)
    assert store is not None
    return store


def _prompt(workspace: Path) -> str:
    # Sprint 70: as diretivas saíram do bloco de abertura do
    # LocalMemoryProvider e são relidas a cada payload (memory_injection).
    from bauer.memory_injection import workspace_memory_block

    return workspace_memory_block(workspace)


def test_fingerprint_is_stable_for_same_reason(ws):
    a = _signal(ws, "s1", reason="Respostas LONGAS demais!")
    b = _signal(ws, "s2", reason="demais longas respostas")
    c = _signal(ws, "s3", reason="usa inglês")
    assert fingerprint(a) == fingerprint(b)
    assert fingerprint(a) != fingerprint(c)


def test_promotion_requires_three_signals(ws):
    _signal(ws, "s1")
    _signal(ws, "s2")
    assert _store(ws).propose() == []
    _signal(ws, "s2")
    drafts = _store(ws).propose()
    assert len(drafts) == 1
    assert drafts[0].status == "draft"
    assert drafts[0].kind == "avoid"
    assert drafts[0].text == "Evite: respostas longas demais"
    assert len(drafts[0].supporting_feedback_ids) == 3
    assert drafts[0].supporting_sessions == ["s1", "s2"]


def test_promotion_requires_two_distinct_sessions(ws):
    for _ in range(4):
        _signal(ws, "s1")
    assert _store(ws).propose() == []


def test_signals_older_than_30_days_decay(ws):
    _signal(ws, "s1", days_ago=40)
    _signal(ws, "s2", days_ago=35)
    _signal(ws, "s3")
    assert _store(ws).propose() == []


def test_positive_signals_become_prefer_drafts(ws):
    for sid in ("s1", "s2", "s2"):
        _signal(ws, sid, rating="positive", reason="exemplos com código")
    drafts = _store(ws).propose()
    assert [d.kind for d in drafts] == ["prefer"]
    assert drafts[0].text.startswith("Prefira:")


def test_draft_is_never_injected_until_approved(ws):
    for sid in ("s1", "s2", "s3"):
        _signal(ws, sid)
    draft = _store(ws).propose()[0]
    assert "respostas longas" not in _prompt(ws)
    assert directives_prompt_block(ws) == ""
    _store(ws).approve(draft.directive_id, ttl_days=30)
    block = _prompt(ws)
    assert "Evite: respostas longas demais" in block
    assert draft.directive_id in block


def test_revoked_directive_disappears_from_prompt(ws):
    for sid in ("s1", "s2", "s3"):
        _signal(ws, sid)
    draft = _store(ws).propose()[0]
    _store(ws).approve(draft.directive_id)
    assert "Evite:" in _prompt(ws)
    _store(ws).revoke(draft.directive_id)
    assert "Evite:" not in _prompt(ws)
    assert _store(ws).get(draft.directive_id).status == "revoked"


def test_expired_directive_disappears_from_prompt(ws):
    for sid in ("s1", "s2", "s3"):
        _signal(ws, sid)
    draft = _store(ws).propose()[0]
    _store(ws).approve(draft.directive_id, ttl_days=1, now=NOW - timedelta(days=2))
    assert "Evite:" not in _prompt(ws)
    assert directives_prompt_block(ws, now=NOW - timedelta(days=1, hours=12))


def test_approval_is_individual_and_validated(ws):
    for sid in ("s1", "s2", "s3"):
        _signal(ws, sid)
        _signal(ws, sid, reason="ignora os testes")
    drafts = _store(ws).propose()
    assert len(drafts) == 2
    _store(ws).approve(drafts[0].directive_id)
    active = _store(ws).active()
    assert [d.directive_id for d in active] == [drafts[0].directive_id]
    with pytest.raises(DirectiveError):
        _store(ws).approve(drafts[0].directive_id)  # já aprovada
    with pytest.raises(DirectiveError):
        _store(ws).approve(drafts[1].directive_id, text=f"use a chave {SECRET}")
    with pytest.raises(DirectiveError):
        _store(ws).approve(drafts[1].directive_id, ttl_days=0)
    with pytest.raises(DirectiveError):
        _store(ws).approve("dir-inexistente")


def test_rejected_draft_only_returns_with_new_signals(ws):
    for sid in ("s1", "s2", "s3"):
        _signal(ws, sid, days_ago=1)
    draft = _store(ws).propose(now=NOW)[0]
    _store(ws).reject(draft.directive_id, now=NOW)
    assert _store(ws).propose(now=NOW) == []
    for sid in ("s4", "s5", "s6"):
        store = FeedbackStore.for_workspace(ws)
        assert store is not None
        store.record(rating="negative", session_id=sid, reason="respostas longas demais",
                     created_at=(NOW + timedelta(minutes=1)).isoformat())
    again = _store(ws).propose(now=NOW + timedelta(minutes=2))
    assert len(again) == 1
    assert again[0].directive_id != draft.directive_id
    assert again[0].status == "draft"
    statuses = sorted(d.status for d in _store(ws).load())
    assert statuses == ["draft", "rejected"]


def test_directives_never_cross_workspaces(ws, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    for sid in ("s1", "s2", "s3"):
        _signal(ws, sid)
    draft = _store(ws).propose()[0]
    _store(ws).approve(draft.directive_id)
    assert _store(other).propose() == []
    assert "Evite:" not in _prompt(other)
    # Copiar o arquivo de A para B não ativa nada em B.
    _store(other).scope.ensure_state_dir()
    (_store(other).path).write_bytes(_store(ws).path.read_bytes())
    assert _store(other).load() == []
    assert directives_prompt_block(other) == ""


def test_cli_flow_requires_explicit_approval(ws):
    from bauer.commands.memory_cmd import memory_app

    for sid in ("s1", "s2", "s3"):
        _signal(ws, sid)
    runner = CliRunner()
    res = runner.invoke(memory_app, ["directives", "propose", "--workspace", str(ws)])
    assert res.exit_code == 0, res.output
    draft = _store(ws).load()[0]
    res = runner.invoke(memory_app, ["directives", "approve", draft.directive_id,
                                     "--workspace", str(ws)], input="n\n")
    assert "Cancelado" in res.output
    assert _store(ws).active() == []
    res = runner.invoke(memory_app, ["directives", "approve", draft.directive_id,
                                     "--workspace", str(ws), "--ttl-days", "7", "--yes"])
    assert res.exit_code == 0, res.output
    assert len(_store(ws).active()) == 1
    res = runner.invoke(memory_app, ["directives", "show", draft.directive_id, "--workspace", str(ws)])
    assert all(fid in res.output for fid in draft.supporting_feedback_ids)
    res = runner.invoke(memory_app, ["directives", "revoke", draft.directive_id, "--workspace", str(ws)])
    assert res.exit_code == 0
    assert _store(ws).active() == []
