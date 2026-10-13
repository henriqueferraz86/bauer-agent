"""Hermetic regressions for functional Kernel/runtime audit findings."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import UTC, datetime, timedelta
import multiprocessing

import pytest

from bauer.core.kernel import BauerKernel, KernelRequest
from bauer.core.kernel.evaluator import Evaluator, GateResult
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.autonomy import BudgetExceededError, BudgetManager
from bauer.core.runtime.resilience import RunHeartbeat, RuntimeRecovery


@pytest.mark.parametrize("with_gates", [False, True])
def test_cancel_during_executor_preserves_terminal(tmp_path, with_gates):
    runs = RunManager(root=tmp_path)
    gates_called = []

    class Gate:
        name = "quality"

        def check(self, **kwargs):
            gates_called.append(True)
            return GateResult(self.name, True)

    kernel = BauerKernel(runs=runs, heartbeat_interval_s=0,
                         evaluator=Evaluator([Gate()]) if with_gates else None)

    def executor(payload):
        runs.cancel_run(payload["run_id"])
        return {"output": "late success"}

    result = kernel.execute(KernelRequest(task="local"), executor=executor)
    assert result.status == "cancelled"
    assert not gates_called
    terminals = [event.event_type for event in runs.event_bus.list_events(run_id=result.run_id)
                 if event.event_type in {"run.completed", "run.failed", "run.cancelled"}]
    assert terminals == ["run.cancelled"]


@pytest.mark.parametrize("passed", [False, True])
def test_cancel_during_gate_does_not_replan_or_complete(tmp_path, passed):
    runs = RunManager(root=tmp_path)
    calls = []

    class Gate:
        name = "quality"

        def check(self, **kwargs):
            runs.cancel_run(calls[-1])
            return GateResult(self.name, passed, "gate verdict")

    kernel = BauerKernel(runs=runs, heartbeat_interval_s=0,
                         evaluator=Evaluator([Gate()], max_replans=1))

    def executor(payload):
        calls.append(payload["run_id"])
        return {"output": "ok"}

    result = kernel.execute(KernelRequest(task="local"), executor=executor)
    assert result.status == "cancelled"
    assert len(calls) == 1


def test_cancel_during_failed_attempt_prevents_retry(tmp_path):
    runs = RunManager(root=tmp_path)
    calls = []

    def executor(payload):
        calls.append(True)
        runs.cancel_run(payload["run_id"])
        return {"status": "failed", "error": "late failure"}

    result = BauerKernel(runs=runs, heartbeat_interval_s=0).execute(
        KernelRequest(task="local", max_retries=1), executor=executor)
    assert result.status == "cancelled"
    assert len(calls) == 1


def test_two_managers_publish_only_one_terminal(tmp_path):
    first = RunManager(root=tmp_path)
    second = RunManager(root=tmp_path)
    run = first.create_run(session_id="local", status="running")
    ready = Barrier(2)

    def finish():
        ready.wait(timeout=5)
        return first.complete_run(run.id, output={"output": "done"}).status

    def cancel():
        ready.wait(timeout=5)
        return second.cancel_run(run.id).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(finish), pool.submit(cancel)]
        statuses = [future.result(timeout=10) for future in futures]

    assert statuses[0] == statuses[1] == first.get_run(run.id).status
    terminals = [event for event in first.event_bus.list_events(run_id=run.id)
                 if event.event_type in {"run.completed", "run.cancelled"}]
    assert len(terminals) == 1
    first.update_run(run.id, elapsed_seconds=0.2)
    assert len([event for event in first.event_bus.list_events(run_id=run.id)
                if event.event_type in {"run.completed", "run.cancelled"}]) == 1


@pytest.mark.parametrize("status", ["failed", "cancelled", "gate_failed"])
def test_known_cost_is_settled_for_every_outcome(tmp_path, status):
    runs = RunManager(root=tmp_path)
    budget = BudgetManager(store=runs.store)

    class Gate:
        name = "quality"

        def check(self, **kwargs):
            return GateResult(self.name, False, "invalid output")

    kernel = BauerKernel(runs=runs, budget=budget, heartbeat_interval_s=0,
                         evaluator=Evaluator([Gate()], max_replans=0)
                         if status == "gate_failed" else None)

    def executor(payload):
        budget.ensure_can_start(run_id=payload["run_id"], estimated_cost_usd=0.1)
        return {"status": "completed" if status == "gate_failed" else status,
                "output": "ok", "cost_usd": 0.4}

    result = kernel.execute(KernelRequest(task="local"), executor=executor)
    assert result.status == ("failed" if status == "gate_failed" else status)
    assert runs.get_run(result.run_id).cost_estimate == pytest.approx(0.4)
    assert budget.status()["daily"]["used_usd"] == pytest.approx(0.4)


@pytest.mark.parametrize("mode", ["retry", "replan", "fallback"])
def test_attempt_metrics_are_incremental_and_settled_once(tmp_path, mode):
    runs = RunManager(root=tmp_path)
    budget = BudgetManager(store=runs.store)
    calls = []

    def executor(payload):
        calls.append(True)
        first = len(calls) == 1
        return {"status": "failed" if first and mode != "replan" else "completed",
                "output": "first" if first else "second", "cost_usd": 0.4 if first else 0.3,
                "tool_calls_count": 2 if first else 3, "llm_calls_count": 1 if first else 2,
                "usage_known": not first}

    class Gate:
        name = "quality"

        def check(self, **kwargs):
            return GateResult(self.name, len(calls) > 1, "try again")

    class Adapter:
        name = "fallback"
        run_agent = staticmethod(executor)

    kernel = BauerKernel(runs=runs, budget=budget, heartbeat_interval_s=0,
                         adapter_factory=lambda *args, **kwargs: Adapter(),
                         evaluator=Evaluator([Gate()], max_replans=1) if mode == "replan" else None)
    request = KernelRequest(task="local", max_retries=1 if mode == "retry" else 0,
                            fallback_adapters=["fallback"] if mode == "fallback" else [])
    result = kernel.execute(request, executor=executor)
    run = runs.get_run(result.run_id)
    assert result.status == "completed"
    assert len(calls) == 2
    assert run.cost_estimate == pytest.approx(0.7)
    assert run.tool_calls_count == 5
    assert run.llm_calls_count == 3
    assert run.usage_known is False
    assert budget.status()["daily"]["used_usd"] == pytest.approx(0.7)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
def test_reported_elapsed_does_not_erase_gate_duration(tmp_path, monkeypatch, stream, cancel):
    from bauer.core.kernel import kernel as kernel_module

    runs = RunManager(root=tmp_path)
    clock = [0.0]
    monkeypatch.setattr(kernel_module.time, "monotonic", lambda: clock[0])

    class Gate:
        name = "quality"

        def check(self, **kwargs):
            clock[0] += 2.0
            if cancel:
                runs.cancel_run(runs.list_runs()[0].id)
            return GateResult(self.name, True)

    kernel = BauerKernel(runs=runs, heartbeat_interval_s=0, evaluator=Evaluator([Gate()]))
    if stream:
        def executor(payload):
            yield {"event": "message.delta", "content": "ok"}
            yield {"event": "run.completed", "elapsed_seconds": 0.001}

        result = list(kernel.stream(KernelRequest(task="local"), executor=executor))[-1]["run"]
    else:
        result = kernel.execute(KernelRequest(task="local"),
                                executor=lambda payload: {"output": "ok", "elapsed_seconds": 0.001})
    assert result.status == ("cancelled" if cancel else "completed")
    assert runs.get_run(result.run_id).elapsed_seconds == 2.0


@pytest.mark.parametrize("heartbeat_fresh", [False, True])
def test_admission_and_recovery_share_liveness(tmp_path, heartbeat_fresh):
    runs = RunManager(root=tmp_path)
    budget = BudgetManager(store=runs.store)
    budget.set_profile(max_parallel_runs=1)
    run = runs.create_run(session_id="old", status="running")
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    runs.store.upsert("runs", {**run.__dict__, "updated_at": old})
    runs.store.upsert(RunHeartbeat.COLECAO, {
        "id": run.id, "last_seen_at": datetime.now(UTC).isoformat() if heartbeat_fresh else old,
        "pid": 1,
    })
    if heartbeat_fresh:
        with pytest.raises(BudgetExceededError, match="max parallel runs"):
            budget.ensure_can_start()
    else:
        budget.ensure_can_start()
    recovered = RuntimeRecovery(store=runs.store).recover_stuck_runs()
    assert bool(recovered) is not heartbeat_fresh


@pytest.mark.parametrize("status", ["paused", "waiting_approval"])
def test_intentional_wait_keeps_parallel_slot(tmp_path, status):
    runs = RunManager(root=tmp_path)
    budget = BudgetManager(store=runs.store)
    budget.set_profile(max_parallel_runs=1)
    run = runs.create_run(session_id="waiting", status=status)
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    runs.store.upsert("runs", {**run.__dict__, "updated_at": old})
    with pytest.raises(BudgetExceededError, match="max parallel runs"):
        budget.ensure_can_start()


@pytest.mark.parametrize("limit", [1, 2])
def test_concurrent_candidates_reserve_exact_parallel_capacity(tmp_path, limit):
    runs = RunManager(root=tmp_path)
    budget = BudgetManager(store=runs.store)
    budget.set_profile(max_parallel_runs=limit)
    candidates = [runs.create_run(session_id=f"candidate-{i}", status="policy_check")
                  for i in range(4)]
    ready = Barrier(len(candidates))

    def admit(run):
        manager = BudgetManager(root=tmp_path)
        ready.wait(timeout=10)
        try:
            manager.ensure_can_start(run_id=run.id)
            return True
        except BudgetExceededError:
            return False

    with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
        accepted = list(pool.map(admit, candidates))
    assert sum(accepted) == limit
    for run, admitted in zip(candidates, accepted):
        if admitted:
            budget.ensure_can_start(run_id=run.id)  # admission is idempotent
        runs.cancel_run(run.id)
    replacement = runs.create_run(session_id="replacement", status="policy_check")
    budget.ensure_can_start(run_id=replacement.id)


def _process_admission(root, run_id, ready, result):
    manager = BudgetManager(root=root)
    ready.wait(timeout=20)
    try:
        manager.ensure_can_start(run_id=run_id)
        result.put(True)
    except BudgetExceededError:
        result.put(False)


def test_parallel_slot_reservation_serializes_processes(tmp_path):
    runs = RunManager(root=tmp_path)
    BudgetManager(store=runs.store).set_profile(max_parallel_runs=1)
    candidates = [runs.create_run(session_id=f"process-{i}", status="policy_check") for i in range(2)]
    context = multiprocessing.get_context("spawn")
    ready = context.Barrier(2)
    result = context.Queue()
    processes = [context.Process(target=_process_admission, args=(tmp_path, run.id, ready, result))
                 for run in candidates]
    try:
        for process in processes:
            process.start()
        accepted = [result.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert sum(accepted) == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        result.close()


@pytest.mark.parametrize("stream", [False, True])
def test_external_cancel_still_settles_late_usage(tmp_path, stream):
    runs = RunManager(root=tmp_path)
    budget = BudgetManager(store=runs.store)
    kernel = BauerKernel(runs=runs, budget=budget, heartbeat_interval_s=0)

    if stream:
        def executor(payload):
            runs.cancel_run(payload["run_id"])
            yield {"event": "run.completed", "cost_usd": 0.4, "tool_calls_count": 2}

        result = list(kernel.stream(KernelRequest(task="local"), executor=executor))[-1]["run"]
    else:
        def executor(payload):
            runs.cancel_run(payload["run_id"])
            return {"output": "late", "cost_usd": 0.4, "tool_calls_count": 2}

        result = kernel.execute(KernelRequest(task="local"), executor=executor)
    assert result.status == "cancelled"
    assert budget.status()["daily"]["used_usd"] == pytest.approx(0.4)
    assert runs.get_run(result.run_id).tool_calls_count == 2


def test_older_cost_snapshot_cannot_reduce_cumulative_settlement(tmp_path):
    budget = BudgetManager(root=tmp_path)
    budget.record_run_cost(run_id="run-local", agent_id="default", cost_usd=0.7)
    budget.record_run_cost(run_id="run-local", agent_id="default", cost_usd=0.4)
    assert budget.status()["daily"]["used_usd"] == pytest.approx(0.7)
    assert len(budget.store.list("run_costs")) == 1


@pytest.mark.parametrize("stream", [False, True])
def test_cancel_at_start_does_not_invoke_executor(tmp_path, monkeypatch, stream):
    runs = RunManager(root=tmp_path)
    original_start = runs.start_run
    calls = []

    def start(run_id):
        runs.cancel_run(run_id)
        return original_start(run_id)

    monkeypatch.setattr(runs, "start_run", start)
    kernel = BauerKernel(runs=runs, heartbeat_interval_s=0)
    if stream:
        def executor(payload):
            calls.append(True)
            yield {"event": "message.delta", "content": "should not run"}

        result = list(kernel.stream(KernelRequest(task="local"), executor=executor))[-1]["run"]
    else:
        def executor(payload):
            calls.append(True)
            return {"output": "should not run"}

        result = kernel.execute(KernelRequest(task="local"), executor=executor)
    assert result.status == "cancelled"
    assert calls == []


def test_stream_disconnect_preserves_known_usage_and_duration(tmp_path, monkeypatch):
    from bauer.core.kernel import kernel as kernel_module

    runs = RunManager(root=tmp_path)
    budget = BudgetManager(store=runs.store)
    clock = [0.0]
    monkeypatch.setattr(kernel_module.time, "monotonic", lambda: clock[0])

    def executor(payload):
        yield {"event": "run.started", "cost_usd": 0.4, "tool_calls_count": 2}
        clock[0] = 2.0
        yield {"event": "message.delta", "content": "partial"}

    generator = BauerKernel(runs=runs, budget=budget, heartbeat_interval_s=0).stream(
        KernelRequest(task="local"), executor=executor)
    assert next(generator)["content"] == "partial"
    generator.close()
    run = runs.list_runs()[0]
    assert run.status == "cancelled"
    assert run.elapsed_seconds == 2.0
    assert run.tool_calls_count == 2
    assert budget.status()["daily"]["used_usd"] == pytest.approx(0.4)
