"""Native provider retries preserve the current turn and its hard budgets."""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from bauer.agent import run_one_turn
from bauer.autonomous_budget import AutonomousBudget
from bauer.context_manager import ContextManager
from bauer.cost_meter import cost_sink
from bauer.delta_stream import emit_provider_retry, reset_sink, set_sink
from bauer.iteration_budget import IterationBudget
from bauer.openai_client import OpenAIClient, OpenAIClientError
from bauer.provider_retry import ProviderRetryInterrupted
from bauer import provider_retry as retry
from bauer.tool_router import ToolRouter


def tool_message(name="write_file", **args):
    return {"content": "", "tool_calls": [{"id": "call-1", "type": "function", "function": {
        "name": name, "arguments": json.dumps(args),
    }}]}


class ControlledClient(OpenAIClient):
    def __init__(self, responses):
        super().__init__(host="http://unused.invalid", model="controlled")
        self.responses = iter(responses)
        self.payloads = []

    def chat_with_tools(self, model, messages, **kwargs):
        self.payloads.append(copy.deepcopy(messages))
        self.last_usage = {"prompt_tokens": 10, "completion_tokens": 1, "cost": 0.25}
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.fixture
def turn(tmp_path, monkeypatch):
    ctx = ContextManager(applied_context=4096, system_prompt="controlled")
    ctx.add_user("execute task")
    router = ToolRouter(workspace=tmp_path)
    delays = []
    def fast_wait(delay, **kwargs):
        delays.append(delay)
        retry.check_provider_retry_interrupt(**kwargs)
    monkeypatch.setattr(retry, "wait_provider_retry", fast_wait)
    monkeypatch.setattr(retry.random, "uniform", lambda *args: 0.0)
    return ctx, router, delays


def test_retry_preserves_tools_context_and_counts_every_call_cost_once(turn, tmp_path):
    ctx, router, delays = turn
    failed = OpenAIClientError("provider overloaded", status_code=503)
    client = ControlledClient([tool_message(path="marker.txt", content="one write"), failed, {"content": "done"}])
    calls = []
    budget = AutonomousBudget(max_llm_calls=10)
    token = cost_sink.set(lambda *args: calls.append(args))
    try:
        answer, log = run_one_turn(ctx, router, client, "controlled", budget=budget)
    finally:
        cost_sink.reset(token)
    assert answer == "done" and len(log) == 1
    assert (tmp_path / "marker.txt").read_text() == "one write"
    assert router._tool_call_count == 1
    assert len(client.payloads) == len(calls) == budget.snapshot().llm_calls == 3
    assert sum(call[3] for call in calls) == pytest.approx(0.75)
    assert client.payloads[1] == client.payloads[2]
    assert client.payloads[2][-1]["role"] == "tool"
    assert delays == [5.0]


def test_retry_exhaustion_preserves_actual_error_and_partial_tools(turn):
    ctx, router, delays = turn
    errors = [OpenAIClientError(f"overloaded {number}", status_code=503) for number in range(3)]
    client = ControlledClient([tool_message("calculate", expression="2+2"), *errors])
    budget = IterationBudget(max_total=10)
    with pytest.raises(OpenAIClientError) as raised:
        run_one_turn(ctx, router, client, "controlled", budget=budget)
    assert raised.value is errors[-1]
    assert len(raised.value.partial_tool_log) == 1
    assert len(client.payloads) == budget.consumed == 4
    assert delays == [5.0, 10.0]


def test_successful_tool_response_resets_consecutive_retry_counter(turn):
    ctx, router, delays = turn
    transient = lambda: OpenAIClientError("overloaded", status_code=503)
    client = ControlledClient([transient(), transient(), tool_message("calculate", expression="1+1"),
                               transient(), transient(), {"content": "done"}])
    answer, log = run_one_turn(ctx, router, client, "controlled")
    assert answer == "done" and len(log) == 1
    assert delays == [5.0, 10.0, 5.0, 10.0]
    assert len(client.payloads) == 6


@pytest.mark.parametrize("error", [
    OpenAIClientError("HTTP 401 unauthorized", status_code=401),
    OpenAIClientError("HTTP 403 forbidden", status_code=403),
    OpenAIClientError("HTTP 402 insufficient credits", status_code=402),
    OpenAIClientError("maximum context length", status_code=400),
    OpenAIClientError("model not found", status_code=404),
    ValueError("server overloaded"), TypeError("timeout"), AssertionError("503"),
    RuntimeError("ordinary programming failure"),
    RuntimeError("server overloaded"), OSError("disk IO error"),
])
def test_permanent_context_and_programming_errors_are_not_retried(turn, error):
    ctx, router, delays = turn
    client = ControlledClient([error])
    with pytest.raises(type(error)) as raised:
        run_one_turn(ctx, router, client, "controlled")
    assert raised.value is error
    assert len(client.payloads) == 1 and delays == []


@pytest.mark.parametrize("error", [
    OpenAIClientError("overloaded"), OpenAIClientError("HTTP 500 error", status_code=500),
    OpenAIClientError("rate limit", status_code=429), httpx.ConnectError("connection refused"),
    httpx.ReadTimeout("read timeout"), TimeoutError("timed out"),
])
def test_classified_transient_provider_failures_retry(turn, error):
    ctx, router, delays = turn
    client = ControlledClient([error, {"content": "recovered"}])
    assert run_one_turn(ctx, router, client, "controlled")[0] == "recovered"
    assert len(client.payloads) == 2 and delays == [5.0]


