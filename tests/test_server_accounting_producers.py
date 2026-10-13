"""K02/K03/K04 integration at HTTP producer boundaries, with controlled turns."""

from threading import Event
import time

import pytest
from fastapi.testclient import TestClient

from bauer.core.kernel.evaluator import Evaluator, GateResult
from bauer.core.runtime.autonomy import BudgetManager
from bauer.core.runtime.run_manager import RunManager
from bauer.cost_meter import cost_sink
from bauer.server import create_app
from bauer.session_store import SessionStore
from bauer.tool_router import ToolRouter


def build_app(tmp_path, monkeypatch, turn, governed=True, replan=False):
    monkeypatch.setattr("bauer.agent.run_one_turn", turn)
    if replan:
        class Gate:
            name = "once"
            calls = 0

            def check(self, **_kwargs):
                self.calls += 1
                return GateResult(self.name, self.calls > 1, "try again")

        monkeypatch.setattr("bauer.core.kernel.kernel.evaluator_from_config",
                            lambda *_a, **_k: Evaluator([Gate()], max_replans=1))
    config = tmp_path / "config.yaml"
    config.write_text(f"model:\n  provider: ollama\n  name: controlled\nkernel:\n  enabled: {str(governed).lower()}\n")
    return create_app(model_name="controlled", applied_context=4096, client=object(),
                      router=ToolRouter(workspace=tmp_path), system_prompt="",
                      sessions_dir=tmp_path / "sessions", config_path=config, rate_limit_requests=0)


def terminal_run(tmp_path, run_id=None):
    manager = RunManager(root=tmp_path / "runtime")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        runs = manager.list_runs()
        run = manager.get_run(run_id) if run_id else (runs[0] if runs else None)
        if run and run.status in {"completed", "failed", "cancelled"}:
            return run
        time.sleep(0.01)
    pytest.fail("controlled worker did not reach terminal")


