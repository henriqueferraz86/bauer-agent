"""Exercise ownership and cancellation while an AgentOS LLM call is active."""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import uvicorn

pytest.importorskip("agno")
jwt = pytest.importorskip("jwt")

from agno.models.base import Model
from agno.models.response import ModelResponse


@dataclass
class _BlockingModel(Model):
    started: Any = None
    release: Any = None

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        raise AssertionError("streaming expected")

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        raise AssertionError("streaming expected")

    def invoke_stream(self, *args: Any, **kwargs: Any):
        raise AssertionError("async streaming expected")
        yield  # pragma: no cover

    async def ainvoke_stream(self, *args: Any, **kwargs: Any):
        self.started.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        yield ModelResponse(role="assistant", content="fim")

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response


def test_active_run_cancel_requires_owner_and_terminal_confirmation(tmp_path, monkeypatch):
    from bauer.agentos_app import build_agentos_app
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    key = "active-test-key-with-at-least-32-bytes"
    for name in ("OS_SECURITY_KEY", "JWT_VERIFICATION_KEY", "JWT_JWKS_FILE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        "BAUER_AGENTOS_JWT_ISOLATION": "true", "BAUER_AGENTOS_CHANNEL_BOUNDARY": "true",
        "BAUER_AGENTOS_JWT_KEY": key, "BAUER_AGENTOS_JWT_AUDIENCE": "active",
        "BAUER_AGENTOS_JWT_ISSUER": "active-issuer",
    }.items():
        monkeypatch.setenv(name, value)
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(AgnoRuntimeAdapter, "_build_model", lambda self, spec: _BlockingModel(
        id="stub", started=started, release=release))
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: ollama\n  name: stub\n", encoding="utf-8")
    app = build_agentos_app(config_path=config, runtime_root=tmp_path / "runtime")

    def auth(subject):
        return {"Authorization": "Bearer " + jwt.encode({
            "sub": subject, "iss": "active-issuer", "aud": "active", "exp": int(time.time()) + 60,
            "scopes": ["agents:bauer.dev:run", "agents:bauer.dev:read"],
        }, key, algorithm="HS256")}

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        path = "/agents/bauer.dev/runs"
        with httpx.Client(timeout=10, trust_env=False) as client:
            with client.stream("POST", url + path, headers=auth("alice"), data={
                "message": "oi", "session_id": "active-session", "stream": "true",
            }) as response:
                assert response.status_code == 200
                run_id = None
                lines = response.iter_lines()
                for line in lines:
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        if event.get("run_id"):
                            run_id = event["run_id"]
                            break
                assert run_id and started.wait(5)
                cancel_url = url + f"{path}/{run_id}/cancel"
                params = {"session_id": "active-session"}
                denied = client.post(cancel_url, headers=auth("bob"), params=params)
                assert denied.status_code in {403, 404}
                accepted = client.post(cancel_url, headers=auth("alice"), params=params)
                assert accepted.status_code == 200, accepted.text
                release.set()
                list(lines)
                state = client.get(url + f"{path}/{run_id}", headers=auth("alice"), params=params)
                assert state.status_code == 200, state.text
                assert str(state.json().get("status", "")).upper() == "CANCELLED"
    finally:
        release.set()
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
