"""The governed HTTP loop keeps cancellation and cost guards during cooldown."""

import json
from threading import Event

import pytest
from fastapi.testclient import TestClient

from bauer import provider_retry
from bauer.core.runtime.resilience import RuntimeControl
from bauer.core.runtime.run_manager import RunManager
from bauer.openai_client import OpenAIClient, OpenAIClientError
from tests.test_serve_loop import _app, _cfg, _wait_done


class OverloadedClient(OpenAIClient):
    def __init__(self, *, tool_first=False):
        super().__init__(host="http://unused.invalid", model="controlled")
        self.calls = 0
        self.tool_first = tool_first

    def chat_with_tools(self, model, messages, **kwargs):
        self.calls += 1
        self.last_usage = {"prompt_tokens": 10, "completion_tokens": 1, "cost": 0.4}
        if self.tool_first and self.calls == 1:
            return {"content": "", "tool_calls": [{"id": "write-once", "type": "function", "function": {
                "name": "write_file", "arguments": json.dumps({"path": "once.txt", "content": "done"}),
            }}]}
        raise OpenAIClientError("Our servers are currently overloaded. Please try again later.")


@pytest.mark.parametrize("stop", ["cancelled", "kill_switch"])
def test_stop_during_cooldown_never_makes_another_provider_call(tmp_path, monkeypatch, stop):
    waiting, resume = Event(), Event()

    def controlled_wait(delay, **kwargs):
        waiting.set()
        assert resume.wait(timeout=10)
        provider_retry.check_provider_retry_interrupt(**kwargs)

    monkeypatch.setattr(provider_retry, "wait_provider_retry", controlled_wait)
    provider = OverloadedClient()
    with TestClient(_app(tmp_path, _cfg(tmp_path), provider)) as client:
        started = client.post("/loop", json={"message": "continue"})
        assert started.status_code == 200
        run_id = started.json()["run_id"]
        try:
            assert waiting.wait(timeout=10)
            status = client.get(f"/loop/{run_id}").json()
            assert "nova tentativa" in status["activity"]
            if stop == "cancelled":
                assert client.post(f"/loop/{run_id}/stop").status_code == 200
            else:
                RuntimeControl(root=tmp_path / "runtime").set_kill_switch(True)
        finally:
            resume.set()
        final = _wait_done(client, run_id)
        assert final["state"] == "stopped"
        assert final["stop_reason"] == stop
        run = RunManager(root=tmp_path / "runtime").get_run(run_id)
        assert run.status == "cancelled"
        assert run.llm_calls_count == 1
        assert provider.calls == 1


def test_failed_request_cost_exhaustion_stops_before_retry(tmp_path, monkeypatch):
    def unexpected_wait(*args, **kwargs):
        pytest.fail("must not wait or retry after cost exhaustion")

    monkeypatch.setattr(provider_retry, "wait_provider_retry", unexpected_wait)
    config = _cfg(tmp_path)
    config.write_text(config.read_text(encoding="utf-8").replace("max_cost_usd: 1.0", "max_cost_usd: 0.7"), encoding="utf-8")
    provider = OverloadedClient(tool_first=True)
    with TestClient(_app(tmp_path, config, provider)) as client:
        started = client.post("/loop", json={"message": "continue"})
        assert started.status_code == 200
        run_id = started.json()["run_id"]
        final = _wait_done(client, run_id)
    assert final["state"] == "limit"
    assert "custo" in final["stop_reason"]
    assert final["tool_calls"] == 1
    assert final["cost_usd"] == pytest.approx(0.8)
    assert provider.calls == 2
    assert (tmp_path / "ws" / "once.txt").read_text() == "done"
    run = RunManager(root=tmp_path / "runtime").get_run(run_id)
    assert run.status == "completed"
    assert run.tool_calls_count == 1
    assert run.llm_calls_count == 2
