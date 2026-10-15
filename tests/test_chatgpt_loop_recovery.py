"""Real Responses parsing and native execution recover without replaying tools."""

from __future__ import annotations

import json
from contextlib import contextmanager
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from bauer.agent import run_one_turn, run_one_turn_with_fallback
from bauer.autonomous_budget import AutonomousBudget
from bauer.chatgpt_backend import ChatGPTBackendClient, DEFAULT_CHATGPT_BASE
from bauer.context_manager import ContextManager
from bauer.core.events import EventBus
from bauer.core.runtime.autonomy import BudgetManager
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.resilience import RuntimeControl
from bauer.cost_meter import cost_sink
from bauer.openai_client import OpenAIClientError
from bauer.server import create_app
from bauer.session_store import SessionStore
from bauer.tool_router import ToolRouter
from tests.test_serve_loop import _wait_done

MODEL = "gpt-4o"


def append_event(call_id="call_append", content="performed once\n"):
    return {"type": "response.output_item.done", "item": {
        "type": "function_call", "call_id": call_id, "name": "append_file",
        "arguments": json.dumps({"path": "effect.txt", "content": content}),
    }}


def completed(input_tokens=100, output_tokens=50):
    return {"type": "response.completed", "response": {
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }}


def overloaded():
    return {"type": "error", "error": {"message": "The model is overloaded. Please try again later."}}


@pytest.fixture
def native(tmp_path, monkeypatch):
    from bauer import provider_retry

    client = ChatGPTBackendClient(access_token="controlled-token", model=MODEL)
    router = ToolRouter(workspace=tmp_path / "workspace", policy_root=tmp_path / "runtime")
    router.workspace.mkdir()
    ctx = ContextManager(applied_context=32768, system_prompt="Keep completed tool outputs.", provider="openai")
    ctx.add_user("Append the marker once and report the result.")
    waits = []
    monkeypatch.setattr(provider_retry, "wait_provider_retry", lambda delay, **kwargs: waits.append(delay))
    monkeypatch.setattr(provider_retry.random, "uniform", lambda *_args: 0)
    monkeypatch.setattr("httpx.post", lambda *_a, **_k: pytest.fail("unexpected chat/completions request"))
    return client, router, ctx, waits


def install_responses(monkeypatch, responses):
    bodies = []
    responses = iter(responses)

    @contextmanager
    def stream(method, url, **kwargs):
        assert (method, url) == ("POST", f"{DEFAULT_CHATGPT_BASE}/responses")
        bodies.append(kwargs["json"])
        response = next(responses)
        if isinstance(response, httpx.Response):
            yield response
        else:
            yield httpx.Response(200, text="\n\n".join("data: " + json.dumps(event) for event in response))

    monkeypatch.setattr("httpx.stream", stream)
    return bodies


def assert_preserved_output(body):
    outputs = [item for item in body["input"] if item.get("type") == "function_call_output"]
    assert outputs == [{"type": "function_call_output", "call_id": "call_append",
                        "output": "Acrescentado em 'effect.txt': 15 chars"}]


def test_native_responses_overload_retries_only_request_and_preserves_tool_result(native, monkeypatch):
    client, router, ctx, waits = native
    requests = install_responses(monkeypatch, [
        [append_event(), completed()],
        # A proposed call from the failed stream must never be dispatched.
        [append_event("uncommitted", "must not run\n"), overloaded()],
        [{"type": "response.output_text.delta", "delta": "Concluído com o arquivo preservado."}, completed(200, 80)],
    ])
    budget = AutonomousBudget(max_cost_usd=1, max_llm_calls=10)
    records = []
    token = cost_sink.set(lambda provider, model, usage, cost: records.append((provider, model, usage, cost)))
    try:
        answer, tools = run_one_turn(ctx, router, client, MODEL, budget=budget, tool_timeout_s=0)
    finally:
        cost_sink.reset(token)
    assert answer == "Concluído com o arquivo preservado."
    assert [tool["tool"] for tool in tools] == ["append_file"]
    assert (router.workspace / "effect.txt").read_text(encoding="utf-8") == "performed once\n"
    assert len(requests) == budget.snapshot().llm_calls == len(records) == 3
    assert waits == [5]
    assert_preserved_output(requests[1])
    assert requests[2] == requests[1]
    assert records[0][2]["input_tokens"] == 100
    assert records[1][2] == {} and records[1][3] == 0
    assert records[2][2]["input_tokens"] == 200
    assert sum(record[3] for record in records) == pytest.approx(0.00205)
    assert all("overloaded" not in str(message) for message in ctx.messages)


