"""Cross-process barrier contention; does not start a network service."""
from multiprocessing import get_context
from pathlib import Path

from bauer.tool_gateway_endpoint import ToolCallLedger


def _effect(path: str, start, results):
    ledger = ToolCallLedger(Path(path))
    start.wait(timeout=20)
    results.put(("effect", ledger.claim("jwt", "digest", run_id="run", remote_run_id="remote", tool_call_id="call")))


def _cancel(path: str, start, results):
    ledger = ToolCallLedger(Path(path))
    start.wait(timeout=20)
    results.put(("cancel", ledger.request_stop(run_id="run", reason="cancel")))


def test_processes_serialize_effect_and_cancel(tmp_path):
    context = get_context("spawn")
    path = str(tmp_path / "barrier.db")
    ToolCallLedger(Path(path))
    start, results = context.Barrier(2), context.Queue()
    processes = [context.Process(target=fn, args=(path, start, results)) for fn in (_effect, _cancel)]
    for process in processes:
        process.start()
    try:
        outcomes = dict(results.get(timeout=30) for _ in processes)
        assert (outcomes["effect"], outcomes["cancel"]) in {(True, False), (False, True)}
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()
