"""A separate Gateway worker and Kernel writer share persistent stop intent."""
from multiprocessing import get_context
from pathlib import Path

import pytest

from bauer.core.runtime.run_manager import EffectBarrierPending, RunManager
from bauer.core.runtime.state_store import SqliteStateStore
from bauer.tool_gateway_endpoint import ToolCallLedger


def _effect(path: str, run_id: str, started, release, finished):
    ledger = ToolCallLedger(Path(path))
    if ledger.claim("jwt", "digest", run_id=run_id, remote_run_id="remote", tool_call_id="call"):
        started.set()
        if release.wait(timeout=20):
            ledger.settle("jwt", completed=True)
            finished.set()


def test_terminal_waits_for_effect_in_separate_process(tmp_path):
    context = get_context("spawn")
    root = tmp_path / "runtime"
    store = SqliteStateStore(root)
    ledger = ToolCallLedger(root / "runtime_state.sqlite3")
    store.activate_tool_gateway_guard(ledger.path)
    runs = RunManager(store=store)
    run = runs.create_run(session_id="session", agent_id="channel.telegram",
                          runtime_adapter="agentos_http", status="running")
    started, release, finished = context.Event(), context.Event(), context.Event()
    process = context.Process(target=_effect, args=(str(ledger.path), run.id, started, release, finished))
    process.start()
    try:
        assert started.wait(timeout=20)
        with pytest.raises(EffectBarrierPending):
            RunManager(root=root).cancel_run(run.id)
        assert runs.get_run(run.id).status == "running"
        release.set()
        assert finished.wait(timeout=20)
        assert RunManager(root=root).cancel_run(run.id).status == "cancelled"
        assert not ledger.claim("jwt2", "digest", run_id=run.id, remote_run_id="remote", tool_call_id="call2")
        process.join(timeout=10)
        assert process.exitcode == 0
    finally:
        release.set()
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
