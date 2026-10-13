"""K01: o candidato persistido pelo Kernel não ocupa uma vaga adicional."""

from pathlib import Path

import pytest

from bauer.core.kernel import BauerKernel, KernelRequest
from bauer.core.policy.engine import PolicyEngine
from bauer.core.runtime.autonomy import BudgetExceededError, BudgetManager
from bauer.core.runtime.run_manager import RunManager


@pytest.fixture
def kit(tmp_path: Path):
    root = tmp_path / "runtime"
    runs = RunManager(root=root)
    budget = BudgetManager(store=runs.store)
    policy = PolicyEngine(workspace=tmp_path, runtime_root=root, rules=[])
    kernel = BauerKernel(
        runs=runs, policy=policy, budget=budget, heartbeat_interval_s=0,
    )
    return kernel, runs, budget


@pytest.mark.parametrize("limit, existing", [(1, 0), (2, 0), (2, 1)])
def test_execute_uses_all_parallel_slots(kit, limit, existing):
    kernel, runs, budget = kit
    budget.set_profile(max_parallel_runs=limit)
    for i in range(existing):
        runs.create_run(session_id=f"existing-{i}", status="running")
    called = []

    def executor(payload):
        called.append(payload["run_id"])
        return {"output": "ok"}

    result = kernel.execute(KernelRequest(task="teste local"), executor=executor)

    assert result.status == "completed", result.error
    assert called == [result.run_id]
    assert runs.get_run(result.run_id).status == "completed"


@pytest.mark.parametrize("limit", [1, 2])
def test_admit_uses_all_slots_and_rejects_next_candidate(kit, limit):
    kernel, runs, budget = kit
    budget.set_profile(max_parallel_runs=limit)
    admitted = []

    for i in range(limit):
        run, early = kernel.admit(KernelRequest(task=f"teste local {i}"))
        assert early is None, early.error if early else ""
        assert run.status == "queued"
        admitted.append(run.id)

    rejected, early = kernel.admit(KernelRequest(task="sem vaga"))
    assert early is not None and early.status == "failed"
    assert f"max parallel runs reached: {limit}/{limit}" in early.error
    assert runs.get_run(rejected.id).status == "failed"
    assert all(runs.get_run(run_id).status == "queued" for run_id in admitted)


@pytest.mark.parametrize("limit", [1, 2])
def test_execute_at_capacity_does_not_call_executor(kit, limit):
    kernel, runs, budget = kit
    budget.set_profile(max_parallel_runs=limit)
    for i in range(limit):
        runs.create_run(session_id=f"existing-{i}", status="running")
    called = []

    result = kernel.execute(
        KernelRequest(task="sem vaga"),
        executor=lambda payload: called.append(payload) or {"output": "ok"},
    )

    assert result.status == "failed"
    assert f"max parallel runs reached: {limit}/{limit}" in result.error
    assert called == []


@pytest.mark.parametrize("candidate_id", [None, "not-persisted"])
def test_missing_candidate_does_not_exempt_other_runs(kit, candidate_id):
    _, runs, budget = kit
    budget.set_profile(max_parallel_runs=1)
    runs.create_run(session_id="existing", status="running")

    with pytest.raises(BudgetExceededError, match="max parallel runs reached: 1/1"):
        budget.ensure_can_start(run_id=candidate_id)
