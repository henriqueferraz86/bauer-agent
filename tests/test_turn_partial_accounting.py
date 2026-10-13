"""K03/K04: completed tools survive a later provider failure or fallback."""

import json
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from bauer.agent import run_one_turn, run_one_turn_with_fallback
from bauer.autonomous_budget import AutonomousBudget
from bauer.cli import app
from bauer.config_loader import BauerConfig, ModelSection
from bauer.context_manager import ContextManager
from bauer.cost_meter import cost_sink
from bauer.core.kernel.kernel import BauerKernel
from bauer.core.kernel.entry import continue_governed, run_governed
from bauer.core.kernel.schemas import KernelRequest
from bauer.core.runtime.autonomy import BudgetManager
from bauer.core.runtime.run_manager import RunManager
from bauer.openai_client import OpenAIClient, OpenAIClientError
from bauer.serve_loop import run_loop_rounds
from bauer.tool_router import ToolRouter


class NativeClient(OpenAIClient):
    def __init__(self, *, error_code=400, final=False, cancel_round=None):
        super().__init__(host="http://unused.invalid", model="controlled")
        self.error_code, self.final = error_code, final
        self.calls = 0
        self.cancel_round = cancel_round
        self._provider = "openai"

    def chat_with_tools(self, model, messages, tools, **_kwargs):
        self.calls += 1
        self.last_usage = {"prompt_tokens": 10, "completion_tokens": 1, "cost": 0.4}
        if self.final:
            return {"content": "done", "tool_calls": []}
        if self.calls == 1 or (self.cancel_round and self.calls % 2):
            return {"content": "", "tool_calls": [{"id": "read", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps({"path": "marker.txt"}),
            }}]}
        if self.cancel_round:
            if self.calls == self.cancel_round * 2:
                raise KeyboardInterrupt("controlled cancellation after tool")
            return {"content": "done", "tool_calls": []}
        raise OpenAIClientError("controlled failure after tool", status_code=self.error_code)


class BridgeClient:
    _provider = "openai"

    def __init__(self, *, error_code=400, final=False, cancel_round=None):
        self.error_code, self.final = error_code, final
        self.calls = 0
        self.cancel_round = cancel_round

    def chat_stream(self, model, messages):
        self.calls += 1
        self.last_usage = {"prompt_tokens": 10, "completion_tokens": 1, "cost": 0.4}
        if self.final:
            yield "done"
        elif self.calls == 1 or (self.cancel_round and self.calls % 2):
            yield json.dumps({"action": "read_file", "args": {"path": "marker.txt"}})
        elif self.cancel_round:
            if self.calls == self.cancel_round * 2:
                raise KeyboardInterrupt("controlled cancellation after tool")
            yield "done"
        else:
            raise OpenAIClientError("controlled failure after tool", status_code=self.error_code)


@pytest.fixture
def turn_kit(tmp_path):
    (tmp_path / "marker.txt").write_text("marker", encoding="utf-8")
    ctx = ContextManager(applied_context=4096, system_prompt="controlled")
    ctx.add_user("read marker")
    return ctx, ToolRouter(workspace=tmp_path)


@pytest.mark.parametrize("client_type", [NativeClient, BridgeClient])
def test_turn_failure_carries_completed_tools(turn_kit, client_type):
    ctx, router = turn_kit
    with pytest.raises(OpenAIClientError) as raised:
        run_one_turn(ctx, router, client_type(), "controlled")
    tools = getattr(raised.value, "partial_tool_log", [])
    assert len(tools) == 1
    assert tools[0]["tool"] == "read_file"
    assert "marker" in tools[0]["result"]
    assert raised.value.status_code == 400


@pytest.mark.parametrize("client_type", [NativeClient, BridgeClient])
@pytest.mark.parametrize("final_failure", [False, True])
def test_fallback_preserves_tools_from_every_attempt(turn_kit, client_type, final_failure):
    ctx, router = turn_kit
    primary = client_type(error_code=429)
    fallback = client_type(final=not final_failure)
    if final_failure:
        with pytest.raises(OpenAIClientError) as raised:
            run_one_turn_with_fallback(ctx, router, primary, "controlled", [(fallback, "controlled")])
        tools = getattr(raised.value, "partial_tool_log", [])
        assert raised.value.status_code == 400
    else:
        answer, tools = run_one_turn_with_fallback(ctx, router, primary, "controlled", [(fallback, "controlled")])
        assert answer == "done"
    assert len(tools) == (2 if final_failure else 1)
    assert all(tool["tool"] == "read_file" for tool in tools)


@pytest.mark.parametrize("client_type", [NativeClient, BridgeClient])
def test_loop_counts_partial_tools_once_with_shared_budget(turn_kit, client_type):
    ctx, router = turn_kit
    client = client_type()
    budget = AutonomousBudget(max_tool_calls=10)
    stop, _, _, tools = run_loop_rounds(
        goal="controlled", ctx=ctx, budget=budget,
        turn_fn=lambda: run_one_turn(ctx, router, client, "controlled", budget=budget),
    )
    assert stop == "provider_error"
    assert len(tools) == 1
    assert budget.snapshot().tool_calls == 1


