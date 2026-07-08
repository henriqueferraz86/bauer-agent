"""Execution engines for orchestration modes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .orchestration_store import OrchestrationRun, OrchestrationStore
from .orchestrator import AgentOrchestrator, StepResult
from .indicators import show_step


@dataclass
class ExecutionEngineResult:
    run_id: str
    mode: str
    node_runtime: str
    final: str
    results: list[StepResult] = field(default_factory=list)
    status: str = "succeeded"


@dataclass
class NodeWorkerResult:
    run_id: str
    step_id: int
    task_id: str
    dispatch_run_id: str
    step_result: StepResult
    status: str
    orchestration_status: str
    final: str = ""


class DurableDAGExecutionEngine:
    """Durable run/node wrapper around the existing AgentOrchestrator DAG."""

    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        *,
        workspace: str | Path = "workspace",
        mode: str = "hybrid",
        node_runtime: str = "auto",
    ):
        self.orchestrator = orchestrator
        self.workspace = Path(workspace).resolve()
        self.mode = _normalize_mode(mode)
        self.node_runtime = _normalize_node_runtime(node_runtime, self.mode)
        self.store = OrchestrationStore(self.workspace)

    def run(
        self,
        objective: str,
        *,
        resume: bool = False,
        run_id: str = "",
        agents: list[Any] | None = None,
        specs: list[Any] | None = None,
    ) -> ExecutionEngineResult:
        orchestration_run = self._load_or_create_run(
            objective,
            resume=resume,
            run_id=run_id,
            agents=agents,
            specs=specs,
        )
        steps = orchestration_run.plan
        self.store.update_run(
            orchestration_run.run_id,
            status="running",
            metadata={"node_runtime": self.node_runtime},
        )

        try:
            all_results, had_errors = self._execute_steps(orchestration_run.run_id, steps)
            final = self.orchestrator.synthesize(steps[0].get("goal", objective) if steps else objective, all_results)
            final_status = "failed" if had_errors else "succeeded"
            self.store.update_run(orchestration_run.run_id, status=final_status, summary=final)
            return ExecutionEngineResult(
                run_id=orchestration_run.run_id,
                mode=self.mode,
                node_runtime=self.node_runtime,
                final=final,
                results=all_results,
                status=final_status,
            )
        except Exception as exc:
            self.store.update_run(orchestration_run.run_id, status="failed", error=str(exc))
            raise

    def resume(self, run_id: str) -> ExecutionEngineResult:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"orchestration run not found: {run_id}")
        return self.run(run.objective, resume=True, run_id=run.run_id)

    def submit(
        self,
        objective: str,
        *,
        resume: bool = False,
        run_id: str = "",
        agents: list[Any] | None = None,
        specs: list[Any] | None = None,
    ) -> ExecutionEngineResult:
        """Submit dependency-ready nodes to Kanban and return without executing them."""
        orchestration_run = self._load_or_create_run(
            objective,
            resume=resume,
            run_id=run_id,
            agents=agents,
            specs=specs,
        )
        self.store.update_run(
            orchestration_run.run_id,
            status="running",
            metadata={"node_runtime": "dispatcher", "background": True},
        )
        queued = self._queue_ready_nodes(orchestration_run.run_id, orchestration_run.plan)
        run = self.store.get_run(orchestration_run.run_id)
        status = run.status if run else "running"
        return ExecutionEngineResult(
            run_id=orchestration_run.run_id,
            mode=self.mode,
            node_runtime="dispatcher",
            final="",
            results=self._succeeded_results(orchestration_run.run_id),
            status=status if queued else status,
        )

    def advance(self, run_id: str) -> ExecutionEngineResult:
        """Advance a background dispatcher-backed DAG after one or more nodes changed."""
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"orchestration run not found: {run_id}")

        nodes = self.store.list_nodes(run_id)
        if any(node.status == "failed" for node in nodes):
            failed = next(node for node in nodes if node.status == "failed")
            updated = self.store.update_run(run_id, status="failed", error=failed.error or failed.response)
            return ExecutionEngineResult(
                run_id=run_id,
                mode=run.mode,
                node_runtime=self.node_runtime,
                final=updated.summary if updated else "",
                results=self._succeeded_results(run_id),
                status="failed",
            )

        results = self._succeeded_results(run_id)
        succeeded_ids = {result.id for result in results}
        step_ids = {int(step.get("id", 0)) for step in run.plan}
        if step_ids and step_ids.issubset(succeeded_ids):
            final = self.orchestrator.synthesize(run.objective, results)
            self.store.update_run(run_id, status="succeeded", summary=final)
            return ExecutionEngineResult(
                run_id=run_id,
                mode=run.mode,
                node_runtime=self.node_runtime,
                final=final,
                results=results,
                status="succeeded",
            )

        queued = self._queue_ready_nodes(run_id, run.plan)
        self.store.update_run(run_id, status="running", metadata={"queued_nodes": queued})
        return ExecutionEngineResult(
            run_id=run_id,
            mode=run.mode,
            node_runtime=self.node_runtime,
            final="",
            results=results,
            status="running",
        )

    def execute_node(
        self,
        run_id: str,
        step_id: int,
        *,
        task_id: str = "",
        claim_id: str = "",
    ) -> NodeWorkerResult:
        """Execute one persisted DAG node inside a dispatcher worker subprocess."""
        from .workspace_manager import WorkspaceManager

        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"orchestration run not found: {run_id}")
        step = next((item for item in run.plan if int(item.get("id", 0)) == int(step_id)), None)
        if step is None:
            raise ValueError(f"step {step_id} not found in orchestration run {run_id}")
        node = self.store.get_node(run_id, int(step_id))
        if node is None:
            raise ValueError(f"node {step_id} not found in orchestration run {run_id}")

        wm = WorkspaceManager(self.workspace)
        task_id = task_id or node.task_id
        dispatch_run_id = node.dispatch_run_id
        if task_id:
            task = wm.get_task(task_id)
            if claim_id and task.metadata.get("claim_id") != claim_id:
                raise ValueError("claim_id does not match orchestration node task")
            dispatch_run_id = task.metadata.get("run_id", "") or dispatch_run_id

        self.store.update_node(
            run_id,
            int(step_id),
            status="running",
            task_id=task_id,
            dispatch_run_id=dispatch_run_id,
        )
        previous_results = self._dependency_results(run_id, step)
        result = self.orchestrator._execute_step_with_retry(step, previous_results)
        failed = result.model_used == "(erro)"
        self.store.update_node(
            run_id,
            result.id,
            status="failed" if failed else "succeeded",
            task_id=task_id,
            dispatch_run_id=dispatch_run_id,
            model_used=result.model_used,
            response=result.response,
            tool_log=result.tool_log,
            error=result.response if failed else "",
        )
        if task_id and dispatch_run_id:
            try:
                wm.update_task_metadata(
                    task_id,
                    metadata={"orchestration_dispatch_run": dispatch_run_id},
                )
            except Exception:
                pass

        if failed:
            updated = self.store.update_run(run_id, status="failed", error=result.response)
            orchestration_status = updated.status if updated else "failed"
            final = updated.summary if updated else ""
        else:
            advanced = self.advance(run_id)
            orchestration_status = advanced.status
            final = advanced.final

        return NodeWorkerResult(
            run_id=run_id,
            step_id=int(step_id),
            task_id=task_id,
            dispatch_run_id=dispatch_run_id,
            step_result=result,
            status="failed" if failed else "succeeded",
            orchestration_status=orchestration_status,
            final=final,
        )

    def _load_or_create_run(
        self,
        objective: str,
        *,
        resume: bool,
        run_id: str,
        agents: list[Any] | None,
        specs: list[Any] | None,
    ) -> OrchestrationRun:
        if resume:
            existing = self.store.get_run(run_id) if run_id else self.store.latest_resumable_run(objective)
            if existing is not None:
                return existing

        steps = self.orchestrator.plan(objective, agents=agents, specs=specs)
        run_id = run_id or f"orch-{uuid.uuid4().hex[:12]}"
        created = self.store.create_run(
            run_id=run_id,
            objective=objective,
            mode=self.mode,
            plan=steps,
            status="planned",
            metadata={"node_runtime": self.node_runtime},
        )
        self.store.upsert_planned_nodes(run_id, steps)
        return created

    def _execute_steps(self, run_id: str, steps: list[dict[str, Any]]) -> tuple[list[StepResult], bool]:
        nodes = self.store.list_nodes(run_id)
        succeeded = {
            node.step_id: StepResult(
                id=node.step_id,
                goal=node.goal,
                model_used=node.model_used,
                response=node.response,
                tool_log=node.tool_log,
            )
            for node in nodes
            if node.status == "succeeded"
        }
        done: dict[int, StepResult] = dict(succeeded)
        all_results: list[StepResult] = [done[key] for key in sorted(done)]
        had_errors = False

        for batch in self.orchestrator._topological_batches(steps):
            pending = [step for step in batch if int(step["id"]) not in succeeded]
            if not pending:
                continue

            for step in pending:
                self.store.update_node(run_id, int(step["id"]), status="running")

            if self.node_runtime == "dispatcher":
                batch_results = self._execute_batch_via_dispatcher(run_id, pending, all_results)
            else:
                batch_results = self.orchestrator.execute_parallel_steps(pending, all_results)

            for result in batch_results:
                is_error = result.model_used == "(erro)"
                had_errors = had_errors or is_error
                self.store.update_node(
                    run_id,
                    result.id,
                    status="failed" if is_error else "succeeded",
                    model_used=result.model_used,
                    response=result.response,
                    tool_log=result.tool_log,
                    error=result.response if is_error else "",
                )
                done[result.id] = result
                if not is_error:
                    succeeded[result.id] = result
            all_results = [done[key] for key in sorted(done)]

        latest_nodes = self.store.list_nodes(run_id)
        had_errors = had_errors or any(node.status == "failed" for node in latest_nodes)
        return all_results, had_errors

    def _execute_batch_via_dispatcher(
        self,
        run_id: str,
        pending: list[dict[str, Any]],
        previous_results: list[StepResult],
    ) -> list[StepResult]:
        from .kanban_store import KanbanStore
        from .task_dispatcher import TaskDispatcher, WorkerResult
        from .workspace_manager import WorkspaceError, WorkspaceManager

        dispatcher = TaskDispatcher(self.workspace)
        wm = WorkspaceManager(self.workspace)
        task_ids: list[str] = []
        step_by_task: dict[str, dict[str, Any]] = {}

        for step in pending:
            task = self._ensure_node_task(run_id, step, wm)
            if task.status != "IN_PROGRESS":
                task = dispatcher.mark_ready(
                    task.id,
                    assignee=str(step.get("agent") or ""),
                    max_retries=1,
                )
            self.store.update_node(run_id, int(step["id"]), task_id=task.id)
            task_ids.append(task.id)
            step_by_task[task.id] = step

        captured: dict[int, StepResult] = {}

        def _worker(claimed):  # type: ignore[no-untyped-def]
            step = step_by_task.get(claimed.id)
            if step is None:
                return WorkerResult(False, error=f"Task {claimed.id} is not part of orchestration {run_id}.")
            step_id = int(step["id"])
            dispatch_run_id = claimed.metadata.get("run_id", "")
            self.store.update_node(
                run_id,
                step_id,
                status="running",
                task_id=claimed.id,
                dispatch_run_id=dispatch_run_id,
            )
            result = self.orchestrator._execute_step_with_retry(step, list(previous_results))
            captured[step_id] = result
            if result.model_used == "(erro)":
                return WorkerResult(False, error=result.response)
            return WorkerResult(True, summary=result.response[:2000])

        dispatch_result = dispatcher.dispatch_once(
            worker_fn=_worker,
            max_spawn=len(task_ids),
            spawn_background=False,
            only_task_ids=task_ids,
        )

        results: list[StepResult] = []
        store = KanbanStore(self.workspace)
        for step in pending:
            step_id = int(step["id"])
            node = self.store.get_node(run_id, step_id)
            task_id = node.task_id if node else ""
            latest_run = store.latest_run_for_task(task_id) if task_id else None
            dispatch_run_id = latest_run.run_id if latest_run else ""
            if dispatch_run_id:
                self.store.update_node(run_id, step_id, dispatch_run_id=dispatch_run_id)
                try:
                    wm.update_task_metadata(
                        task_id,
                        metadata={"orchestration_dispatch_run": dispatch_run_id},
                    )
                except WorkspaceError:
                    pass
            if step_id in captured:
                results.append(captured[step_id])
                continue
            reason = "; ".join(dispatch_result.skipped) or "Dispatcher did not execute orchestration node."
            results.append(
                StepResult(
                    id=step_id,
                    goal=str(step.get("goal", "")),
                    model_used="(erro)",
                    response=reason,
                    tool_log=[],
                )
            )
        results.sort(key=lambda result: result.id)
        return results

    def _queue_ready_nodes(self, run_id: str, steps: list[dict[str, Any]]) -> list[str]:
        from .task_dispatcher import TaskDispatcher
        from .workspace_manager import WorkspaceManager

        dispatcher = TaskDispatcher(self.workspace)
        wm = WorkspaceManager(self.workspace)
        nodes = {node.step_id: node for node in self.store.list_nodes(run_id)}
        succeeded = {step_id for step_id, node in nodes.items() if node.status == "succeeded"}
        queued: list[str] = []

        total_steps = len(steps)
        for step_idx, step in enumerate(steps, 1):
            goal = step.get('goal', step.get('name', 'passo ' + str(step_idx)))
            show_step('Passo ' + str(step_idx) + '/' + str(total_steps) + ': ' + goal[:60], 'running')
            step_id = int(step.get("id", 0))
            node = nodes.get(step_id)
            if node is None or node.status not in {"planned"}:
                continue
            deps = [int(dep) for dep in step.get("depends_on", [])]
            if any(dep not in succeeded for dep in deps):
                continue
            task = self._ensure_node_task(run_id, step, wm)
            if task.status not in {"READY", "IN_PROGRESS", "DONE"}:
                task = dispatcher.mark_ready(
                    task.id,
                    assignee=str(step.get("agent") or ""),
                    max_retries=1,
                )
            self.store.update_node(run_id, step_id, status="queued", task_id=task.id)
            queued.append(task.id)

        return queued

    def _succeeded_results(self, run_id: str) -> list[StepResult]:
        results = [
            StepResult(
                id=node.step_id,
                goal=node.goal,
                model_used=node.model_used,
                response=node.response,
                tool_log=node.tool_log,
            )
            for node in self.store.list_nodes(run_id)
            if node.status == "succeeded"
        ]
        results.sort(key=lambda result: result.id)
        return results

    def _dependency_results(self, run_id: str, step: dict[str, Any]) -> list[StepResult]:
        deps = {int(dep) for dep in step.get("depends_on", [])}
        results = [
            result
            for result in self._succeeded_results(run_id)
            if not deps or result.id in deps
        ]
        results.sort(key=lambda result: result.id)
        return results

    def _ensure_node_task(self, run_id: str, step: dict[str, Any], wm: Any):
        from .workspace_manager import WorkspaceError

        step_id = int(step["id"])
        node = self.store.get_node(run_id, step_id)
        if node and node.task_id:
            try:
                task = wm.get_task(node.task_id)
                wm.update_task_metadata(task.id, metadata=_node_task_metadata(run_id, step, self.node_runtime))
                return wm.get_task(task.id)
            except WorkspaceError:
                pass

        goal = str(step.get("goal", "")).strip() or f"Passo {step_id}"
        title_goal = goal if len(goal) <= 96 else goal[:93].rstrip() + "..."
        task = wm.add_task(
            f"[Orch {run_id}] Step {step_id}: {title_goal}",
            description=(
                f"Orchestration run: {run_id}\n"
                f"Step: {step_id}\n"
                f"Depends on: {step.get('depends_on', [])}\n\n"
                f"{goal}"
            ),
            status="TODO",
            assignee=str(step.get("agent") or ""),
            metadata=_node_task_metadata(run_id, step, self.node_runtime),
        )
        self.store.update_node(run_id, step_id, task_id=task.id)
        return task


def _normalize_mode(mode: str) -> str:
    raw = (mode or "hybrid").strip().lower()
    if raw in {"durable", "hybrid"}:
        return raw
    return "hybrid"


def _normalize_node_runtime(node_runtime: str, mode: str) -> str:
    raw = (node_runtime or "auto").strip().lower()
    if raw == "auto":
        return "dispatcher" if mode == "durable" else "inline"
    if raw in {"dispatcher", "inline"}:
        return raw
    return "dispatcher" if mode == "durable" else "inline"


def _node_task_metadata(run_id: str, step: dict[str, Any], node_runtime: str) -> dict[str, str | int]:
    metadata: dict[str, str | int] = {
        "orchestration_run": run_id,
        "orchestration_step": int(step.get("id", 0)),
        "orchestration_backend": node_runtime,
    }
    if step.get("agent"):
        metadata["agent"] = str(step.get("agent"))
    return metadata


def run_orchestration_node(
    orchestrator: AgentOrchestrator,
    *,
    workspace: str | Path,
    run_id: str,
    step_id: int,
    task_id: str = "",
    claim_id: str = "",
) -> NodeWorkerResult:
    store = OrchestrationStore(workspace)
    run = store.get_run(run_id)
    if run is None:
        raise ValueError(f"orchestration run not found: {run_id}")
    engine = DurableDAGExecutionEngine(
        orchestrator,
        workspace=workspace,
        mode=run.mode or "durable",
        node_runtime=(run.metadata.get("node_runtime") or "dispatcher"),
    )
    return engine.execute_node(run_id, step_id, task_id=task_id, claim_id=claim_id)
