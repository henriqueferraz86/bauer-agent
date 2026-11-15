"""Unwired barrier contracts for cancellation and terminal transitions."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from bauer.tool_gateway_endpoint import ToolCallLedger


@pytest.mark.parametrize("reason", ["cancel", "kill_switch", "timeout", "terminal"])
def test_stop_is_sticky_while_effect_in_flight(tmp_path, reason):
    effect = ToolCallLedger(tmp_path / "gateway.db")
    control = ToolCallLedger(tmp_path / "gateway.db")
    assert effect.claim("token", "digest", run_id="run", remote_run_id="remote", tool_call_id="call")
    assert not control.request_stop(run_id="run", reason=reason)
    assert not control.claim("token-2", "digest", run_id="run", remote_run_id="remote", tool_call_id="call-2")
    effect.settle("token", completed=True)
    assert control.stop_reason("run") == reason
    assert control.request_stop(run_id="run", reason=reason)
    assert not control.claim("token-3", "digest", run_id="run", remote_run_id="remote", tool_call_id="call-3")


@pytest.mark.parametrize("reason", ["cancel", "kill_switch", "timeout", "terminal"])
def test_uncertain_effect_keeps_stop_blocked_after_restart(tmp_path, reason):
    ledger = ToolCallLedger(tmp_path / "gateway.db")
    assert ledger.claim("token", "digest", run_id="run", remote_run_id="remote", tool_call_id="call")
    assert not ledger.request_stop(run_id="run", reason=reason)
    ledger.settle("token", completed=False)
    restarted = ToolCallLedger(tmp_path / "gateway.db")
    assert not restarted.request_stop(run_id="run", reason=reason)
    assert not restarted.claim("token-2", "digest", run_id="run", remote_run_id="remote", tool_call_id="call-2")


def test_stop_and_effect_claim_never_both_win_across_instances(tmp_path):
    first = ToolCallLedger(tmp_path / "gateway.db")
    second = ToolCallLedger(tmp_path / "gateway.db")
    start = Barrier(2)

    def effect():
        start.wait()
        return first.claim("token", "digest", run_id="run", remote_run_id="remote", tool_call_id="call")

    def stop():
        start.wait()
        return second.request_stop(run_id="run", reason="kill_switch")

    with ThreadPoolExecutor(max_workers=2) as workers:
        first_result, second_result = workers.submit(effect), workers.submit(stop)
        assert (first_result.result(), second_result.result()) in {(True, False), (False, True)}
