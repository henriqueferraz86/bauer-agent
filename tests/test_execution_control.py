"""Cooperative cancellation stops subsequent effects and preserves custody."""

from __future__ import annotations

import json
import threading
from contextvars import copy_context

import pytest

from bauer.agent import run_one_turn, run_one_turn_with_fallback
from bauer.context_manager import ContextManager
from bauer.execution_control import ExecutionControl, ExecutionControlError, current_control
from bauer.openai_client import OpenAIClient, OpenAIClientError
from bauer.provider_retry import ProviderRetryInterrupted
from bauer.tool_router import ToolRouter


class Native(OpenAIClient):
    def __init__(self, responses):
        super().__init__(host="http://unused.invalid", model="fake")
        self.responses = iter(responses)
        self.calls = 0

    def chat_with_tools(self, model, messages, **kwargs):
        self.calls += 1
        response = next(self.responses)
        if callable(response):
            return response()
        if isinstance(response, Exception):
            raise response
        return response


def native_batch(*names):
    return {"content": "", "tool_calls": [
        {"id": f"call-{number}", "type": "function", "function": {
            "name": name, "arguments": json.dumps({})}}
        for number, name in enumerate(names)
    ]}


def context():
    ctx = ContextManager(applied_context=4096, system_prompt="test")
    ctx.add_user("do the task")
    return ctx


def test_control_identity_scope_and_fail_closed_governance():
    first = ExecutionControl("r1", "s1")
    second = ExecutionControl("r2", "s2")
    assert current_control() is None
    with first.activate():
        assert current_control() is first
        with second.activate():
            assert current_control() is second
        assert current_control() is first
    assert current_control() is None
    failed = ExecutionControl(check_callback=lambda: (_ for _ in ()).throw(OSError("store unavailable")))
    with pytest.raises(ExecutionControlError) as error:
        failed.checkpoint()
    assert isinstance(error.value.__cause__, OSError)


@pytest.mark.parametrize("reason", ["cancelled", "kill_switch", "budget_exhausted"])
def test_stop_reason_never_turns_into_tool_error(reason, tmp_path):
    control = ExecutionControl(check_callback=lambda: reason)
    router = ToolRouter(tmp_path)
    with control.activate(), pytest.raises(ProviderRetryInterrupted) as raised:
        router.execute({"action": "write_file", "args": {"path": "never.txt", "content": "x"}})
    assert raised.value.reason == reason
    assert not (tmp_path / "never.txt").exists()


def test_native_batch_keeps_completed_log_and_stops_next_tool(tmp_path):
    control = ExecutionControl()
    router = ToolRouter(tmp_path)
    effects = []
    def first(args):
        effects.append("first")
        control.request_cancel()
        return "first completed"
    router._tools["first"] = {"fn": first, "args": {}, "description": "first"}
    router._tools["second"] = {"fn": lambda args: effects.append("second"), "args": {}, "description": "second"}
    client = Native([native_batch("first", "second")])
    ctx = context()
    with control.activate(), pytest.raises(ProviderRetryInterrupted) as raised:
        run_one_turn(ctx, router, client, "fake")
    assert effects == ["first"]
    assert [item["tool"] for item in raised.value.partial_tool_log] == ["first"]
    assert client.calls == control.tool_calls == 1
    assert [item["tool_call_id"] for item in ctx.messages if item["role"] == "tool"] == ["call-0", "call-1"]
    assert "Cancelada" in ctx.messages[-1]["content"]
    ctx.add_user("show what completed")
    next_client = Native([{"content": "first completed; second cancelled"}])
    assert "second cancelled" in run_one_turn(ctx, router, next_client, "fake")[0]
    assert effects == ["first"]


def test_bridge_batch_stops_after_first_effect(tmp_path):
    control = ExecutionControl()
    router = ToolRouter(tmp_path)
    effects = []
    def first(args):
        effects.append("first")
        control.request_cancel()
        return "first completed"
    router._tools["first"] = {"fn": first, "args": {}, "description": "first"}
    router._tools["second"] = {"fn": lambda args: effects.append("second"), "args": {}, "description": "second"}
    class Bridge:
        calls = 0
        def chat_stream(self, *args):
            self.calls += 1
            yield json.dumps([{"action": "first", "args": {}}, {"action": "second", "args": {}}])
    client = Bridge()
    with control.activate(), pytest.raises(ProviderRetryInterrupted) as raised:
        run_one_turn(context(), router, client, "fake")
    assert effects == ["first"]
    assert len(raised.value.partial_tool_log) == 1
    assert client.calls == 1


def test_cancelled_provider_error_never_retries_or_falls_back(tmp_path):
    control = ExecutionControl()
    def fail():
        control.request_cancel()
        raise OpenAIClientError("overloaded", status_code=503)
    client, fallback = Native([fail]), Native([{"content": "wrong"}])
    with control.activate(), pytest.raises(ProviderRetryInterrupted):
        run_one_turn_with_fallback(context(), ToolRouter(tmp_path), client, "fake", [(fallback, "fallback")])
    assert client.calls == 1
    assert fallback.calls == 0


def test_cancel_after_native_response_blocks_its_proposed_tools(tmp_path):
    control = ExecutionControl()
    def reply():
        control.request_cancel()
        return native_batch("write_file")
    client = Native([reply])
    router = ToolRouter(tmp_path)
    with control.activate(), pytest.raises(ProviderRetryInterrupted):
        run_one_turn(context(), router, client, "fake")
    assert control.tool_calls == 0


