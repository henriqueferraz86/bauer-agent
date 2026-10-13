"""WEB-04: a session owns its complete turn, including background transports."""

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
from threading import Event

import pytest
from fastapi.testclient import TestClient

from bauer.server import create_app
from bauer.session_store import SessionStore
from bauer.tool_router import ToolRouter


def make_app(tmp_path, monkeypatch, turn, *, governed=False):
    monkeypatch.setattr("bauer.agent.run_one_turn", turn)
    config = tmp_path / "config.yaml"
    config.write_text(
        f"model:\n  name: controlled\n  provider: ollama\nkernel:\n  enabled: {str(governed).lower()}\n",
        encoding="utf-8",
    )
    return create_app(
        model_name="controlled", applied_context=4096,
        router=ToolRouter(workspace=tmp_path), client=object(),
        system_prompt="", sessions_dir=tmp_path / "sessions",
        config_path=config, rate_limit_requests=0,
    )


@pytest.mark.parametrize("governed", [False, True])
@pytest.mark.parametrize("first_transport", ["chat", "stream", "loop", "openai-batch", "openai-stream"])
def test_same_session_preserves_both_turns(tmp_path, monkeypatch, governed, first_transport):
    first_entered, release_first, second_entered = Event(), Event(), Event()
    seen = []

    def turn(ctx, *_args, **_kwargs):
        content = ctx.messages[-1]["content"]
        if "FIRST" in content:
            first_entered.set()
            assert release_first.wait(5)
            answer = "answer-FIRST"
        else:
            second_entered.set()
            answer = "answer-SECOND"
        seen.append(list(ctx.messages))
        ctx.add_assistant(answer)
        return answer, []

    app = make_app(tmp_path, monkeypatch, turn, governed=governed)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        def first():
            if first_transport.startswith("openai"):
                return client.post("/v1/chat/completions", json={
                    "messages": [{"role": "user", "content": "FIRST"}],
                    "history_mode": "incremental", "session_id": "shared",
                    "stream": first_transport == "openai-stream",
                })
            if first_transport == "stream":
                return client.get("/stream", params={"message": "FIRST", "session_id": "shared"})
            return client.post(f"/{first_transport}", json={"message": "FIRST", "session_id": "shared"})

        a = pool.submit(first)
        try:
            assert first_entered.wait(5)
            b = pool.submit(client.post, "/chat", json={"message": "SECOND", "session_id": "shared"})
            # The second executor cannot enter while the first owns the session.
            assert not second_entered.wait(0.1)
        finally:
            release_first.set()
        assert a.result(timeout=5).status_code == 200
        assert b.result(timeout=5).status_code == 200

    messages = SessionStore(tmp_path / "sessions").load("shared")
    assert any(m["content"] == "answer-FIRST" for m in messages)
    assert messages[-2:] == [
        {"role": "user", "content": "SECOND"},
        {"role": "assistant", "content": "answer-SECOND"},
    ]
    assert any(m["content"] == "answer-FIRST" for m in seen[-1])


def test_distinct_sessions_remain_parallel(tmp_path, monkeypatch):
    first_entered, release_first, second_entered = Event(), Event(), Event()

    def turn(ctx, *_args, **_kwargs):
        if ctx.messages[-1]["content"] == "FIRST":
            first_entered.set()
            assert release_first.wait(5)
        else:
            second_entered.set()
        ctx.add_assistant("ok")
        return "ok", []

    app = make_app(tmp_path, monkeypatch, turn)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(client.post, "/chat", json={"message": "FIRST", "session_id": "a"})
        try:
            assert first_entered.wait(5)
            b = pool.submit(client.post, "/chat", json={"message": "SECOND", "session_id": "b"})
            assert second_entered.wait(5)
            assert b.result(timeout=5).status_code == 200
        finally:
            release_first.set()
        assert a.result(timeout=5).status_code == 200


def test_session_save_is_atomic_on_serialization_failure(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    store.save("a", [{"role": "user", "content": "original"}])
    monkeypatch.setattr("bauer.session_store._safe_json_dumps", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("broken")))
    with pytest.raises(ValueError, match="broken"):
        store.save("a", [{"role": "user", "content": "replacement"}])
    assert store.load("a") == [{"role": "user", "content": "original"}]
    assert list(tmp_path.glob("*.tmp")) == []


def test_session_locks_shared_between_store_instances(tmp_path):
    a, b = SessionStore(tmp_path), SessionStore(tmp_path)
    entered = Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with a.turn("same"):
            future = pool.submit(b.serialized("same")(entered.set))
            assert not entered.wait(0.1)
        future.result(timeout=5)
    assert entered.is_set()


def _process_turn(sessions_dir, waiting, entered):
    store = SessionStore(sessions_dir)
    waiting.set()
    with store.turn("same"):
        entered.set()


@pytest.mark.skipif(os.name != "posix", reason="cross-process session locks require POSIX flock")
def test_session_lock_coordinates_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    waiting, entered = context.Event(), context.Event()
    process = context.Process(target=_process_turn, args=(tmp_path, waiting, entered))
    with SessionStore(tmp_path).turn("same"):
        process.start()
        try:
            assert waiting.wait(5)
            assert not entered.wait(0.1)
        except BaseException:
            process.terminate()
            process.join(5)
            raise
    try:
        assert entered.wait(5)
        process.join(5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
