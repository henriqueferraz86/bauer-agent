"""K03/K04: each bridge provider attempt retains its own partial usage."""

from contextlib import contextmanager

import pytest

from bauer.agent_response import collect_response, stream_to_sink
from bauer.cost_meter import cost_sink
from bauer.delta_stream import reset_sink, set_sink
from bauer.openai_client import OpenAIClient, OpenAIClientError


@contextmanager
def accounting(*, stream=False):
    reports = []
    cost_token = cost_sink.set(lambda provider, model, usage, cost: reports.append((usage, cost)))

    class Sink:
        def on_round(self):
            pass

        def on_delta(self, _chunk):
            pass

    delta_token = set_sink(Sink()) if stream else None
    try:
        yield reports
    finally:
        if delta_token is not None:
            reset_sink(delta_token)
        cost_sink.reset(cost_token)


class Client:
    host = "https://api.openai.com"

    def __init__(self, *, failed=False, usage=None):
        self.last_usage = {"prompt_tokens": 999_000, "completion_tokens": 999_000}
        self.failed = failed
        self.usage = usage

    def chat_stream(self, _model, _payload):
        if self.usage is not None:
            self.last_usage = self.usage
        yield "partial"
        if self.failed:
            raise OpenAIClientError("controlled failure", status_code=400)


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("failed", [False, True])
@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": 100, "completion_tokens": 0}])
def test_success_and_failure_report_only_current_usage(stream, failed, usage):
    with accounting(stream=stream) as reports:
        client = Client(failed=failed, usage=usage)
        if failed:
            with pytest.raises(OpenAIClientError, match="controlled failure"):
                collect_response(client, "gpt-4o", [])
        else:
            assert collect_response(client, "gpt-4o", []) == "partial"
    assert len(reports) == 1
    assert reports[0][1] == pytest.approx(0.00025 if usage else 0)
    assert bool(reports[0][0]) is bool(usage)


@pytest.mark.parametrize("wrapped", [False, True])
def test_injected_stream_collector_accounts_once(wrapped):
    def injected(client, model, payload):
        if wrapped:
            return stream_to_sink(client, model, payload)
        return list(client.chat_stream(model, payload))

    with accounting(stream=True) as reports:
        assert collect_response(Client(usage={"prompt_tokens": 100}), "gpt-4o", [],
                                stream_to_sink_fn=injected) == "partial"
    assert len(reports) == 1
    assert reports[0][1] == pytest.approx(0.00025)


@pytest.mark.parametrize("final_failure", [False, True])
@pytest.mark.parametrize("partial_usage", [False, True])
def test_stream_retries_account_each_attempt_once(monkeypatch, final_failure, partial_usage):
    monkeypatch.setattr("bauer.retry_utils.jittered_backoff", lambda *_a, **_k: 0)

    class RetryingClient(Client):
        attempts = 0

        def chat_stream(self, _model, _payload):
            self.attempts += 1
            if self.attempts < 3 or final_failure:
                if partial_usage:
                    self.last_usage = {"prompt_tokens": 100}
                raise OpenAIClientError("controlled retry", status_code=429)
            self.last_usage = {"prompt_tokens": 200}
            yield "done"

    with accounting(stream=True) as reports:
        client = RetryingClient()
        if final_failure:
            with pytest.raises(OpenAIClientError, match="controlled retry"):
                collect_response(client, "gpt-4o", [])
        else:
            assert collect_response(client, "gpt-4o", []) == "done"
    assert client.attempts == 3
    assert len(reports) == 3
    failed_cost = 0.00025 if partial_usage else 0
    assert [cost for _usage, cost in reports] == pytest.approx(
        [failed_cost, failed_cost, failed_cost if final_failure else 0.0005])


@pytest.mark.parametrize("failed", [False, True])
def test_chat_with_retry_override_is_preserved(failed):
    class RetryClient(OpenAIClient):
        def __init__(self):
            super().__init__(host="http://unused.invalid")

        def chat_with_retry(self, _model, _payload):
            self.last_usage = {"prompt_tokens": 100}
            if failed:
                raise OpenAIClientError("controlled retry failure", status_code=400)
            return ["from retry override"]

        def chat_stream(self, _model, _payload):
            pytest.fail("collect_response must preserve the chat_with_retry override")

    with accounting() as reports:
        if failed:
            with pytest.raises(OpenAIClientError, match="controlled retry failure"):
                collect_response(RetryClient(), "gpt-4o", [])
        else:
            assert collect_response(RetryClient(), "gpt-4o", []) == "from retry override"
    assert len(reports) == 1
    assert reports[0][1] == pytest.approx(0.00025)


