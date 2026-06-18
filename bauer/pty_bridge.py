"""PTY Bridge — shell interativo sobre WebSocket.

Usa asyncio.create_subprocess_exec em vez de POSIX pty para compatibilidade
com Windows. Protocolo WebSocket JSON:

  client → server: {"type": "input",  "data": "ls\\n"}
  server → client: {"type": "output", "data": "file.txt\\n"}
                   {"type": "exit",   "code": 0}
                   {"type": "error",  "message": "..."}

Uso com FastAPI::

    from bauer.pty_bridge import PtyBridge

    @app.websocket("/ws/shell")
    async def shell_ws(ws: WebSocket):
        await ws.accept()
        bridge = PtyBridge()
        await bridge.start_session(ws)
"""

from __future__ import annotations

import asyncio
import json
import platform
import sys
from typing import Any


_DEFAULT_SHELL_WINDOWS = ["cmd.exe"]
_DEFAULT_SHELL_UNIX = ["/bin/bash"]


def _default_shell() -> list[str]:
    if platform.system() == "Windows":
        return _DEFAULT_SHELL_WINDOWS
    shell = "/bin/bash"
    if not __import__("pathlib").Path(shell).exists():
        shell = "/bin/sh"
    return [shell]


class PtyBridge:
    """Shell interativo sobre WebSocket via asyncio subprocess.

    Compatível com Windows — não usa `pty` POSIX.
    """

    _CHUNK_SIZE = 4096
    _READ_TIMEOUT = 0.1  # seconds — poll interval for stdout

    def __init__(self, shell: list[str] | None = None) -> None:
        self._shell = shell or _default_shell()

    async def start_session(self, websocket: Any, shell: list[str] | None = None) -> None:
        """Inicia o processo shell e faz bridge com o websocket até o processo terminar."""
        cmd = shell or self._shell
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as exc:
            await self._send(websocket, {"type": "error", "message": f"Failed to start shell: {exc}"})
            return

        input_task = asyncio.create_task(self._pipe_input(proc, websocket))
        output_task = asyncio.create_task(self._pipe_output(proc, websocket))

        try:
            await asyncio.gather(input_task, output_task, return_exceptions=True)
        finally:
            if proc.returncode is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            code = proc.returncode if proc.returncode is not None else -1
            await self._send(websocket, {"type": "exit", "code": code})

    async def _pipe_output(self, proc: asyncio.subprocess.Process, websocket: Any) -> None:
        """Lê stdout/stderr do subprocess e envia chunks ao WebSocket."""
        assert proc.stdout is not None
        while True:
            try:
                chunk = await asyncio.wait_for(
                    proc.stdout.read(self._CHUNK_SIZE),
                    timeout=self._READ_TIMEOUT,
                )
            except asyncio.TimeoutError:
                if proc.returncode is not None:
                    break
                continue
            except Exception:
                break

            if not chunk:
                break

            text = chunk.decode("utf-8", errors="replace")
            try:
                await self._send(websocket, {"type": "output", "data": text})
            except Exception:
                break

    async def _pipe_input(self, proc: asyncio.subprocess.Process, websocket: Any) -> None:
        """Recebe mensagens JSON do WebSocket e escreve no stdin do subprocess."""
        assert proc.stdin is not None
        while proc.returncode is None:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

            try:
                msg = json.loads(raw)
            except Exception:
                msg = {"type": "input", "data": raw}

            if msg.get("type") == "input":
                data = msg.get("data", "")
                if isinstance(data, str):
                    data = data.encode("utf-8", errors="replace")
                try:
                    proc.stdin.write(data)
                    await proc.stdin.drain()
                except Exception:
                    break

    @staticmethod
    async def _send(websocket: Any, msg: dict) -> None:
        """Envia mensagem JSON ao WebSocket."""
        try:
            await websocket.send_text(json.dumps(msg))
        except Exception:
            pass
