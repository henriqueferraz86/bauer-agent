"""G14: testes para o PtyBridge (shell interativo sobre WebSocket via asyncio)."""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

from bauer.pty_bridge import PtyBridge, _default_shell


# ---------------------------------------------------------------------------
# WebSocket fake — registra envios, alimenta entradas
# ---------------------------------------------------------------------------

class FakeWS:
    def __init__(self, inputs=None):
        self.sent: list[dict] = []
        self._inputs = list(inputs or [])

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def receive_text(self) -> str:
        if self._inputs:
            return self._inputs.pop(0)
        # Sem mais entrada: bloqueia (o _pipe_input usa wait_for com timeout).
        await asyncio.sleep(3600)


# ---------------------------------------------------------------------------
# _default_shell
# ---------------------------------------------------------------------------

def test_default_shell_returns_list():
    sh = _default_shell()
    assert isinstance(sh, list) and sh
    assert isinstance(sh[0], str)


def test_default_shell_platform_specific():
    sh = _default_shell()
    if sys.platform == "win32":
        assert "cmd" in sh[0].lower()
    else:
        assert sh[0] in ("/bin/bash", "/bin/sh")


# ---------------------------------------------------------------------------
# _send
# ---------------------------------------------------------------------------

def test_send_serializes_json():
    ws = FakeWS()
    asyncio.run(PtyBridge._send(ws, {"type": "output", "data": "x"}))
    assert ws.sent == [{"type": "output", "data": "x"}]


# ---------------------------------------------------------------------------
# start_session — usa um "shell" python que emite saída e sai
# ---------------------------------------------------------------------------

def test_start_session_captures_output_and_exit():
    ws = FakeWS()
    bridge = PtyBridge()
    cmd = [sys.executable, "-c", "print('PTYHELLO')"]
    asyncio.run(asyncio.wait_for(bridge.start_session(ws, shell=cmd), timeout=15))

    types = [m["type"] for m in ws.sent]
    assert "exit" in types
    outputs = "".join(m.get("data", "") for m in ws.sent if m["type"] == "output")
    assert "PTYHELLO" in outputs
    exit_msg = next(m for m in ws.sent if m["type"] == "exit")
    assert exit_msg["code"] == 0


def test_start_session_nonzero_exit_code():
    ws = FakeWS()
    bridge = PtyBridge()
    cmd = [sys.executable, "-c", "import sys; sys.exit(3)"]
    asyncio.run(asyncio.wait_for(bridge.start_session(ws, shell=cmd), timeout=15))
    exit_msg = next(m for m in ws.sent if m["type"] == "exit")
    assert exit_msg["code"] == 3


def test_start_session_bad_shell_emits_error():
    ws = FakeWS()
    bridge = PtyBridge()
    cmd = ["nonexistent_shell_binary_xyz_123"]
    asyncio.run(asyncio.wait_for(bridge.start_session(ws, shell=cmd), timeout=15))
    assert any(m["type"] == "error" for m in ws.sent)


def test_start_session_feeds_input_to_stdin():
    # Um pequeno REPL python que ecoa a linha lida e sai.
    ws = FakeWS(inputs=[json.dumps({"type": "input", "data": "MARCO\n"})])
    bridge = PtyBridge()
    cmd = [sys.executable, "-c", "import sys; print('ECHO:' + sys.stdin.readline().strip())"]
    asyncio.run(asyncio.wait_for(bridge.start_session(ws, shell=cmd), timeout=15))
    outputs = "".join(m.get("data", "") for m in ws.sent if m["type"] == "output")
    assert "ECHO:MARCO" in outputs


# ---------------------------------------------------------------------------
# _pipe_output isolado (mock proc)
# ---------------------------------------------------------------------------

class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n):
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _FakeProc:
    def __init__(self, chunks):
        self.stdout = _FakeStream(chunks)
        self.returncode = 0


def test_pipe_output_forwards_chunks():
    ws = FakeWS()
    bridge = PtyBridge()
    proc = _FakeProc([b"hello ", b"world"])
    asyncio.run(asyncio.wait_for(bridge._pipe_output(proc, ws), timeout=10))
    outputs = "".join(m.get("data", "") for m in ws.sent if m["type"] == "output")
    assert outputs == "hello world"


# ---------------------------------------------------------------------------
# shell_server app + CLI `bauer shell`
# ---------------------------------------------------------------------------

def test_shell_server_create_app():
    pytest.importorskip("fastapi")
    from bauer.shell_server import create_app
    app = create_app()
    assert app is not None


def test_cli_shell_command_registered():
    pytest.importorskip("typer")
    from bauer.cli import app
    from typer.main import get_command
    cmd = get_command(app)
    assert "shell" in cmd.commands