@pytest.mark.parametrize("client_type", [NativeClient, BridgeClient])
def test_loop_fallback_keeps_previously_accounted_tools(turn_kit, client_type):
    ctx, router = turn_kit
    primary = client_type(error_code=429)
    fallback = client_type(final=True)
    budget = AutonomousBudget(max_tool_calls=10)
    stop, _, _, tools = run_loop_rounds(
        goal="controlled", ctx=ctx, budget=budget,
        turn_fn=lambda: run_one_turn_with_fallback(
            ctx, router, primary, "controlled", [(fallback, "controlled")], budget=budget,
        ),
    )
    assert stop == "completed"
    assert len(tools) == 1
    assert budget.snapshot().tool_calls == 1


@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_nonprovider_fallback_error_retains_previous_tools(turn_kit, error_type):
    ctx, router = turn_kit

    class FailingFallback(NativeClient):
        def chat_with_tools(self, *args, **kwargs):
            raise error_type("controlled fallback failure")

    with pytest.raises(error_type) as raised:
        run_one_turn_with_fallback(ctx, router, NativeClient(error_code=429), "controlled",
                                   [(FailingFallback(), "controlled")])
    assert len(raised.value.partial_tool_log) == 1


@pytest.mark.parametrize("failure_round", [1, 2])
def test_loop_callback_failure_retains_completed_tools(turn_kit, failure_round):
    ctx, router = turn_kit

    class ToolThenDone(NativeClient):
        def chat_with_tools(self, *args, **kwargs):
            if self.calls:
                self.final = True
            return super().chat_with_tools(*args, **kwargs)

    client = ToolThenDone()
    budget = AutonomousBudget(max_tool_calls=10)
    failure = OSError("controlled session save failure")

    def save_round(round_number, _text, _tools):
        if round_number == failure_round:
            raise failure

    with pytest.raises(OSError) as raised:
        run_loop_rounds(
            goal="controlled", ctx=ctx, budget=budget, on_round=save_round,
            turn_fn=lambda: run_one_turn(ctx, router, client, "controlled", budget=budget),
        )
    assert raised.value is failure
    assert len(raised.value.partial_tool_log) == 1
    assert raised.value.partial_tool_log[0]["tool"] == "read_file"
    assert budget.snapshot().tool_calls == 1


@pytest.mark.parametrize("client_type", [NativeClient, BridgeClient])
@pytest.mark.parametrize("cancel_round", [1, 2])
def test_loop_cancellation_preserves_every_round_tool(turn_kit, client_type, cancel_round):
    ctx, router = turn_kit
    client = client_type(cancel_round=cancel_round)
    budget = AutonomousBudget(max_tool_calls=10)
    with pytest.raises(KeyboardInterrupt) as raised:
        run_loop_rounds(
            goal="controlled", ctx=ctx, budget=budget,
            turn_fn=lambda: run_one_turn(ctx, router, client, "controlled", budget=budget),
        )
    assert len(raised.value.partial_tool_log) == cancel_round
    assert budget.snapshot().tool_calls == cancel_round


@pytest.mark.parametrize("client_type", [NativeClient, BridgeClient])
def test_cli_real_loop_persists_tools_before_provider_failure(tmp_path, monkeypatch, client_type):
    result, kernel, budget = _invoke_cli(tmp_path, monkeypatch, client_type())
    assert result.exit_code == 2, result.output
    run = kernel.runs.list_runs()[0]
    assert run.status == "failed"
    assert run.tool_calls_count == 1
    assert run.llm_calls_count == 2
    assert run.cost_estimate == pytest.approx(0.8)
    assert budget.status()["daily"]["used_usd"] == pytest.approx(run.cost_estimate)


@pytest.mark.parametrize("client_type", [NativeClient, BridgeClient])
@pytest.mark.parametrize("cancel_round", [1, 2])
def test_cli_cancellation_persists_partial_usage_and_tools(tmp_path, monkeypatch, client_type, cancel_round):
    result, kernel, budget = _invoke_cli(tmp_path, monkeypatch, client_type(cancel_round=cancel_round))
    assert result.exit_code == 130, result.output
    run = kernel.runs.list_runs()[0]
    assert run.status == "cancelled"
    assert run.tool_calls_count == cancel_round
    assert run.llm_calls_count == cancel_round * 2
    assert run.cost_estimate == pytest.approx(cancel_round * 0.8)
    assert run.elapsed_seconds > 0
    assert budget.status()["daily"]["used_usd"] == pytest.approx(run.cost_estimate)


@pytest.mark.parametrize("client_type", [NativeClient, BridgeClient])
def test_cli_round_callback_failure_persists_completed_usage(tmp_path, monkeypatch, client_type):
    def fail_callback(*_args, **_kwargs):
        raise OSError("controlled round callback failure")

    monkeypatch.setattr("bauer.progress_signals.SinaisDeProgresso.rodada", fail_callback)
    result, kernel, budget = _invoke_cli(tmp_path, monkeypatch, client_type(cancel_round=2))
    assert result.exit_code == 2
    run = kernel.runs.list_runs()[0]
    assert run.status == "failed"
    assert run.tool_calls_count == 1
    assert run.llm_calls_count == 2
    assert run.cost_estimate == pytest.approx(0.8)
    assert run.error == "controlled round callback failure"
    assert budget.status()["daily"]["used_usd"] == pytest.approx(0.8)


