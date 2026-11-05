"""Agno workflows under Kernel custody (plano 057, sprint 55 / plano "23").

An Agno ``Workflow`` is used only as the *step engine* (sequence, parallel
branches, conditions) **inside** one Kernel run:

* the run is opened, admitted (policy/budget/kill-switch), evaluated (quality
  gates) and closed by the Kernel through ``core.kernel.entry.run_governed``;
  the workflow never declares a terminal state — it returns a result dict and
  the Kernel decides (``completed`` only after gates);
* Agno's own retry/skip-on-error is disabled (``max_retries=0`` and a Bauer
  failure ledger): a failed step can never be reported as a completed run;
* before each step the Kernel run is re-read: cancelled/terminal ⇒ stop; the
  workflow deadline is checked; ``checkpoint_execution`` propagates channel
  cancellation;
* human review is a Bauer approval bound to the run (``ApprovalManager`` with
  ``suspend_run``): the Kernel run goes ``waiting_approval`` → ``queued`` →
  ``running`` on approval; denial/expiry/timeout stop the workflow;
* step events (``agent.started``/``agent.finished``/``agent.failed`` with
  ``agent_id=workflow:<name>/<step>``) carry only names, status and duration —
  never step inputs/outputs;
* no Agno db, no Agno session store, no AgentOS: persistence and scheduling
  stay with Bauer (plan STOP rule).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ...execution_control import ExecutionControlError, checkpoint_execution
from ...provider_retry import ProviderRetryInterrupted
from ..kernel.entry import CANCELLED, FAILED, GovernedResult, run_governed

logger = logging.getLogger("bauer.agno_workflows")

TERMINAL = {"completed", "failed", "cancelled"}


class WorkflowStopped(Exception):
    """Raised inside a step to stop the workflow (cancel/deadline/review)."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass
