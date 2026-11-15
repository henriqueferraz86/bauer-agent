"""Opt-in run terminal guard; production is not wired to it yet."""
from pathlib import Path

import pytest

from bauer.core.runtime.run_manager import EffectBarrierPending, RunManager
from bauer.core.runtime.resilience import RuntimeRecovery
from bauer.execution_control import ExecutionControl, ExecutionControlError
from bauer.tool_gateway_endpoint import ToolCallLedger


def test_kernel_must_not_confirm_cancellation_while_effect_is_in_flight(tmp_path: Path):
    ledger = ToolCallLedger(tmp_path / "runtime" / "runtime_state.sqlite3")
    runs = RunManager(root=tmp_path / "runtime", effect_barrier=ledger)
    run = runs.create_run(session_id="session", agent_id="channel.telegram",
                          runtime_adapter="agentos_http", status="running")
    assert ledger.claim("jwt", "digest", run_id=run.id, remote_run_id="remote", tool_call_id="call")
    with pytest.raises(EffectBarrierPending):
        runs.cancel_run(run.id)
    assert runs.get_run(run.id).status != "cancelled"
    ledger.settle("jwt", completed=True)
    assert runs.cancel_run(run.id).status == "cancelled"


def test_kernel_terminal_must_block_later_effect_claim(tmp_path: Path):
    ledger = ToolCallLedger(tmp_path / "runtime" / "runtime_state.sqlite3")
    runs = RunManager(root=tmp_path / "runtime", effect_barrier=ledger)
    run = runs.create_run(session_id="session", agent_id="channel.telegram",
                          runtime_adapter="agentos_http", status="running")
    runs.cancel_run(run.id)
    assert not ledger.claim("jwt", "digest", run_id=run.id, remote_run_id="remote", tool_call_id="call")


@pytest.mark.parametrize("reason", ["cancelled", "kill_switch", "budget_exhausted"])
def test_execution_control_persists_stop_before_signalling(reason, tmp_path):
    ledger = ToolCallLedger(tmp_path / "runtime" / "tool-gateway.db")
    assert ledger.claim("jwt", "digest", run_id="run", remote_run_id="remote", tool_call_id="call")
    control = ExecutionControl(run_id="run", effect_barrier=ledger)
    control.request_cancel(reason)
    assert control.cancel_event.is_set()
    assert not ledger.claim("jwt2", "digest", run_id="run", remote_run_id="remote", tool_call_id="call2")
    ledger.settle("jwt", completed=True)
    assert ledger.request_stop(run_id="run", reason="cancel")


def test_barrier_failure_still_signals_stop_but_is_not_silenced():
    class Broken:
        def request_stop(self, **kwargs):
            raise OSError("unavailable")

    control = ExecutionControl(run_id="run", effect_barrier=Broken())
    with pytest.raises(ExecutionControlError):
        control.request_cancel()
    assert control.cancel_event.is_set()


def test_recovery_cannot_mark_terminal_while_effect_in_flight(tmp_path):
    root = tmp_path / "runtime"
    ledger = ToolCallLedger(root / "runtime_state.sqlite3")
    runs = RunManager(root=root, effect_barrier=ledger)
    run = runs.create_run(session_id="session", agent_id="channel.telegram",
                          runtime_adapter="agentos_http", status="running")
    assert ledger.claim("jwt", "digest", run_id=run.id, remote_run_id="remote", tool_call_id="call")
    recovery = RuntimeRecovery(root=root, effect_barrier=ledger)
    assert recovery.recover_stuck_runs(max_age_s=-1) == []
    assert runs.get_run(run.id).status == "running"
    ledger.settle("jwt", completed=True)
    assert recovery.recover_stuck_runs(max_age_s=-1)[0]["status"] == "failed"