def _invoke_cli(tmp_path, monkeypatch, client):
    (tmp_path / "marker.txt").write_text("marker", encoding="utf-8")
    root = tmp_path / "memory" / "runtime"
    budget = BudgetManager(root=root)
    kernel = BauerKernel(runs=RunManager(root=root), budget=budget, heartbeat_interval_s=0)
    config = BauerConfig(model=ModelSection(provider="ollama", name="controlled"))
    monkeypatch.setattr("bauer.commands._runtime._load_or_die", lambda *_a: (config, MagicMock()))
    monkeypatch.setattr("bauer.commands._runtime._build_client", lambda *_a: client)
    router = ToolRouter(workspace=tmp_path)
    monkeypatch.setattr("bauer.commands._runtime._build_router", lambda *_a, **_k: router)
    monkeypatch.setattr("bauer.commands.agent_cmd._build_fallback_clients", lambda *_a, **_k: [])
    monkeypatch.setattr("bauer.agent._build_system_prompt", lambda *_a, **_k: "controlled")
    monkeypatch.setattr("bauer.commands.run_cmd.Path.cwd", lambda: tmp_path)
    monkeypatch.setattr("bauer.core.kernel.build_kernel", lambda *_a, **_k: kernel)
    return CliRunner().invoke(app, ["run", "controlled"]), kernel, budget


@pytest.mark.parametrize("mode", ["execute", "continue", "direct"])
def test_governed_cancel_preserves_partial_executor_metrics(tmp_path, mode):
    budget = BudgetManager(root=tmp_path)
    kernel = BauerKernel(runs=RunManager(root=tmp_path), budget=budget, heartbeat_interval_s=0)
    error = KeyboardInterrupt("controlled cancellation")
    error.partial_metrics = {"output": "partial", "cost_estimate": 0.4,
                             "llm_calls_count": 3, "tool_calls_count": 2,
                             "usage_known": True, "elapsed_seconds": 2.0}

    def interrupted(_payload):
        raise error

    if mode == "continue":
        run, early = kernel.admit(KernelRequest(task="controlled", agent_id="test"))
        assert early is None
        result = continue_governed(kernel, run.id, interrupted)
    else:
        result = run_governed(kernel if mode == "execute" else None, interrupted,
                              task="controlled", agent_id="test")
    assert result.status == "cancelled"
    assert result.result["cost_estimate"] == 0.4
    assert result.result["tool_calls_count"] == 2
    assert result.result["llm_calls_count"] == 3
    if mode != "direct":
        run = kernel.runs.get_run(result.run_id)
        assert run.cost_estimate == 0.4
        assert run.tool_calls_count == 2
        assert run.llm_calls_count == 3
        assert run.elapsed_seconds == 2.0
        assert run.usage_known is True
        assert budget.status()["daily"]["used_usd"] == 0.4


@pytest.mark.parametrize("telemetry_fault", ["getter", "setter", "readonly", "stale"])
@pytest.mark.parametrize("provider_failure", [False, True])
def test_native_usage_telemetry_failure_preserves_provider_result(turn_kit, telemetry_fault, provider_failure):
    ctx, router = turn_kit

    class AuxiliaryFailureClient(NativeClient):
        telemetry_fault = None

        @property
        def last_usage(self):
            if self.telemetry_fault == "getter":
                raise RuntimeError("controlled telemetry getter failure")
            return self._usage

        @last_usage.setter
        def last_usage(self, value):
            if self.telemetry_fault in {"readonly", "stale"}:
                raise AttributeError("controlled read-only telemetry")
            if self.telemetry_fault == "setter":
                raise RuntimeError("controlled telemetry setter failure")
            self._usage = value

        def chat_with_tools(self, *_args, **_kwargs):
            self.calls += 1
            if self.telemetry_fault != "stale":
                self._usage.update(prompt_tokens=20, completion_tokens=1, cost=0.4)
            if provider_failure:
                raise OpenAIClientError("controlled provider failure", status_code=400)
            return {"content": "done", "tool_calls": []}

    client = AuxiliaryFailureClient()
    client._usage = {"prompt_tokens": 10, "completion_tokens": 1, "cost": 0.1}
    client.telemetry_fault = telemetry_fault
    events = []
    token = cost_sink.set(lambda *args: events.append(args))
    try:
        if provider_failure:
            with pytest.raises(OpenAIClientError, match="controlled provider failure"):
                run_one_turn(ctx, router, client, "controlled")
        else:
            answer, tools = run_one_turn(ctx, router, client, "controlled")
            assert answer == "done"
            assert tools == []
    finally:
        cost_sink.reset(token)
    assert client.calls == 1
    assert len(events) == 1
    assert events[0][3] == pytest.approx(0.0 if telemetry_fault in {"getter", "stale"} else 0.4)
    if telemetry_fault in {"getter", "stale"}:
        assert events[0][2] == {}
