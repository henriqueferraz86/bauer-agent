"""Plano 057 / sprint 55 — workflow Agno sob custódia do Kernel.

O motor de etapas é o ``agno.workflow.Workflow`` real (etapas função, sem LLM);
Kernel, RunManager, ApprovalManager e EventBus reais sobre ``tmp_path``.
Cenários: sucesso, falha, cancelamento, timeout, rejeição de aprovação,
retomada e gate do Kernel reprovando.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from bauer.core.events.bus import EventBus
from bauer.core.kernel import BauerKernel
from bauer.core.kernel.evaluator import Evaluator, GateResult
from bauer.core.policy.approvals import ApprovalManager
from bauer.core.runtime.agno_workflows import (
    GovernedWorkflow,
    StepSpec,
    data_report_workflow,
)
from bauer.core.runtime.run_manager import RunManager

pytest.importorskip("agno")

_INPUT = {"path": "sales.csv", "group_by": "region", "metric": "amount", "name": "Vendas"}


@pytest.fixture()
def kit(tmp_path: Path):
    root = tmp_path / "runtime"
    bus = EventBus(root=root)
    runs = RunManager(root=root, event_bus=bus)
    approvals = ApprovalManager(root=root, event_bus=bus)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sales.csv").write_text(
        "region,amount\nsul,10\nsul,5\nnorte,7\nnorte,\n", encoding="utf-8",
    )
    return {"bus": bus, "runs": runs, "approvals": approvals, "workspace": workspace}


def _kernel(kit, **kwargs) -> BauerKernel:
    return BauerKernel(runs=kit["runs"], bus=kit["bus"], approvals=kit["approvals"], heartbeat_interval_s=0, **kwargs)


def _decide_when_pending(kit, kernel, decision: str, seen: list[str] | None = None):
    def worker():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            pending = kit["approvals"].list("pending")
            if pending:
                record = pending[0]
                if seen is not None:
                    seen.append(kit["runs"].get_run(record.run_id).status)
                if decision == "approve":
                    kernel.approve(record.id)
                elif decision == "deny":
                    kernel.deny(record.id)
                else:
                    kernel.cancel(record.run_id)
                return
            time.sleep(0.02)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _workflow_events(kit, run_id: str) -> list:
    return [e for e in kit["bus"].list_events(run_id=run_id) if (e.agent_id or "").startswith("workflow:")]


def test_success_with_review_resumes_and_kernel_declares_completion(kit):
    kernel = _kernel(kit)
    seen: list[str] = []
    thread = _decide_when_pending(kit, kernel, "approve", seen)

    result = data_report_workflow(kit["workspace"]).run(kernel, agent_id="bauer.data", input=_INPUT)
    thread.join(5)

    assert result.status == "completed", result.error
    assert result.governed is True
    assert result.output == ".bauer/artifacts/data/vendas.md"
    report = (kit["workspace"] / result.output).read_text(encoding="utf-8")
    assert "| sul | 15 |" in report and "valores ausentes" in report
    assert seen == ["waiting_approval"]
    assert kit["runs"].get_run(result.run_id).status == "completed"

    steps = [(e.event_type, e.data["step"]) for e in _workflow_events(kit, result.run_id)]
    finished = [step for kind, step in steps if kind == "agent.finished"]
    assert finished[0] == "profile" and set(finished[1:3]) == {"totals", "quality"}
    assert finished[3:] == ["flag", "review", "publish"]
    assert ("agent.paused", "review") in steps and ("agent.resumed", "review") in steps
    # Events reconstruct the sequence without carrying step content.
    serialized = json.dumps([e.data for e in _workflow_events(kit, result.run_id)])
    assert "15" not in serialized and "sul" not in serialized


def test_review_denied_fails_run_and_nothing_is_published(kit):
    kernel = _kernel(kit)
    thread = _decide_when_pending(kit, kernel, "deny")

    result = data_report_workflow(kit["workspace"]).run(kernel, agent_id="bauer.data", input=_INPUT)
    thread.join(5)

    assert result.status == "failed"
    assert kit["runs"].get_run(result.run_id).status == "failed"
    assert not (kit["workspace"] / ".bauer" / "artifacts" / "data" / "vendas.md").exists()


def test_cancellation_during_review_is_terminal_cancelled(kit):
    kernel = _kernel(kit)
    thread = _decide_when_pending(kit, kernel, "cancel")

    result = data_report_workflow(kit["workspace"]).run(kernel, agent_id="bauer.data", input=_INPUT)
    thread.join(5)

    assert result.status == "cancelled"
    assert kit["runs"].get_run(result.run_id).status == "cancelled"
    assert not (kit["workspace"] / ".bauer" / "artifacts" / "data" / "vendas.md").exists()


def test_review_timeout_fails_and_expires_approval(kit):
    kernel = _kernel(kit)
    workflow = data_report_workflow(kit["workspace"], review_wait_seconds=0.3)

    result = workflow.run(kernel, agent_id="bauer.data", input=_INPUT)

    assert result.status == "failed"
    assert kit["approvals"].list("pending") == []
    assert [r.status for r in kit["approvals"].list()] == ["expired"]


def test_step_failure_is_never_reported_as_completed(kit):
    kernel = _kernel(kit)
    result = data_report_workflow(kit["workspace"]).run(
        kernel, agent_id="bauer.data", input={**_INPUT, "path": "missing.csv"},
    )
    assert result.status == "failed"
    assert "profile" in (result.error or "")
    assert kit["approvals"].list() == []  # never reached review


def test_deadline_stops_workflow(kit):
    def slow(ctx, data):
        time.sleep(0.3)
        return "ok"

    workflow = GovernedWorkflow(
        "slow", [StepSpec("one", slow), StepSpec("two", slow)], deadline_seconds=0.2,
    )
    result = workflow.run(_kernel(kit), agent_id="bauer.test", input={})
    assert result.status == "failed"
    assert "prazo" in (result.error or "")


def test_kernel_gates_still_decide_terminal_state(kit):
    class AlwaysFails:
        name = "always_fails"

        def check(self, *, request, result):
            return GateResult(self.name, False, "reprovado")

    calls: list[int] = []

    def step(ctx, data):
        calls.append(1)
        return "pronto"

    workflow = GovernedWorkflow("gated", [StepSpec("only", step)])
    kernel = _kernel(kit, evaluator=Evaluator([AlwaysFails()], max_replans=0))
    result = workflow.run(kernel, agent_id="bauer.test", input={})

    assert result.status != "completed"
    assert kit["runs"].get_run(result.run_id).status == "failed"
    assert calls == [1]


def test_no_kernel_custody_bypass_and_no_agno_db():
    import inspect

    import bauer.core.runtime.agno_workflows as module

    source = inspect.getsource(module)
    for forbidden in ("complete_run(", "fail_run(", ".admit(", "SqliteDb", "PostgresDb", "AgentOS(", "agno.db", "agno.os"):
        assert forbidden not in source
