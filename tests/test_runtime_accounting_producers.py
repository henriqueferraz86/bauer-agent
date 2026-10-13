"""K03/K04: CLI and scheduler report incremental consumption on every outcome."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from bauer.cli import app
from bauer.config_loader import BauerConfig, ModelSection
from bauer.core.kernel.evaluator import Evaluator, GateResult
from bauer.core.kernel.kernel import BauerKernel
from bauer.core.runtime.autonomy import BudgetManager
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.scheduler import Scheduler


class ReplanOnce:
    name = "replan_once"

    def __init__(self):
        self.calls = 0

    def check(self, **_kwargs):
        self.calls += 1
        return GateResult(self.name, self.calls > 1, "try again")


@pytest.mark.parametrize("outcome", ["failed", "tool_limit", "retry", "zero"])
def test_scheduler_preserves_consumption_on_failures_and_retries(tmp_path, outcome):
    calls = []

    def execute(_request):
        calls.append(1)
        return {"status": "failed" if outcome == "failed" or (outcome == "retry" and len(calls) == 1) else "completed",
                "error": "controlled failure", "output": "ok",
                "cost_estimate": 0 if outcome == "zero" else 0.4,
                "tool_calls_count": 2, "llm_calls_count": 1, "usage_known": True}

    scheduler = Scheduler(root=tmp_path, adapter_factory=lambda _name: SimpleNamespace(run_agent=execute))
    task = scheduler.add_task({
        "id": "controlled", "agent_id": "controlled", "runtime_adapter": "fake",
        "schedule": {"type": "interval", "seconds": 60}, "input": {"message": "controlled"},
        "policy": {"estimated_cost_usd": 0.1, "max_tool_calls": 1 if outcome == "tool_limit" else 10,
                   "retry_count": 1 if outcome == "retry" else 0},
    })
    result = scheduler.run_task(task.id)
    expected_cost = 0 if outcome == "zero" else 0.4 * len(calls)
    run = scheduler.run_manager.get_run(result["run_id"])
    assert run.status == ("failed" if outcome in {"failed", "tool_limit"} else "completed")
    assert run.cost_estimate == pytest.approx(expected_cost)
    assert run.tool_calls_count == 2 * len(calls)
    assert run.llm_calls_count == len(calls)
    assert scheduler.budget_manager.status()["daily"]["used_usd"] == pytest.approx(expected_cost)


@pytest.mark.parametrize("failed", [False, True])
def test_cli_attempts_report_deltas_and_failed_provider_cost(tmp_path, monkeypatch, failed):
    from bauer.cost_meter import cost_sink

    root = tmp_path / "memory" / "runtime"
    budget_manager = BudgetManager(root=root)
    kernel = BauerKernel(runs=RunManager(root=root), budget=budget_manager,
                         evaluator=Evaluator([ReplanOnce()], max_replans=1), heartbeat_interval_s=0)
    config = BauerConfig(model=ModelSection(provider="ollama", name="controlled"))
    monkeypatch.setattr("bauer.commands._runtime._load_or_die", lambda *_a: (config, MagicMock()))
    monkeypatch.setattr("bauer.commands._runtime._build_client", lambda *_a: object())
    router = MagicMock(workspace=tmp_path)
    router.available_tools.return_value = []
    monkeypatch.setattr("bauer.commands._runtime._build_router", lambda *_a, **_k: router)
    monkeypatch.setattr("bauer.commands.agent_cmd._build_fallback_clients", lambda *_a, **_k: [])
    monkeypatch.setattr("bauer.agent._build_system_prompt", lambda *_a, **_k: "controlled")
    monkeypatch.setattr("bauer.commands.run_cmd.Path.cwd", lambda: tmp_path)
    monkeypatch.setattr("bauer.core.kernel.build_kernel", lambda *_a, **_k: kernel)

    def rounds(**kwargs):
        cost_sink.get()("openai", "controlled", {"prompt_tokens": 10}, 0.4)
        if failed:
            # run_loop_rounds also catches provider failures before on_round.
            return "provider_error", 1, "controlled failure", []
        tools = [{"tool": "read_file"}, {"tool": "read_file"}]
        for _tool in tools:
            kwargs["budget"].consume_tool_call()
        kwargs["on_round"](1, "done", tools)
        return "completed", 1, "done", tools

    monkeypatch.setattr("bauer.serve_loop.run_loop_rounds", rounds)
    result = CliRunner().invoke(app, ["run", "controlled"])
    assert result.exit_code == (2 if failed else 0), result.output
    run = kernel.runs.list_runs()[0]
    assert run.cost_estimate == pytest.approx(0.4 if failed else 0.8)
    assert run.tool_calls_count == (0 if failed else 4)
    assert run.llm_calls_count == (1 if failed else 2)
    assert budget_manager.status()["daily"]["used_usd"] == pytest.approx(run.cost_estimate)
