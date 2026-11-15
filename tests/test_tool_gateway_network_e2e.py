"""Exercise the experimental gateway over a real local HTTP socket."""
from __future__ import annotations

import socket
import threading
import time

import httpx
import uvicorn
from fastapi import FastAPI

from bauer.tool_gateway_endpoint import ToolCallLedger, build_tool_gateway_router
from tests.test_tool_gateway_authorization import env  # noqa: F401


def test_gateway_real_http_auth_policy_and_replay(env):
    auth, token, _, runs, _, _, _, workspace, _ = env
    (workspace / "example.txt").write_text("conteudo-controlado", encoding="utf-8")
    service_key = "isolated-service-key-at-least-32-bytes"
    app = FastAPI()
    app.include_router(build_tool_gateway_router(
        authorizer=auth,
        ledger=ToolCallLedger(runs.store.path),
        service_key=service_key,
        policy_root=runs.store.root,
        require_secure_transport=True,
    ))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "gateway did not start"
        url = f"http://127.0.0.1:{port}/bauer/tool-gateway/call"
        headers = {"Authorization": token(), "X-Bauer-Service-Key": service_key}
        payload = {"action": "read_file", "args": {"path": "example.txt"}}
        with httpx.Client(timeout=10, trust_env=False) as client:
            assert client.post(url, json=payload).status_code == 401
            assert client.post(url, headers={**headers, "X-Bauer-Service-Key": "wrong"}, json=payload).status_code == 401
            assert client.post(url, headers={**headers, "Authorization": token({"sub": "tg-other"})},
                               json=payload).status_code == 403
            assert client.post(url, headers=headers, json={
                "action": "write_file", "args": {"path": "example.txt", "content": "alterado"},
            }).status_code == 403
            response = client.post(url, headers=headers, json=payload)
            assert response.status_code == 200, response.text
            assert "conteudo-controlado" in response.json()["result"]
            assert client.post(url, headers=headers, json=payload).status_code == 409
        assert (workspace / "example.txt").read_text(encoding="utf-8") == "conteudo-controlado"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
    assert not thread.is_alive(), "gateway did not stop"