class WorkflowContext:
    """What a step may see: ids and governance handles, never other steps' raw I/O."""

    workflow: str
    run_id: str
    session_id: str | None
    deadline: float
    kernel: Any
    approvals: Any | None
    review_wait_seconds: float
    events: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    stopped: WorkflowStopped | None = None
    #: Scratch space steps share (in-process only, never published).
    values: dict[str, Any] = field(default_factory=dict)
    #: Return value of each finished step, by step name.
    outputs: dict[str, Any] = field(default_factory=dict)
    #: The workflow input, as given to the Kernel run (Agno may hand later
    #: steps the previous step's content instead — steps never rely on that).
    input: Any = None

    # ── governance checks ────────────────────────────────────────────────
    def check(self) -> None:
        if self.stopped is not None:
            raise self.stopped
        if self.failures:
            # Agno's default on_error is "skip": without this, the steps after a
            # failed one (review, publish...) would still run.
            raise WorkflowStopped(FAILED, f"etapa {self.failures[0]['step']} falhou")
        try:
            checkpoint_execution()
        except (ExecutionControlError, ProviderRetryInterrupted) as exc:
            self.stopped = WorkflowStopped(CANCELLED, f"cancelado: {type(exc).__name__}")
            raise self.stopped from None
        run = self.kernel.runs.get_run(self.run_id) if self.kernel is not None else None
        if run is not None and run.status in TERMINAL:
            self.stopped = WorkflowStopped(CANCELLED if run.status == "cancelled" else FAILED,
                                           f"run {run.status} fora do workflow")
            raise self.stopped
        if time.monotonic() > self.deadline:
            self.stopped = WorkflowStopped(FAILED, "prazo do workflow excedido")
            raise self.stopped

    def publish(self, event_type: str, step: str, status: str, **data: Any) -> None:
        record = {"event": event_type, "step": step, "status": status, **data}
        self.events.append(record)
        bus = getattr(self.kernel, "bus", None)
        if bus is None:
            return
        try:
            bus.publish(
                event_type,  # type: ignore[arg-type]
                run_id=self.run_id,
                session_id=self.session_id,
                agent_id=f"workflow:{self.workflow}/{step}",
                status=status,
                data={"workflow": self.workflow, "step": step, **data},
            )
        except Exception as exc:  # noqa: BLE001 - telemetry is best-effort
            logger.debug("workflow event failed: %s", type(exc).__name__)

    # ── human review ─────────────────────────────────────────────────────
    def request_review(self, step: str, *, summary: str, risk_level: str = "medium") -> None:
        """Block until a human approves this run's review (bound to the run).

        ``summary`` must be a short, non-private description of what is being
        approved (it is shown to the approver and stored with the request).
        """
        if self.approvals is None:
            raise WorkflowStopped(FAILED, "revisão humana exige ApprovalManager")
        operation = "workflow.review"
        tool_name = f"workflow:{self.workflow}/{step}"
        payload = {"workflow": self.workflow, "step": step, "summary": summary[:300]}
        record = self.approvals.request(
            operation=operation, tool_name=tool_name, reason=summary[:300], risk_level=risk_level,
            payload=payload, run_id=self.run_id, session_id=self.session_id, suspend_run=True,
        )
        self.publish("agent.paused", step, "waiting_approval", approval_id=record.id)
        wait_until = min(self.deadline, time.monotonic() + self.review_wait_seconds)
        while True:
            try:
                checkpoint_execution()
            except (ExecutionControlError, ProviderRetryInterrupted):
                self._expire(record.id)
                self.stopped = WorkflowStopped(CANCELLED, "cancelado durante revisão")
                raise self.stopped from None
            current = self.approvals.get(record.id)
            status = getattr(current, "status", "missing")
            run = self.kernel.runs.get_run(self.run_id)
            if status == "pending" and (run is None or run.status in TERMINAL):
                self._expire(record.id)
                run_status = getattr(run, "status", "ausente")
                self.stopped = WorkflowStopped(
                    CANCELLED if run_status == "cancelled" else FAILED, f"run {run_status} durante revisão",
                )
                raise self.stopped
            if status == "approved":
                if self.approvals.authorize(
                    record.id, operation=operation, tool_name=tool_name, payload=payload,
                    run_id=self.run_id, session_id=self.session_id, resume_run=True,
                ):
                    self.publish("agent.resumed", step, "approved", approval_id=record.id)
                    return
                self.stopped = WorkflowStopped(FAILED, "aprovação não autoriza esta revisão")
                raise self.stopped
            if status in {"denied", "expired", "cancelled", "missing"}:
                cancelled = getattr(run, "status", None) == "cancelled"
                self.stopped = WorkflowStopped(
                    CANCELLED if cancelled else FAILED,
                    "cancelado durante revisão" if cancelled else f"revisão {status}",
                )
                raise self.stopped
            if time.monotonic() > wait_until:
                self._expire(record.id)
                self.stopped = WorkflowStopped(FAILED, "revisão humana expirou")
                raise self.stopped
            time.sleep(0.05)

    def _expire(self, approval_id: str) -> None:
        try:
            self.kernel.expire_approval(approval_id)
        except Exception as exc:  # noqa: BLE001 - a concurrent decision may have won
            logger.debug("expire approval skipped: %s", type(exc).__name__)


StepFn = Callable[[WorkflowContext, Any], Any]


@dataclass(frozen=True)
class StepSpec:
    name: str
    fn: StepFn


@dataclass(frozen=True)
class ParallelSpec:
    name: str
    steps: tuple[StepSpec, ...]


@dataclass(frozen=True)
class ConditionSpec:
    name: str
    predicate: Callable[[WorkflowContext], bool]
    steps: tuple[StepSpec, ...]


Node = StepSpec | ParallelSpec | ConditionSpec


