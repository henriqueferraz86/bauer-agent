"""WEB-05/06/07: transcript and execution contract of OpenAI transports."""

import copy
import json

import pytest
from fastapi.testclient import TestClient

from bauer.core.runtime.run_manager import RunManager
from bauer.openai_client import OpenAIClient, OpenAIClientError
from bauer.server import create_app
from bauer.session_store import SessionStore
from bauer.tool_router import ToolRouter


def make_app(tmp_path, client, *, governed=False, fallback_clients=None):
    config = tmp_path / "config.yaml"
    config.write_text(
        f"model:\n  name: gpt-4o\n  provider: openai\nkernel:\n  enabled: {str(governed).lower()}\n",
        encoding="utf-8",
    )
    return create_app(
        model_name="gpt-4o", applied_context=4096,
        router=ToolRouter(workspace=tmp_path), client=client,
        system_prompt="SERVER_INSTRUCTION", sessions_dir=tmp_path / "sessions",
        config_path=config, rate_limit_requests=0, fallback_clients=fallback_clients,
    )


class ControlledClient(OpenAIClient):
    def __init__(self, *, native=False, failure=False, failure_usage=None):
        super().__init__(host="http://unused.invalid", model="gpt-4o")
        self._provider = "openai"
        self.native = native
        self.failure = failure
        self.failure_usage = failure_usage
        self.calls = []

    def chat_with_tools(self, model, messages, tools, **_kwargs):
        if self.failure:
            if self.failure_usage is not None:
                self.last_usage = self.failure_usage
            raise OpenAIClientError("controlled provider unavailable", status_code=429)
        self.calls.append((model, copy.deepcopy(messages), copy.deepcopy(tools)))
        self.last_usage = {"prompt_tokens": 1000, "completion_tokens": 100}
        if self.native and len(self.calls) == 1:
            return {"content": "", "tool_calls": [{"id": "read", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps({"path": "marker.txt"}),
            }}]}
        return {"content": "CONTROLLED_OK", "tool_calls": []}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("governed", [False, True])
def test_batch_and_stream_share_native_tools_and_accounting(tmp_path, stream, governed):
    (tmp_path / "marker.txt").write_text("marker", encoding="utf-8")
    client = ControlledClient(native=True)
    app = make_app(tmp_path, client, governed=governed)
    with TestClient(app) as http:
        response = http.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "read marker.txt"}], "stream": stream,
        })
    assert response.status_code == 200
    assert "CONTROLLED_OK" in response.text
    assert len(client.calls) == 2
    assert all("read_file" in [tool["function"]["name"] for tool in call[2]] for call in client.calls)
    run = RunManager(root=tmp_path / "runtime").get_run(response.headers["X-Bauer-Run-ID"])
    assert run.status == "completed"
    assert run.tool_calls_count == 1
    assert run.cost_estimate == pytest.approx(0.007)
    if not stream:
        assert response.json()["usage"] == {"prompt_tokens": 2000, "completion_tokens": 200, "total_tokens": 2200}
    if governed:
        import sqlite3
        with sqlite3.connect(tmp_path / "runtime" / "budget_ledger.sqlite3") as connection:
            state, actual = connection.execute("SELECT state, actual_usd FROM budget_runs WHERE run_id = ?", (run.id,)).fetchone()
        assert state == "settled"
        assert actual == pytest.approx(0.007)


@pytest.mark.parametrize("stream", [False, True])
def test_openai_transport_uses_shared_provider_fallback(tmp_path, stream):
    primary, fallback = ControlledClient(failure=True), ControlledClient()
    app = make_app(tmp_path, primary, fallback_clients=[(fallback, "gpt-4o")])
    response = TestClient(app).post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hi"}], "stream": stream,
    })
    assert response.status_code == 200
    assert "CONTROLLED_OK" in response.text
    assert len(fallback.calls) == 1


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_fallback_counts_current_partial_usage_without_reusing_stale_usage(tmp_path, monkeypatch, stream, partial):
    from bauer import provider_retry

    monkeypatch.setattr(provider_retry, "wait_provider_retry",
                        lambda _delay, **kwargs: provider_retry.check_provider_retry_interrupt(**kwargs))
    usage = {"prompt_tokens": 100, "completion_tokens": 0} if partial else None
    primary, fallback = ControlledClient(failure=True, failure_usage=usage), ControlledClient()
    primary_attempts = []
    original_request = primary.chat_with_tools

    def counted_request(*args, **kwargs):
        primary_attempts.append(True)
        return original_request(*args, **kwargs)

    monkeypatch.setattr(primary, "chat_with_tools", counted_request)
    primary.last_usage = {"prompt_tokens": 999_000, "completion_tokens": 999_000}
    app = make_app(tmp_path, primary, governed=True, fallback_clients=[(fallback, "gpt-4o")])
    response = TestClient(app).post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hello"}], "stream": stream,
    })
    assert response.status_code == 200
    assert "CONTROLLED_OK" in response.text
    run = RunManager(root=tmp_path / "runtime").get_run(response.headers["X-Bauer-Run-ID"])
    assert len(primary_attempts) == 3  # Original request and two bounded retries.
    assert len(fallback.calls) == 1
    assert run.cost_estimate == pytest.approx(0.0035 + (3 * 0.00025 if partial else 0))
    assert run.llm_calls_count == 4
    assert run.usage_known is partial