@pytest.mark.parametrize("failure", [
    httpx.Response(401, text='{"error":"invalid OAuth token"}'),
    httpx.Response(402, text='{"error":"insufficient credits"}'),
    [{"type": "error", "error": {"message": "maximum context length exceeded"}}],
])
def test_permanent_responses_error_preserves_completed_tool_without_retry(native, monkeypatch, failure):
    client, router, ctx, waits = native
    requests = install_responses(monkeypatch, [[append_event(), completed()], failure])
    with pytest.raises(OpenAIClientError) as raised:
        run_one_turn(ctx, router, client, MODEL, tool_timeout_s=0)
    assert len(requests) == 2 and waits == []
    assert len(raised.value.partial_tool_log) == 1
    assert (router.workspace / "effect.txt").read_text(encoding="utf-8") == "performed once\n"
    assert_preserved_output(requests[1])
    assert len([message for message in ctx.messages if message["role"] == "tool"]) == 1


def test_exhausted_responses_retry_preserves_partial_tools_once(native, monkeypatch):
    client, router, ctx, waits = native
    requests = install_responses(monkeypatch, [
        [append_event(), completed()], [overloaded()], [overloaded()], [overloaded()],
    ])
    with pytest.raises(OpenAIClientError, match="overloaded") as raised:
        run_one_turn(ctx, router, client, MODEL, tool_timeout_s=0)
    assert len(requests) == 4 and waits == [5, 10]
    assert len(raised.value.partial_tool_log) == 1
    assert requests[1] == requests[2] == requests[3]
    assert (router.workspace / "effect.txt").read_text(encoding="utf-8") == "performed once\n"


def test_exhausted_request_retry_then_configured_fallback_counts_old_tool_once(native, monkeypatch):
    client, router, ctx, waits = native
    fallback = ChatGPTBackendClient(access_token="controlled-fallback", model=MODEL)
    requests = install_responses(monkeypatch, [
        [append_event(), completed()], [overloaded()], [overloaded()], [overloaded()],
        [{"type": "response.output_text.delta", "delta": "Resposta pelo fallback."}, completed()],
    ])
    answer, tools = run_one_turn_with_fallback(ctx, router, client, MODEL, [(fallback, MODEL)], tool_timeout_s=0)
    assert answer == "Resposta pelo fallback."
    assert len(requests) == 5 and waits == [5, 10]
    assert [tool["tool"] for tool in tools] == ["append_file"]
    assert (router.workspace / "effect.txt").read_text(encoding="utf-8") == "performed once\n"


def make_loop_app(tmp_path, monkeypatch, router, client):
    config = tmp_path / "config.yaml"
    config.write_text(f"model:\n  provider: openai\n  name: {MODEL}\nkernel:\n  enabled: true\nloop:\n  max_cost_usd: 1\n", encoding="utf-8")
    monkeypatch.setattr("bauer.projects_registry._DEFAULT_REGISTRY", tmp_path / "projects.json")
    return create_app(model_name=MODEL, applied_context=32768, router=router, client=client,
                      system_prompt="Keep tool results.", config_path=config,
                      sessions_dir=tmp_path / "sessions", api_key="", rate_limit_requests=0)


