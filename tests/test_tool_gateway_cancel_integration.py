"""The live Telegram cancellation helper respects an activated effect barrier."""
from types import SimpleNamespace
import sqlite3

import pytest

from bauer.channel_governance import runtime_root
from bauer.core.runtime.run_manager import EffectBarrierPending, RunManager
from bauer.core.runtime.state_store import SqliteStateStore
from bauer.tool_gateway_endpoint import ToolCallLedger


def _fake_remote(monkeypatch, run):
    import bauer.telegram_agentos_http as remote

    monkeypatch.setattr(remote, "_identity", lambda *_: ({"base_url": "http://example.invalid"}, "token", "owner"))
    monkeypatch.setattr(remote, "RemoteSessionStore", lambda *_: SimpleNamespace(
        current=lambda **__: SimpleNamespace(session_id="remote-session")))
    monkeypatch.setattr(remote, "RemoteRunStore", lambda *_, **__: SimpleNamespace(recoverable=lambda **___: [run]))
    called = []

    class Client:
        def __init__(self, *args, **kwargs):
            called.append(True)
            raise AssertionError("network must not be reached while an effect may be active")

    monkeypatch.setattr(remote, "AgentOSHttpClient", Client)
    return remote, called


def test_cancel_records_stop_before_remote_request_and_waits_for_inflight(tmp_path, monkeypatch):
    state_dir = tmp_path / "sessions"
    root = runtime_root(state_dir)
    state = SqliteStateStore(root)
    ledger = ToolCallLedger(root / "runtime_state.sqlite3")
    state.activate_tool_gateway_guard(ledger.path)
    actual = RunManager(store=state).create_run(session_id="session", agent_id="channel.telegram",
                                                runtime_adapter="agentos_http", status="running")
    assert ledger.claim("jwt", "digest", run_id=actual.id, remote_run_id="remote-run", tool_call_id="call")
    run = SimpleNamespace(gateway_run_id=actual.id, remote_run_id="remote-run", session_id="remote-session")
    remote, called = _fake_remote(monkeypatch, run)
    msg = SimpleNamespace(user_id="123", chat_id="123", project_snapshot=None)
    response = remote.cancel_remote_session(tmp_path / "config.yaml", state_dir, msg)
    assert "aguardando" in response.lower() and not called
    assert not ledger.claim("jwt2", "digest", run_id=actual.id, remote_run_id="remote-run", tool_call_id="call2")
    ledger.settle("jwt", completed=True)
    assert ledger.request_stop(run_id=actual.id, reason="cancel")


def test_cancel_fails_closed_if_activated_ledger_identity_disappears(tmp_path, monkeypatch):
    state_dir = tmp_path / "sessions"
    root = runtime_root(state_dir)
    state = SqliteStateStore(root)
    ledger = ToolCallLedger(root / "runtime_state.sqlite3")
    state.activate_tool_gateway_guard(ledger.path)
    with sqlite3.connect(ledger.path) as db:
        db.execute("DELETE FROM tool_gateway_meta WHERE key='ledger_id'")
    run = SimpleNamespace(gateway_run_id="gateway-run", remote_run_id="remote-run", session_id="remote-session")
    remote, called = _fake_remote(monkeypatch, run)
    msg = SimpleNamespace(user_id="123", chat_id="123", project_snapshot=None)
    with pytest.raises(EffectBarrierPending):
        remote.cancel_remote_session(tmp_path / "config.yaml", state_dir, msg)
    assert not called