class ControlledBridgeClient:
    _provider = "openai"

    def __init__(self):
        self.calls = []

    def chat_stream(self, model, messages):
        self.calls.append((model, copy.deepcopy(messages)))
        self.last_usage = {"prompt_tokens": 1000, "completion_tokens": 100}
        if len(self.calls) == 1:
            yield json.dumps({"action": "read_file", "args": {"path": "marker.txt"}})
        else:
            yield "CONTROLLED_BRIDGE_OK"


@pytest.mark.parametrize("stream", [False, True])
def test_batch_and_stream_share_bridge_tools(tmp_path, stream):
    (tmp_path / "marker.txt").write_text("marker", encoding="utf-8")
    client = ControlledBridgeClient()
    response = TestClient(make_app(tmp_path, client, governed=True)).post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "read marker.txt"}], "stream": stream,
    })
    assert response.status_code == 200
    assert "CONTROLLED_BRIDGE_OK" in response.text
    assert len(client.calls) == 2
    run = RunManager(root=tmp_path / "runtime").get_run(response.headers["X-Bauer-Run-ID"])
    assert run.status == "completed"
    assert run.tool_calls_count == 1
    assert run.cost_estimate == pytest.approx(0.007)


@pytest.mark.parametrize("stream", [False, True])
def test_full_transcript_does_not_duplicate_persisted_history(tmp_path, stream):
    client = ControlledClient()
    http = TestClient(make_app(tmp_path, client))
    transcript = [{"role": "user", "content": "first"}]
    first = http.post("/v1/chat/completions", json={"session_id": "stable", "messages": transcript})
    assert first.status_code == 200
    transcript += [{"role": "assistant", "content": "CONTROLLED_OK"}, {"role": "user", "content": "second"}]
    second = http.post("/v1/chat/completions", json={"messages": transcript, "stream": stream}, headers={"X-Hermes-Session-Id": "stable"})
    assert second.status_code == 200
    assert client.calls[-1][1][1:] == transcript
    assert SessionStore(tmp_path / "sessions").load("stable") == transcript + [{"role": "assistant", "content": "CONTROLLED_OK"}]


def test_incremental_mode_retains_prior_history(tmp_path):
    client = ControlledClient()
    http = TestClient(make_app(tmp_path, client))
    for text in ("first", "second"):
        response = http.post("/v1/chat/completions", json={
            "session_id": "stable", "history_mode": "incremental", "messages": [{"role": "user", "content": text}],
        })
        assert response.status_code == 200
    assert [m["content"] for m in SessionStore(tmp_path / "sessions").load("stable")] == ["first", "CONTROLLED_OK", "second", "CONTROLLED_OK"]


@pytest.mark.parametrize("field, value", [("model", "different"), ("max_tokens", 7), ("temperature", 0.123)])
@pytest.mark.parametrize("stream", [False, True])
def test_unsupported_parameters_rejected_before_run(tmp_path, field, value, stream):
    client = ControlledClient()
    response = TestClient(make_app(tmp_path, client)).post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hello"}], field: value, "stream": stream,
    })
    assert response.status_code == 422
    assert client.calls == []
    assert RunManager(root=tmp_path / "runtime").list_runs() == []
    assert SessionStore(tmp_path / "sessions").list_sessions() == []


@pytest.mark.parametrize("stream", [False, True])
def test_active_model_and_request_instructions_reach_provider(tmp_path, stream):
    client = ControlledClient()
    response = TestClient(make_app(tmp_path, client)).post("/v1/chat/completions", json={
        "model": "gpt-4o", "stream": stream,
        "messages": [{"role": "system", "content": "CLIENT_SYSTEM"},
                     {"role": "developer", "content": "CLIENT_DEVELOPER"},
                     {"role": "user", "content": "hello"}],
    })
    assert response.status_code == 200
    model, payload, _tools = client.calls[0]
    assert model == "gpt-4o"
    assert all(marker in payload[0]["content"] for marker in ("CLIENT_SYSTEM", "CLIENT_DEVELOPER"))
    assert len(payload[0]["content"]) > len("CLIENT_SYSTEM\n\nCLIENT_DEVELOPER")


def test_unknown_message_role_is_rejected(tmp_path):
    client = ControlledClient()
    response = TestClient(make_app(tmp_path, client)).post("/v1/chat/completions", json={"messages": [{"role": "invented", "content": "hi"}]})
    assert response.status_code == 422
    assert client.calls == []