def test_loop_responses_overload_completes_with_one_effect_and_durable_accounting(native, tmp_path, monkeypatch):
    client, router, _ctx, waits = native
    requests = install_responses(monkeypatch, [
        [append_event(), completed()], [overloaded()],
        [{"type": "response.output_text.delta", "delta": "Arquivo pronto."}, completed(200, 80)],
        [{"type": "response.output_text.delta", "delta": "Tarefa concluída."}, completed()],
        [{"type": "response.output_text.delta", "delta": "Confirmo a conclusão."}, completed()],
    ])
    with TestClient(make_loop_app(tmp_path, monkeypatch, router, client)) as http:
        response = http.post("/loop", json={"message": "Append the marker once."})
        assert response.status_code == 200, response.text
        run_id = response.json()["run_id"]
        state = _wait_done(http, run_id)
    run = RunManager(root=tmp_path / "runtime").get_run(run_id)
    assert state["state"] == run.status == "completed"
    assert state["stop_reason"] == "completed"
    assert run.tool_calls_count == state["tool_calls"] == 1
    assert run.llm_calls_count == len(requests) == 5
    assert run.usage_known is False  # The failed stream did not report usage.
    assert run.cost_estimate == pytest.approx(0.00355)
    assert BudgetManager(root=tmp_path / "runtime").status()["daily"]["used_usd"] == pytest.approx(run.cost_estimate)
    assert (router.workspace / "effect.txt").read_text(encoding="utf-8") == "performed once\n"
    assert_preserved_output(requests[1])
    assert requests[1] == requests[2]
    assert waits == [5]
    saved = SessionStore(tmp_path / "sessions").load(run.session_id)
    assert len([message for message in saved if message["role"] == "tool"]) == 1
    retries = [event for event in EventBus(root=tmp_path / "runtime").list_events(run_id=run_id)
               if event.event_type == "agent.retry"]
    assert len(retries) == 1
    assert retries[0].data["attempt"] == 1
    assert "overloaded" not in json.dumps(retries[0].data)


@pytest.mark.parametrize("reason", ["cancelled", "kill_switch"])
def test_loop_stop_during_responses_cooldown_does_not_send_retry(native, tmp_path, monkeypatch, reason):
    from bauer import provider_retry

    client, router, _ctx, _waits = native
    requests = install_responses(monkeypatch, [[append_event(), completed()], [overloaded()]])
    waiting, release = Event(), Event()

    def controlled_wait(_delay, **kwargs):
        waiting.set()
        assert release.wait(30), "test did not release controlled cooldown"
        provider_retry.check_provider_retry_interrupt(**kwargs)

    monkeypatch.setattr(provider_retry, "wait_provider_retry", controlled_wait)
    try:
        with TestClient(make_loop_app(tmp_path, monkeypatch, router, client)) as http:
            response = http.post("/loop", json={"message": "Append once and wait."})
            assert response.status_code == 200
            run_id = response.json()["run_id"]
            assert waiting.wait(30), "native request did not enter retry cooldown"
            live = http.get(f"/loop/{run_id}").json()
            assert "nova tentativa" in live["activity"]
            if reason == "cancelled":
                assert http.post(f"/loop/{run_id}/stop").status_code == 200
            else:
                RuntimeControl(root=tmp_path / "runtime").set_kill_switch(True)
            release.set()
            state = _wait_done(http, run_id)
    finally:
        release.set()
    run = RunManager(root=tmp_path / "runtime").get_run(run_id)
    assert state["stop_reason"] == reason
    assert state["state"] == "stopped" and run.status == "cancelled"
    assert len(requests) == run.llm_calls_count == 2
    assert run.tool_calls_count == state["tool_calls"] == 1
    assert run.cost_estimate == pytest.approx(0.00075)
    assert (router.workspace / "effect.txt").read_text(encoding="utf-8") == "performed once\n"


def test_loop_cost_cap_stops_before_next_native_request_with_preserved_effect(native, tmp_path, monkeypatch):
    client, router, _ctx, waits = native
    requests = install_responses(monkeypatch, [[append_event(), completed()]])
    with TestClient(make_loop_app(tmp_path, monkeypatch, router, client)) as http:
        response = http.post("/loop", json={"message": "Append once.", "max_cost_usd": 0.0006})
        assert response.status_code == 200
        run_id = response.json()["run_id"]
        state = _wait_done(http, run_id)
    run = RunManager(root=tmp_path / "runtime").get_run(run_id)
    assert "limite de custo estimado" in state["stop_reason"]
    assert run.status == "completed", run.error
    assert len(requests) == run.llm_calls_count == 1
    assert waits == []
    assert run.tool_calls_count == state["tool_calls"] == 1
    assert run.cost_estimate == pytest.approx(0.00075)
