"""Cross-process persistent guard at the authoritative runtime transaction."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest

from bauer.core.policy import ApprovalManager
from bauer.core.runtime.resilience import RuntimeRecovery
from bauer.core.runtime.run_manager import EffectBarrierPending, RunManager
from bauer.core.runtime.state_store import SqliteStateStore
from bauer.tool_gateway_endpoint import ToolCallLedger


@pytest.fixture
def runtime(tmp_path):
    root = tmp_path / "runtime"
    store = SqliteStateStore(root)
    ledger = ToolCallLedger(root / "runtime_state.sqlite3")
    store.activate_tool_gateway_guard(ledger.path)
    runs = RunManager(store=store)  # no in-process injection
    run = runs.create_run(session_id="session", agent_id="channel.telegram",
                          runtime_adapter="agentos_http", status="running")
    return root, store, ledger, runs, run


def _claim(ledger, run):
    assert ledger.claim("jwt", "digest", run_id=run.id, remote_run_id="remote", tool_call_id="call")


def test_new_manager_and_direct_transaction_cannot_bypass_guard(runtime):
    root, store, ledger, runs, run = runtime
    _claim(ledger, run)
    with pytest.raises(EffectBarrierPending):
        RunManager(root=root).cancel_run(run.id)
    assert runs.get_run(run.id).status == "running"
    with pytest.raises(EffectBarrierPending):
        with store.transaction() as tx:
            tx.upsert("runs", {**tx.latest("runs", run.id), "status": "failed"})
    assert runs.get_run(run.id).status == "running"
    ledger.settle("jwt", completed=True)
    assert RunManager(root=root).cancel_run(run.id).status == "cancelled"
    assert not ledger.claim("jwt2", "digest", run_id=run.id, remote_run_id="remote", tool_call_id="call2")


def test_approval_denial_cannot_mark_terminal_or_consume_while_effect_in_flight(runtime):
    root, _, ledger, runs, run = runtime
    approvals = ApprovalManager(root=root)
    _claim(ledger, run)
    record = approvals.request(operation="shell.execute", tool_name="run_command", reason="test",
                               risk_level="high", payload={"command": "echo x"}, run_id=run.id,
                               session_id=run.session_id, suspend_run=True)
    with pytest.raises(EffectBarrierPending):
        approvals.resolve_for_run(record.id, "denied", run_status="failed")
    assert approvals.get(record.id).status == "pending"
    assert runs.get_run(run.id).status == "waiting_approval"
    ledger.settle("jwt", completed=True)
    approvals.resolve_for_run(record.id, "denied", run_status="failed")
    assert runs.get_run(run.id).status == "failed"


def test_runtime_adapter_downgrade_and_missing_ledger_fail_closed(runtime):
    _, store, ledger, runs, run = runtime
    with pytest.raises(ValueError, match="cannot change runtime adapter"):
        runs.update_run(run.id, runtime_adapter="bauer_native")
    with pytest.raises(ValueError, match="cannot change runtime adapter"):
        with store.transaction() as tx:
            tx.upsert("runs", {**tx.latest("runs", run.id), "runtime_adapter": "agno"})
    with sqlite3.connect(ledger.path) as db:
        db.execute("DELETE FROM tool_gateway_meta WHERE key='ledger_id'")
    with pytest.raises(EffectBarrierPending):
        runs.cancel_run(run.id)
    assert runs.get_run(run.id).status == "running"


def test_recovery_skips_pending_run_and_recovers_other(runtime):
    root, _, ledger, runs, run = runtime
    other = runs.create_run(session_id="other", agent_id="channel.telegram",
                            runtime_adapter="agentos_http", status="running")
    _claim(ledger, run)
    recovered = RuntimeRecovery(root=root).recover_stuck_runs(max_age_s=-1)
    assert {entry["run_id"] for entry in recovered} == {other.id}
    assert runs.get_run(run.id).status == "running"
    ledger.settle("jwt", completed=True)
    assert RuntimeRecovery(root=root).recover_stuck_runs(max_age_s=-1)[0]["run_id"] == run.id


def test_rejected_compare_and_set_does_not_stop_active_run(runtime):
    _, _, ledger, runs, run = runtime
    unchanged = runs.update_run(run.id, status="failed", expected_status="queued")
    assert unchanged.status == "running"
    assert ledger.claim("jwt", "digest", run_id=run.id, remote_run_id="remote", tool_call_id="call")


@pytest.mark.parametrize("replacement", ["missing", "changed"])
def test_replaced_ledger_identity_cannot_silently_reactivate(runtime, replacement):
    _, _, ledger, runs, run = runtime
    with sqlite3.connect(ledger.path) as db:
        if replacement == "missing":
            db.execute("DELETE FROM tool_gateway_meta WHERE key='ledger_id'")
        else:
            db.execute("UPDATE tool_gateway_meta SET value=? WHERE key='ledger_id'", ("b" * 32,))
    with pytest.raises(EffectBarrierPending):
        runs.cancel_run(run.id)
    assert runs.get_run(run.id).status == "running"
    with pytest.raises((ValueError, OSError)):
        ledger.request_stop(run_id=run.id, reason="cancel")
    if replacement == "changed":
        reopened = ToolCallLedger(ledger.path, create=False)
        with pytest.raises(ValueError, match="identity changed"):
            reopened.claim("new-token", "digest", run_id=run.id,
                           remote_run_id="remote", tool_call_id="new-call")


def test_backup_contains_run_and_inflight_claim_in_same_snapshot(runtime, tmp_path):
    """Backing up the database cannot independently roll back its ledger."""
    _, store, ledger, _, run = runtime
    _claim(ledger, run)
    backup = tmp_path / "runtime-backup.sqlite3"
    with sqlite3.connect(ledger.path) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    assert ledger.path.resolve() == store.path.resolve()
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT state FROM tool_gateway_calls WHERE run_id=?", (run.id,)).fetchone() == ("CLAIMED",)
        assert db.execute("SELECT 1 FROM runtime_records WHERE collection='runs' AND record_id=?", (run.id,)).fetchone()


def test_independent_ledger_is_rejected(runtime, tmp_path):
    _, store, _, _, _ = runtime
    separate = ToolCallLedger(tmp_path / "independent.sqlite3")
    with pytest.raises(ValueError, match="invalid Tool Gateway ledger path"):
        store.activate_tool_gateway_guard(separate.path)


def test_claim_requires_active_governed_run_in_authoritative_transaction(runtime):
    _, _, ledger, runs, run = runtime
    assert not ledger.claim("missing", "digest", run_id="missing", remote_run_id="remote", tool_call_id="call")
    runs.update_run(run.id, status="waiting_approval")
    assert not ledger.claim("waiting", "digest", run_id=run.id, remote_run_id="remote", tool_call_id="call")
    runs.update_run(run.id, status="running")
    assert ledger.claim("allowed", "digest", run_id=run.id, remote_run_id="remote", tool_call_id="call")


def test_stop_intent_retains_writer_lock_after_rollback(runtime, monkeypatch):
    _, _, ledger, runs, run = runtime
    _claim(ledger, run)
    original = ToolCallLedger.request_stop_in_transaction
    at_reapply, release = threading.Event(), threading.Event()
    calls = 0

    def paused_reapply(db, *, run_id, reason):
        nonlocal calls
        calls += 1
        if calls == 2:
            at_reapply.set()
            assert release.wait(timeout=10)
        return original(db, run_id=run_id, reason=reason)

    monkeypatch.setattr(ToolCallLedger, "request_stop_in_transaction", staticmethod(paused_reapply))
    with ThreadPoolExecutor(max_workers=2) as pool:
        cancelling = pool.submit(runs.cancel_run, run.id)
        try:
            assert at_reapply.wait(timeout=10)
            competing = pool.submit(ledger.claim, "jwt2", "digest", run_id=run.id,
                                    remote_run_id="remote", tool_call_id="call2")
            with pytest.raises(TimeoutError):
                competing.result(timeout=0.2)
        finally:
            release.set()
        with pytest.raises(EffectBarrierPending):
            cancelling.result(timeout=10)
        assert competing.result(timeout=10) is False
