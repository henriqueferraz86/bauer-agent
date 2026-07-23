"""Tests for durable orchestration execution engine."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from bauer.execution_engine import DurableDAGExecutionEngine
from bauer.kanban_store import KanbanStore
from bauer.orchestration_store import OrchestrationStore
from bauer.orchestrator import StepResult
from bauer.workspace_manager_factory import get_workspace_manager


def _steps() -> list[dict]:
    return [
        {"id": 1, "goal": "step one", "tools": False, "depends_on": [], "agent": ""},
        {"id": 2, "goal": "step two", "tools": False, "depends_on": [1], "agent": ""},
    ]


def _fake_orchestrator(steps: list[dict] | None = None):
    plan = steps or _steps()
    orch = MagicMock()
    orch.plan.return_value = plan
    orch._topological_batches.return_value = [[step] for step in plan]
    orch.synthesize.side_effect = lambda _objective, results: "final: " + ",".join(str(r.id) for r in results)

    def execute_parallel_steps(batch, previous_results):
        return [
            StepResult(
                id=step["id"],
                goal=step["goal"],
                model_used="phi4-mini",
                response=f"ok {step['id']}",
                tool_log=[],
            )
            for step in batch
        ]

    orch.execute_parallel_steps.side_effect = execute_parallel_steps
    orch._execute_step_with_retry.side_effect = lambda step, _previous_results: StepResult(
        id=step["id"],
        goal=step["goal"],
        model_used="phi4-mini",
        response=f"ok {step['id']}",
        tool_log=[],
    )
    return orch


def test_durable_engine_persists_run_and_nodes(tmp_path: Path):
    orch = _fake_orchestrator()
    engine = DurableDAGExecutionEngine(orch, workspace=tmp_path, mode="hybrid")

    result = engine.run("build feature")

    store = OrchestrationStore(tmp_path)
    run = store.get_run(result.run_id)
    nodes = store.list_nodes(result.run_id)
    assert run is not None
    assert run.status == "succeeded"
    assert run.summary == "final: 1,2"
    assert [node.status for node in nodes] == ["succeeded", "succeeded"]
    assert nodes[0].response == "ok 1"


def test_durable_engine_resume_skips_succeeded_nodes(tmp_path: Path):
    steps = _steps()
    store = OrchestrationStore(tmp_path)
    run = store.create_run(run_id="orch-test", objective="resume me", mode="hybrid", plan=steps)
    store.upsert_planned_nodes(run.run_id, steps)
    store.update_node(run.run_id, 1, status="succeeded", model_used="phi4-mini", response="already done")

    orch = _fake_orchestrator(steps)
    engine = DurableDAGExecutionEngine(orch, workspace=tmp_path, mode="hybrid")
    result = engine.run("resume me", resume=True, run_id=run.run_id)

    executed_batches = [call.args[0] for call in orch.execute_parallel_steps.call_args_list]
    assert [[step["id"] for step in batch] for batch in executed_batches] == [[2]]
    assert result.run_id == run.run_id
    nodes = store.list_nodes(run.run_id)
    assert [node.status for node in nodes] == ["succeeded", "succeeded"]
    assert nodes[0].response == "already done"
    assert nodes[1].response == "ok 2"


def test_durable_engine_records_failed_node_status(tmp_path: Path):
    steps = [{"id": 1, "goal": "broken", "tools": False, "depends_on": [], "agent": ""}]
    orch = _fake_orchestrator(steps)
    orch._topological_batches.return_value = [[steps[0]]]
    orch.execute_parallel_steps.side_effect = None
    orch.execute_parallel_steps.return_value = [
        StepResult(id=1, goal="broken", model_used="(erro)", response="boom", tool_log=[]),
    ]

    engine = DurableDAGExecutionEngine(orch, workspace=tmp_path, mode="durable", node_runtime="inline")
    result = engine.run("broken flow")

    run = OrchestrationStore(tmp_path).get_run(result.run_id)
    nodes = OrchestrationStore(tmp_path).list_nodes(result.run_id)
    assert result.status == "failed"
    assert run is not None
    assert run.status == "failed"
    assert nodes[0].status == "failed"
    assert nodes[0].error == "boom"


def test_durable_dispatcher_runtime_materializes_nodes_as_kanban_tasks(tmp_path: Path):
    orch = _fake_orchestrator()
    engine = DurableDAGExecutionEngine(orch, workspace=tmp_path, mode="durable")

    result = engine.run("build through dispatcher")

    assert result.node_runtime == "dispatcher"
    assert orch.execute_parallel_steps.call_count == 0
    tasks = get_workspace_manager(tmp_path).list_tasks()
    nodes = OrchestrationStore(tmp_path).list_nodes(result.run_id)
    assert [task.status for task in tasks] == ["DONE", "DONE"]
    assert [node.status for node in nodes] == ["succeeded", "succeeded"]
    assert nodes[0].task_id == "001"
    assert nodes[0].dispatch_run_id
    assert tasks[0].metadata["orchestration_run"] == result.run_id
    assert tasks[0].metadata["orchestration_backend"] == "dispatcher"
    run = KanbanStore(tmp_path).latest_run_for_task("001")
    assert run is not None
    assert run.status == "succeeded"


def test_dispatcher_runtime_filters_claims_to_orchestration_tasks(tmp_path: Path):
    workspace = tmp_path
    wm = get_workspace_manager(workspace)
    wm.init_project("Scoped Dispatch")
    unrelated = wm.add_task("Unrelated READY", status="READY")

    orch = _fake_orchestrator([{"id": 1, "goal": "scoped step", "tools": False, "depends_on": [], "agent": ""}])
    engine = DurableDAGExecutionEngine(orch, workspace=workspace, mode="durable")

    result = engine.run("scoped orchestration")

    assert wm.get_task(unrelated.id).status == "READY"
    orch_task = next(task for task in wm.list_tasks() if task.metadata.get("orchestration_run") == result.run_id)
    assert orch_task.status == "DONE"


def test_background_submit_queues_ready_nodes_and_worker_advances_dag(tmp_path: Path):
    orch = _fake_orchestrator()
    engine = DurableDAGExecutionEngine(orch, workspace=tmp_path, mode="durable")

    submitted = engine.submit("background orchestration")

    wm = get_workspace_manager(tmp_path)
    store = OrchestrationStore(tmp_path)
    nodes = store.list_nodes(submitted.run_id)
    assert submitted.status == "running"
    assert [node.status for node in nodes] == ["queued", "planned"]
    first_task = wm.get_task(nodes[0].task_id)
    assert first_task.status == "READY"

    from bauer.task_dispatcher import TaskDispatcher

    dispatcher = TaskDispatcher(tmp_path)
    with dispatcher._lock():
        claimed_first = dispatcher._claim_locked(first_task)

    first = engine.execute_node(
        submitted.run_id,
        1,
        task_id=claimed_first.id,
        claim_id=claimed_first.metadata["claim_id"],
    )

    nodes = store.list_nodes(submitted.run_id)
    assert first.status == "succeeded"
    assert [node.status for node in nodes] == ["succeeded", "queued"]
    second_task = wm.get_task(nodes[1].task_id)
    assert second_task.status == "READY"

    with dispatcher._lock():
        claimed_second = dispatcher._claim_locked(second_task)

    second = engine.execute_node(
        submitted.run_id,
        2,
        task_id=claimed_second.id,
        claim_id=claimed_second.metadata["claim_id"],
    )

    run = store.get_run(submitted.run_id)
    assert second.orchestration_status == "succeeded"
    assert run is not None
    assert run.status == "succeeded"
    assert run.summary == "final: 1,2"
