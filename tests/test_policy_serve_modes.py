"""Modos de Policy Engine no serve (B0 — governança).

off / audit / enforce, mais a garantia de que `deny` bloqueia nos dois modos.
Usa delete_file (→ filesystem.delete, que as regras default classificam como
"ask") como operação sensível — não precisa de shell_runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.core.events import EventBus
from bauer.tool_router import ToolRouter, reset_runtime_ids, set_runtime_ids


@pytest.fixture(autouse=True)
def _runtime_ids():
    token = set_runtime_ids("sess-1", "run-1")
    yield
    reset_runtime_ids(token)


def _router(tmp_path: Path, *, enabled: bool, mode: str, rules_path: Path | None = None):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    r = ToolRouter(workspace=ws)
    r._event_bus = EventBus(root=tmp_path / "runtime")
    r._policy_root = tmp_path / "runtime"
    r._policy_enabled = enabled
    r._policy_mode = mode
    if rules_path is not None:
        r._policy_rules_path = rules_path
    return r, ws


def _policy_events(tmp_path: Path) -> list[str]:
    return [
        f"{e.event_type}:{e.status}"
        for e in EventBus(root=tmp_path / "runtime").list_events()
        if e.event_type.startswith("policy.") or e.event_type.startswith("approval.")
    ]


def test_off_mode_no_policy_and_executes(tmp_path):
    r, ws = _router(tmp_path, enabled=False, mode="audit")
    (ws / "x.txt").write_text("hi", encoding="utf-8")
    r.execute({"action": "delete_file", "args": {"path": "x.txt", "confirm": True}})
    assert not (ws / "x.txt").exists()           # executou
    assert _policy_events(tmp_path) == []         # nenhuma avaliação de policy


def test_audit_mode_executes_and_records(tmp_path):
    r, ws = _router(tmp_path, enabled=True, mode="audit")
    (ws / "x.txt").write_text("hi", encoding="utf-8")
    r.execute({"action": "delete_file", "args": {"path": "x.txt", "confirm": True}})
    assert not (ws / "x.txt").exists()           # audit NÃO bloqueia "ask"
    evs = _policy_events(tmp_path)
    assert "policy.evaluated:ask" in evs
    assert "approval.accepted:auto" in evs        # trilha de auto-aprovação


def test_enforce_mode_blocks_ask(tmp_path):
    from bauer.tools.base import ToolError

    r, ws = _router(tmp_path, enabled=True, mode="enforce")
    (ws / "y.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(ToolError, match="waiting_approval"):
        r.execute({"action": "delete_file", "args": {"path": "y.txt", "confirm": True}})
    assert (ws / "y.txt").exists()                # bloqueou → preservado
    evs = _policy_events(tmp_path)
    assert "policy.evaluated:ask" in evs
    assert "approval.requested:pending" in evs


def test_serve_default_audit_mode_wires_policy_and_report_shows_ask(tmp_path):
    """Integração B0: o serve default (audit) liga a policy no router; um turno
    que roda uma op sensível ("ask") executa E deixa rastro que o `audit report`
    contabiliza (policy_ask >= 1)."""
    from unittest.mock import MagicMock, patch
    from fastapi.testclient import TestClient

    from bauer.server import create_app
    from bauer.core.audit import build_report

    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "alvo.txt").write_text("apagar", encoding="utf-8")

    turns = [
        iter(['{"action": "delete_file", "args": {"path": "alvo.txt", "confirm": true}}']),
        iter(["Pronto, apaguei o arquivo."]),
    ]
    mock_client = MagicMock()
    mock_client.chat_stream.side_effect = lambda *a, **k: turns.pop(0)

    # Isola o registry: sem isso o serve leria o ~/.bauer/projects.json real e
    # rodaria o turno na pasta de um projeto ativo da máquina, não em `ws`.
    with patch("bauer.projects_registry._DEFAULT_REGISTRY", tmp_path / "projects.json"):
        app = create_app(
            model_name="m", applied_context=4096, router=ToolRouter(workspace=ws),
            client=mock_client, system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        resp = TestClient(app).get("/stream?message=apaga o alvo.txt")
    assert resp.status_code == 200

    assert not (ws / "alvo.txt").exists()  # audit mode não bloqueou a exclusão

    report = build_report(tmp_path / "runtime")
    assert report.policy_ask >= 1          # a decisão "ask" foi contabilizada


def test_deny_blocks_in_audit_mode(tmp_path):
    """deny é segurança real — bloqueia mesmo em audit."""
    from bauer.tools.base import ToolError

    rules = tmp_path / "policy.yaml"
    rules.write_text(
        "rules:\n"
        "  - id: fs.delete.deny\n"
        "    operation: filesystem.delete\n"
        "    action: deny\n"
        "    reason: exclusao proibida por politica\n",
        encoding="utf-8",
    )
    r, ws = _router(tmp_path, enabled=True, mode="audit", rules_path=rules)
    (ws / "z.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(ToolError, match="policy denied"):
        r.execute({"action": "delete_file", "args": {"path": "z.txt", "confirm": True}})
    assert (ws / "z.txt").exists()                # deny bloqueia mesmo em audit
    assert "approval.denied:denied" in _policy_events(tmp_path)