class GovernedWorkflow:
    """Declarative steps → Agno Workflow, executed inside a Kernel run."""

    def __init__(
        self,
        name: str,
        nodes: list[Node],
        *,
        deadline_seconds: float = 600.0,
        review_wait_seconds: float = 3600.0,
    ) -> None:
        self.name = name
        self.nodes = list(nodes)
        self.deadline_seconds = deadline_seconds
        self.review_wait_seconds = review_wait_seconds

    # ── Agno graph ──────────────────────────────────────────────────────
    def _wrap(self, ctx: WorkflowContext, spec: StepSpec):
        from agno.workflow import Step
        from agno.workflow.types import StepInput, StepOutput

        def executor(step_input: StepInput) -> StepOutput:
            ctx.check()
            started = time.monotonic()
            ctx.publish("agent.started", spec.name, "running")
            try:
                value = spec.fn(ctx, ctx.input)
            except WorkflowStopped as stop:
                if not ctx.failures:
                    ctx.stopped = ctx.stopped or stop
                ctx.publish("agent.cancelled" if stop.status == CANCELLED else "agent.failed",
                            spec.name, stop.status, duration_ms=_ms(started))
                raise
            except Exception as exc:  # noqa: BLE001 - recorded; Agno must not swallow it
                ctx.failures.append({"step": spec.name, "error": type(exc).__name__})
                ctx.publish("agent.failed", spec.name, "failed", duration_ms=_ms(started), error=type(exc).__name__)
                raise
            ctx.outputs[spec.name] = value
            ctx.publish("agent.finished", spec.name, "completed", duration_ms=_ms(started))
            return StepOutput(content=value if isinstance(value, str) else repr(type(value).__name__))

        return Step(name=spec.name, executor=executor, max_retries=0, skip_on_failure=False)

    def _build(self, ctx: WorkflowContext):
        from agno.workflow import Condition, Parallel, Workflow

        steps: list[Any] = []
        for node in self.nodes:
            if isinstance(node, StepSpec):
                steps.append(self._wrap(ctx, node))
            elif isinstance(node, ParallelSpec):
                steps.append(Parallel(*[self._wrap(ctx, item) for item in node.steps], name=node.name))
            else:
                predicate = node.predicate
                steps.append(Condition(
                    name=node.name,
                    evaluator=lambda _si, _p=predicate: (not ctx.failures and ctx.stopped is None and bool(_p(ctx))),
                    steps=[self._wrap(ctx, item) for item in node.steps],
                ))
        # No db: Bauer owns persistence; Agno keeps nothing between runs. No
        # telemetry: Agno would otherwise report runs to its own API.
        return Workflow(name=f"bauer-{self.name}", steps=steps, db=None, telemetry=False)

    # ── Kernel entry ────────────────────────────────────────────────────
    def run(
        self,
        kernel: Any,
        *,
        agent_id: str,
        input: Any,
        session_id: str | None = None,
        approvals: Any | None = None,
        metadata: dict[str, Any] | None = None,
        on_context: Callable[[WorkflowContext], None] | None = None,
    ) -> GovernedResult:
        def executor(payload: dict[str, Any]) -> dict[str, Any]:
            run_id = str(payload.get("run_id") or "")
            run = kernel.runs.get_run(run_id) if kernel is not None and run_id else None
            ctx = WorkflowContext(
                workflow=self.name,
                run_id=run_id,
                session_id=getattr(run, "session_id", None) or session_id,
                deadline=time.monotonic() + self.deadline_seconds,
                kernel=kernel,
                approvals=approvals if approvals is not None else getattr(kernel, "approvals", None),
                review_wait_seconds=self.review_wait_seconds,
                input=payload.get("workflow_input", input),
            )
            if on_context is not None:
                on_context(ctx)
            workflow = self._build(ctx)
            try:
                output = workflow.run(input=ctx.input)
            except WorkflowStopped as stop:
                ctx.stopped = ctx.stopped or stop
            except Exception as exc:  # noqa: BLE001
                ctx.failures.append({"step": "<workflow>", "error": type(exc).__name__})
            else:
                raw_status = getattr(output, "status", None)
                agno_status = str(getattr(raw_status, "value", raw_status or "")).lower().rsplit(".", 1)[-1]
                if agno_status not in {"completed", ""} and not ctx.failures and ctx.stopped is None:
                    ctx.failures.append({"step": "<workflow>", "error": f"agno:{agno_status}"})
            summary = {"workflow": self.name, "steps": [e["step"] for e in ctx.events if e["event"] == "agent.finished"]}
            if ctx.failures:
                first = ctx.failures[0]
                return {"status": FAILED, "error": f"etapa {first['step']} falhou ({first['error']})", "output": summary}
            if ctx.stopped is not None:
                return {"status": ctx.stopped.status, "error": ctx.stopped.reason, "output": summary}
            final = ctx.outputs.get(self._last_step_name() or "")
            return {"output": final if final is not None else summary, "tool_calls_count": 0, "cost_estimate": 0.0}

        return run_governed(
            kernel,
            executor,
            agent_id=agent_id,
            task=f"workflow:{self.name}",
            input={"workflow_input": input},
            session_id=session_id,
            runtime_adapter="agno_workflow",
            metadata={"workflow": self.name, **dict(metadata or {})},
        )

    def _last_step_name(self) -> str | None:
        for node in reversed(self.nodes):
            if isinstance(node, StepSpec):
                return node.name
        return None