def test_cancel_during_bridge_stream_preserves_cost_and_blocks_recovery(tmp_path):
    from bauer.channel_governance import TurnMetrics

    control = ExecutionControl()
    recorder = TurnMetrics()
    class Bridge:
        calls = 0
        last_usage = None
        def chat_stream(self, *args):
            self.calls += 1
            self.last_usage = {"prompt_tokens": 10, "completion_tokens": 1, "cost": .1}
            yield "part"
            control.request_cancel()
            yield "never published"
    client = Bridge()
    with control.activate(), recorder.context("s", "r"), pytest.raises(ProviderRetryInterrupted):
        run_one_turn(context(), ToolRouter(tmp_path), client, "fake")
    assert client.calls == 1
    assert recorder.result()["llm_calls_count"] == 1


def test_cancelled_recovery_and_compression_never_call_provider(monkeypatch):
    from bauer.agent_response import recover_empty_response
    from bauer.context_manager import _summarize_llm_structured

    control = ExecutionControl()
    control.request_cancel()
    control.close()
    calls = []
    class Client:
        def chat_stream(self, *args):
            calls.append(1)
            yield "unexpected"
    with control.activate():
        with pytest.raises(ProviderRetryInterrupted):
            recover_empty_response(Client(), "fake", context())
        with pytest.raises(ProviderRetryInterrupted):
            _summarize_llm_structured(Client(), "fake", [{"role": "user", "content": "task"}])
    assert calls == []


def test_cancel_inside_auxiliary_stream_propagates_and_records_cost(monkeypatch):
    from bauer.auxiliary_client import call_aux_text
    from bauer.channel_governance import TurnMetrics
    control = ExecutionControl()
    recorder = TurnMetrics()
    class Client:
        last_usage = None
        def chat_stream(self, *args):
            self.last_usage = {"prompt_tokens": 10, "completion_tokens": 1, "cost": .1}
            yield "first"
            control.request_cancel()
            yield "second"
    monkeypatch.setattr("bauer.auxiliary_client.get_text_auxiliary_client", lambda *a: (Client(), "fake"))
    with control.activate(), recorder.context("s", "r"), pytest.raises(ProviderRetryInterrupted):
        call_aux_text("approval_model", [])
    assert recorder.result()["llm_calls_count"] == 1


def test_router_checks_again_after_policy_approval_even_when_policy_was_allowed(tmp_path, monkeypatch):
    control = ExecutionControl()
    router = ToolRouter(tmp_path, policy_enabled=True)
    def approve(*args):
        control.request_cancel()
        return True
    monkeypatch.setattr(router, "_evaluate_runtime_policy", approve)
    with control.activate(), pytest.raises(ProviderRetryInterrupted):
        router.execute({"action": "write_file", "args": {"path": "never.txt", "content": "x"}})
    assert not (tmp_path / "never.txt").exists()


def test_tool_limit_allows_exactly_the_reserved_number(tmp_path):
    control = ExecutionControl()
    control.max_tool_calls = 1
    router = ToolRouter(tmp_path)
    with control.activate():
        assert router.execute({"action": "calculate", "args": {"expression": "1+1"}})
        with pytest.raises(ProviderRetryInterrupted, match="budget_exhausted"):
            router.execute({"action": "write_file", "args": {"path": "never.txt", "content": "x"}})
    assert control.tool_calls == 1
    assert not (tmp_path / "never.txt").exists()


def test_controlled_tool_timeout_keeps_owner_waiting_until_real_exit():
    from bauer.tool_timeout import call_with_timeout
    control = ExecutionControl()
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    outcome = []
    def tool():
        entered.set()
        release.wait(5)
        return "completed side effect"
    def owner():
        with control.activate():
            outcome.append(call_with_timeout(tool, .02))
        returned.set()
    worker = threading.Thread(target=owner)
    worker.start()
    assert entered.wait(2)
    assert control.cancel_event.wait(2)
    assert not returned.is_set()
    release.set()
    worker.join(2)
    assert outcome == [("completed side effect", True)]


def test_registered_processes_are_terminated_for_cancel_and_late_registration():
    control = ExecutionControl()
    process, calls = object(), []
    control.register_process(process, lambda: calls.append("first"))
    control.request_cancel()
    control.close()
    assert calls == ["first"]
    control.unregister_process(process)
    control.register_process(object(), lambda: calls.append("late"))
    control.close()
    assert calls == ["first", "late"]


def test_close_without_cancellation_leaves_background_process_running():
    # A deliberately detached background process (process action=start,
    # run_command background=True) must survive the turn that created it —
    # close() only settles cleanup a real cancellation already requested.
    control = ExecutionControl()
    calls = []
    control.register_process(object(), lambda: calls.append("killed"))
    control.close()
    assert calls == []
    assert control.check() is None


def test_execution_control_propagates_only_when_context_is_copied():
    control = ExecutionControl("run", "session")
    observed = []
    with control.activate():
        inherited = copy_context()
        worker = threading.Thread(target=lambda: inherited.run(lambda: observed.append(current_control())))
        worker.start()
        worker.join()
    assert observed == [control]