def test_budget_llm_limit_stops_before_sleep_or_another_call(turn):
    ctx, router, delays = turn
    budget = IterationBudget(max_total=1)
    client = ControlledClient([OpenAIClientError("overloaded", status_code=503)])
    with pytest.raises(ProviderRetryInterrupted, match="budget_exhausted"):
        run_one_turn(ctx, router, client, "controlled", budget=budget)
    assert len(client.payloads) == budget.consumed == 1 and delays == []


@pytest.mark.parametrize("reason", ["cancelled", "kill_switch", "budget_exhausted"])
def test_interrupt_after_provider_failure_keeps_tools_and_avoids_next_call(turn, reason):
    ctx, router, delays = turn
    client = ControlledClient([tool_message("calculate", expression="3+3"), OpenAIClientError("overloaded")])
    with pytest.raises(ProviderRetryInterrupted) as raised:
        run_one_turn(ctx, router, client, "controlled", interrupt_check=lambda: reason if len(client.payloads) >= 2 else None)
    assert raised.value.reason == reason
    assert len(raised.value.partial_tool_log) == 1
    assert len(client.payloads) == 2 and delays == []


def test_tool_transport_shaped_failure_does_not_retry_provider_or_replay_dispatch(turn, monkeypatch):
    ctx, router, delays = turn
    failure = httpx.ReadTimeout("tool server overloaded")
    calls = []
    def failed_dispatch(*args, **kwargs):
        calls.append(True)
        raise failure
    monkeypatch.setattr(router, "execute_native_call", failed_dispatch)
    client = ControlledClient([tool_message("calculate", expression="4+4")])
    with pytest.raises(httpx.ReadTimeout) as raised:
        run_one_turn(ctx, router, client, "controlled", tool_timeout_s=0)
    assert raised.value is failure
    assert len(client.payloads) == len(calls) == 1 and delays == []


def test_helper_wait_polls_budget_and_stop_with_bounded_slices(monkeypatch):
    now = [0.0]
    sleeps = []
    def sleep(duration):
        sleeps.append(duration)
        now[0] += duration
    monkeypatch.setattr(retry, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep))
    with pytest.raises(ProviderRetryInterrupted) as raised:
        retry.wait_provider_retry(5, interrupt_check=lambda: "cancelled" if now[0] >= 0.4 else None)
    assert raised.value.reason == "cancelled"
    assert sleeps == [0.2, 0.2]
    now[0] = 0
    sleeps.clear()
    with pytest.raises(ProviderRetryInterrupted) as raised:
        retry.wait_provider_retry(10, budget_exhausted=lambda: now[0] >= 0.2)
    assert raised.value.reason == "budget_exhausted" and sleeps == [0.2]


def test_retry_events_are_best_effort_and_resume_clears_cooldown_activity(turn):
    ctx, router, delays = turn
    events = []
    class Sink:
        def on_retry(self, attempt, delay_s):
            events.append((attempt, delay_s))
            raise RuntimeError("cosmetic sink unavailable")
        def on_round(self):
            events.append("resuming")
    client = ControlledClient([OpenAIClientError("overloaded"), {"content": "done"}])
    token = set_sink(Sink())
    try:
        assert run_one_turn(ctx, router, client, "controlled")[0] == "done"
        emit_provider_retry(1, 5)
    finally:
        reset_sink(token)
    assert events == [(1, 5.0), "resuming", (1, 5)]


@pytest.mark.parametrize("fail_after_request", [False, True])
def test_failed_governance_callback_never_grants_another_request(turn, fail_after_request):
    ctx, router, delays = turn
    client = ControlledClient([OpenAIClientError("overloaded")])
    failure = ValueError("governance unavailable")
    def broken_check():
        if not fail_after_request or client.payloads:
            raise failure
        return None
    with pytest.raises(ValueError) as raised:
        run_one_turn(ctx, router, client, "controlled", interrupt_check=broken_check)
    assert raised.value is failure
    assert len(client.payloads) == (1 if fail_after_request else 0)
    assert delays == []


def test_backoff_is_bounded_with_jitter(monkeypatch):
    monkeypatch.setattr(retry.random, "uniform", lambda *args: 0.75)
    assert retry.retry_delay_s(1) == 5.75
    assert retry.retry_delay_s(2) == 10.75
    assert retry.retry_delay_s(100) == 20


def test_unsupported_tools_keep_existing_bridge_downgrade_without_retry(turn):
    ctx, router, delays = turn
    class UnsupportedClient(ControlledClient):
        def chat_stream(self, model, messages):
            yield "bridge answer"
    client = UnsupportedClient([OpenAIClientError("HTTP 400 tools unsupported", status_code=400)])
    budget = IterationBudget(max_total=3)
    answer, tools = run_one_turn(ctx, router, client, "controlled", budget=budget)
    assert answer == "bridge answer" and tools == []
    assert len(client.payloads) == 1 and delays == []
    assert budget.consumed == 1