def _ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


# ── concrete process: data report (sprint 55 pilot) ─────────────────────────


def data_report_workflow(
    workspace: Any,
    *,
    deadline_seconds: float = 300.0,
    review_wait_seconds: float = 3600.0,
    data_settings: Any | None = None,
) -> GovernedWorkflow:
    """Profile → (totals ∥ quality) → [flag] → human review → publish.

    A real, bounded process that benefits from steps, a parallel branch, a
    condition and human review. Read steps use ``bauer.data`` (workspace-
    scoped, budgeted); the only effect (writing the report artifact under
    ``.bauer/artifacts/data``) happens strictly after a run-bound approval.
    Input: ``{"path": str, "group_by": str, "metric": str, "name": str}``.
    """
    from pathlib import Path

    from .agno_capabilities.families.data import DataSettings, _actions

    actions = _actions(Path(workspace), data_settings or DataSettings())

    def profile(ctx: WorkflowContext, data: dict[str, Any]) -> str:
        info = actions["describe_file"](path=data["path"], sample_rows=0)
        ctx.values["profile"] = info
        return f"{info['rows_scanned']} linhas, {len(info['columns'])} colunas"

    def totals(ctx: WorkflowContext, data: dict[str, Any]) -> str:
        result = actions["query_file"](
            path=data["path"], group_by=[data["group_by"]], aggregate={data["metric"]: "sum"},
            order_by=data["group_by"], limit=200,
        )
        ctx.values["totals"] = result["rows"]
        return f"{len(result['rows'])} grupos"

    def quality(ctx: WorkflowContext, data: dict[str, Any]) -> str:
        info = actions["describe_file"](path=data["path"], sample_rows=0)
        nulls = {c["name"]: c["nulls"] for c in info["columns"] if c["nulls"]}
        ctx.values["nulls"] = nulls
        return f"{sum(nulls.values())} valores nulos"

    def flag(ctx: WorkflowContext, data: dict[str, Any]) -> str:
        ctx.values["warning"] = "Atenção: colunas com valores ausentes: " + ", ".join(sorted(ctx.values["nulls"]))
        return "qualidade sinalizada"

    def review(ctx: WorkflowContext, data: dict[str, Any]) -> str:
        groups = len(ctx.values.get("totals") or [])
        ctx.request_review("review", summary=f"Publicar relatório '{data.get('name', 'relatorio')}' ({groups} grupos)")
        return "aprovado"

    def publish(ctx: WorkflowContext, data: dict[str, Any]) -> str:
        metric, key = data["metric"], data["group_by"]
        lines = [f"# {data.get('name', 'Relatório')}", "", f"| {key} | soma de {metric} |", "|---|---|"]
        for row in ctx.values.get("totals") or []:
            lines.append(f"| {row.get(key)} | {row.get('sum_' + metric)} |")
        if ctx.values.get("warning"):
            lines += ["", ctx.values["warning"]]
        saved = actions["save_report"](name=data.get("name", "relatorio"), content="\n".join(lines) + "\n", format="md")
        return str(saved["artifact"])

    return GovernedWorkflow(
        "data_report",
        [
            StepSpec("profile", profile),
            ParallelSpec("analysis", (StepSpec("totals", totals), StepSpec("quality", quality))),
            ConditionSpec("quality_gate", lambda ctx: bool(ctx.values.get("nulls")), (StepSpec("flag", flag),)),
            StepSpec("review", review),
            StepSpec("publish", publish),
        ],
        deadline_seconds=deadline_seconds,
        review_wait_seconds=review_wait_seconds,
    )