@pytest.mark.parametrize("transport", ["chat", "stream", "loop", "v1", "v1-stream"])
@pytest.mark.parametrize("governed", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_turn_preserves_known_consumption(tmp_path, monkeypatch, transport, governed, failed):
    def turn(ctx, *_a, **_k):
        cost_sink.get()("openai", "controlled", {"prompt_tokens": 10}, 0.4)
        if failed:
            raise ValueError("controlled failure")
        ctx.add_assistant("controlled success")
        return "controlled success", []

    with TestClient(build_app(tmp_path, monkeypatch, turn, governed=governed)) as client:
        if transport == "stream":
            response = client.get("/stream", params={"message": "controlled"})
        elif transport.startswith("v1"):
            response = client.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": "controlled"}], "stream": transport == "v1-stream"})
        else:
            response = client.post(f"/{transport}", json={"message": "controlled"})
        assert response.status_code in {200, 500}
        run = terminal_run(tmp_path)
    calls = 2 if transport == "loop" and not failed else 1
    assert run.status == ("failed" if failed else "completed")
    assert run.cost_estimate == pytest.approx(0.4 * calls)
    assert run.llm_calls_count == calls
    assert run.usage_known is True
    assert BudgetManager(root=tmp_path / "runtime").status()["daily"]["used_usd"] == pytest.approx(0.4 * calls)


@pytest.mark.parametrize("transport", ["chat", "loop", "v1"])
def test_replan_counts_each_attempt_once_in_run_and_ledger(tmp_path, monkeypatch, transport):
    calls = []

    def turn(ctx, *_a, **_k):
        calls.append(1)
        cost_sink.get()("openai", "controlled", {"prompt_tokens": 10}, 0.2 if transport == "loop" else 0.4)
        ctx.add_assistant("controlled done")
        return "controlled done", []

    with TestClient(build_app(tmp_path, monkeypatch, turn, replan=True)) as client:
        payload = {"messages": [{"role": "user", "content": "controlled"}]} if transport == "v1" else {"message": "controlled"}
        response = client.post("/v1/chat/completions" if transport == "v1" else f"/{transport}", json=payload)
        assert response.status_code == 200
        run = terminal_run(tmp_path)
    assert run.status == "completed"
    assert len(calls) == (4 if transport == "loop" else 2)
    assert run.llm_calls_count == len(calls)
    assert run.cost_estimate == pytest.approx(0.8)
    assert BudgetManager(root=tmp_path / "runtime").status()["daily"]["used_usd"] == pytest.approx(0.8)


def test_stream_cancelled_before_session_lock_does_not_execute(tmp_path, monkeypatch):
    executed = Event()
    original = SessionStore.serialized

    def serialized(self, sid):
        def decorate(worker):
            wrapped = original(self, sid)(worker)

            def cancelled_worker():
                manager = RunManager(root=tmp_path / "runtime")
                manager.update_run(manager.list_runs()[0].id, status="cancelled")
                return wrapped()

            return cancelled_worker
        return decorate

    monkeypatch.setattr(SessionStore, "serialized", serialized)
    with TestClient(build_app(tmp_path, monkeypatch, lambda *_a, **_k: (executed.set() or "bad", []))) as client:
        response = client.get("/stream", params={"message": "controlled", "session_id": "same"})
        assert response.status_code == 200
    assert not executed.is_set()
    assert terminal_run(tmp_path).status == "cancelled"
    assert SessionStore(tmp_path / "sessions").load("same") == []


@pytest.mark.parametrize("transport", ["chat", "stream", "loop", "v1", "v1-stream"])
@pytest.mark.parametrize("governed", [False, True])
def test_session_save_failure_closes_run_and_preserves_consumption(tmp_path, monkeypatch, transport, governed):
    def turn(ctx, *_a, **_k):
        cost_sink.get()("openai", "controlled", {"prompt_tokens": 10}, 0.4)
        ctx.add_assistant("completed turn")
        return "completed turn", [{"tool": "read_file", "result": "controlled"}]

    app = build_app(tmp_path, monkeypatch, turn, governed=governed)

    def fail_save(*_a, **_k):
        raise OSError("controlled save failure")

    monkeypatch.setattr(SessionStore, "save", fail_save)
    with TestClient(app, raise_server_exceptions=False) as client:
        if transport == "stream":
            response = client.get("/stream", params={"message": "controlled"})
        elif transport.startswith("v1"):
            response = client.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": "controlled"}], "stream": transport == "v1-stream"})
        else:
            response = client.post(f"/{transport}", json={"message": "controlled"})
        assert response.status_code == (500 if transport in {"chat", "v1"} else 200)
        run = terminal_run(tmp_path)
        if transport == "loop":
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = client.get(f"/loop/{run.id}").json()
                if state["state"] != "running":
                    break
                time.sleep(0.01)
            assert state["state"] == "failed"
            assert state["tool_calls"] == 1
    assert run.status == "failed"
    assert run.cost_estimate == pytest.approx(0.4)
    assert run.llm_calls_count == 1
    assert run.tool_calls_count == 1
    assert run.usage_known is True
    assert BudgetManager(root=tmp_path / "runtime").status()["daily"]["used_usd"] == pytest.approx(0.4)


@pytest.mark.parametrize("transport", ["chat", "stream", "loop", "v1", "v1-stream"])
@pytest.mark.parametrize("governed", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_real_tool_survives_later_provider_failure(tmp_path, monkeypatch, transport, governed, native):
    from bauer.agent import run_one_turn
    from tests.test_turn_partial_accounting import BridgeClient, NativeClient

    # Exercise attempts without real retry sleeps; streamed 400 is classified
    # UNKNOWN by the existing classifier and legitimately retries twice.
    monkeypatch.setattr("bauer.retry_utils.jittered_backoff", lambda *_a, **_k: 0)
    controlled = NativeClient() if native else BridgeClient()
    (tmp_path / "marker.txt").write_text("marker", encoding="utf-8")
    app = build_app(tmp_path, monkeypatch,
                    lambda ctx, router, _client, model, **kw: run_one_turn(ctx, router, controlled, model, **kw),
                    governed=governed)
    with TestClient(app) as client:
        if transport == "stream":
            response = client.get("/stream", params={"message": "read marker"})
        elif transport.startswith("v1"):
            response = client.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": "read marker"}], "stream": transport == "v1-stream"})
        else:
            response = client.post(f"/{transport}", json={"message": "read marker"})
        assert response.status_code == (200 if transport in {"stream", "loop", "v1-stream"} else 500)
        run = terminal_run(tmp_path)
    assert run.status == "failed"
    assert run.tool_calls_count == 1
    assert run.llm_calls_count == controlled.calls
    assert run.llm_calls_count >= 2
    assert run.usage_known is True
    assert run.cost_estimate > 0
    assert BudgetManager(root=tmp_path / "runtime").status()["daily"]["used_usd"] == pytest.approx(run.cost_estimate)


def test_loop_replan_preserves_tools_in_run_and_live_status(tmp_path, monkeypatch):
    calls = []

    def turn(ctx, *_a, **_k):
        calls.append(1)
        cost_sink.get()("openai", "controlled", {"prompt_tokens": 10}, 0.1)
        ctx.add_assistant("done")
        tools = [{"tool": "read_file", "result": "controlled"}] if len(calls) in {1, 4} else []
        return "done", tools

    with TestClient(build_app(tmp_path, monkeypatch, turn, replan=True)) as client:
        response = client.post("/loop", json={"message": "controlled"})
        assert response.status_code == 200
        run = terminal_run(tmp_path)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get(f"/loop/{run.id}").json()
            if state["state"] != "running":
                break
            time.sleep(0.01)
    assert len(calls) == 6
    assert run.status == "completed"
    assert run.tool_calls_count == 2
    assert state["state"] == "completed"
    assert state["tool_calls"] == 2
    assert run.llm_calls_count == 6
    assert run.cost_estimate == pytest.approx(0.6)
    assert BudgetManager(root=tmp_path / "runtime").status()["daily"]["used_usd"] == pytest.approx(0.6)