@pytest.mark.parametrize("current_usage", [None, {"prompt_tokens": 100}])
def test_read_only_usage_does_not_reuse_a_previous_call(current_usage):
    class ReadOnlyClient(Client):
        def __init__(self):
            self._usage = {"prompt_tokens": 999_000}

        @property
        def last_usage(self):
            return self._usage

        def chat_stream(self, _model, _payload):
            if current_usage is not None:
                self._usage = current_usage
            raise OpenAIClientError("controlled read-only usage failure", status_code=400)
            yield  # generator contract

    with accounting() as reports:
        with pytest.raises(OpenAIClientError, match="read-only usage failure"):
            collect_response(ReadOnlyClient(), "gpt-4o", [])
    assert len(reports) == 1
    assert reports[0][1] == pytest.approx(0.00025 if current_usage else 0)


def test_stream_cancellation_retains_current_partial_usage():
    from bauer.voice_session import VoiceTurnCancelled

    class CancelledSink:
        cancelled = True

        def on_round(self):
            pass

    with accounting() as reports:
        delta_token = set_sink(CancelledSink())
        try:
            with pytest.raises(VoiceTurnCancelled):
                collect_response(Client(usage={"prompt_tokens": 100}), "gpt-4o", [])
        finally:
            reset_sink(delta_token)
    assert len(reports) == 1
    assert reports[0][1] == pytest.approx(0.00025)


@pytest.mark.parametrize("failed", [False, True])
def test_telemetry_failure_preserves_provider_result(monkeypatch, failed):
    def broken_report(*_a, **_k):
        raise RuntimeError("controlled telemetry failure")

    monkeypatch.setattr("bauer.cost_meter.report_llm_cost", broken_report)
    if failed:
        with pytest.raises(OpenAIClientError, match="controlled failure"):
            collect_response(Client(failed=True), "gpt-4o", [])
    else:
        assert collect_response(Client(), "gpt-4o", []) == "partial"


@pytest.mark.parametrize("final_failure", [False, True])
def test_openai_chat_with_retry_accounts_real_attempts(monkeypatch, final_failure):
    monkeypatch.setattr("bauer.retry_utils.jittered_backoff", lambda *_a, **_k: 0)

    class RetryClient(OpenAIClient):
        attempts = 0

        def __init__(self):
            super().__init__(host="http://unused.invalid")
            self.last_usage = {"prompt_tokens": 999_000}

        def chat_stream(self, _model, _payload):
            self.attempts += 1
            self.last_usage = {"prompt_tokens": 100}
            if self.attempts < 3 or final_failure:
                raise OpenAIClientError("controlled OpenAI retry", status_code=429)
            yield "done"

    with accounting() as reports:
        client = RetryClient()
        if final_failure:
            with pytest.raises(OpenAIClientError, match="controlled OpenAI retry"):
                collect_response(client, "gpt-4o", [])
        else:
            assert collect_response(client, "gpt-4o", []) == "done"
    assert client.attempts == 3
    assert len(reports) == 3
    assert [cost for _usage, cost in reports] == pytest.approx([0.00025] * 3)


@pytest.mark.parametrize("same_client", [False, True])
def test_nested_other_client_or_model_keeps_both_provider_calls(same_client):
    class OuterClient(Client):
        def chat_stream(self, model, _payload):
            if model == "outer":
                nested = self if same_client else Client(usage={"prompt_tokens": 200})
                assert collect_response(nested, "inner", []) == "partial"
                self.last_usage = {"prompt_tokens": 100}
                yield "outer result"
            else:
                self.last_usage = {"prompt_tokens": 200}
                yield "partial"

    with accounting() as reports:
        assert collect_response(OuterClient(), "outer", []) == "outer result"
    assert len(reports) == 2
    assert [usage["prompt_tokens"] for usage, _cost in reports] == [200, 100]


def test_copied_context_does_not_suppress_parent_provider_call():
    from contextvars import copy_context
    from bauer.agent_response import account_provider_call

    client = Client(usage={"prompt_tokens": 200})
    with accounting() as reports:
        with account_provider_call(client, "gpt-4o"):
            assert copy_context().run(collect_response, client, "gpt-4o", []) == "partial"
            client.last_usage = {"prompt_tokens": 100}
    assert len(reports) == 2
    assert [usage["prompt_tokens"] for usage, _cost in reports] == [200, 100]
