"""LspClient — JSON-RPC 2.0 client over stdio for Language Server Protocol."""

from __future__ import annotations

import asyncio
import json
import logging

logger = logging.getLogger(__name__)


class LspClientError(Exception):
    pass


class LspClient:
    """JSON-RPC 2.0 LSP client over stdin/stdout of a server process.

    Talks to an LSP server (pyright, jedi-language-server, etc.) using the
    standard Content-Length framing defined by the LSP spec.
    """

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background reader task."""
        self._reader_task = asyncio.create_task(self._reader_loop())

    async def shutdown(self) -> None:
        """Send LSP shutdown + exit, then terminate the process."""
        try:
            await self._send("shutdown", {})
        except Exception:
            pass
        try:
            await self._notify("exit", {})
        except Exception:
            pass
        if self._reader_task:
            self._reader_task.cancel()
        if self._proc.returncode is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # LSP methods
    # ------------------------------------------------------------------

    async def initialize(self, root_uri: str) -> dict:
        """Send LSP initialize request."""
        return await self._send("initialize", {
            "processId": None,
            "clientInfo": {"name": "bauer-agent", "version": "1.0"},
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "hover": {"contentFormat": ["plaintext", "markdown"]},
                    "definition": {},
                    "references": {},
                    "publishDiagnostics": {},
                },
                "workspace": {"workspaceFolders": True},
            },
        })

    async def initialized(self) -> None:
        await self._notify("initialized", {})

    async def hover(self, file_uri: str, line: int, character: int) -> dict | None:
        try:
            result = await self._send("textDocument/hover", {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
            })
            return result
        except Exception as exc:
            logger.debug("LSP hover failed: %s", exc)
            return None

    async def definitions(self, file_uri: str, line: int, character: int) -> list[dict]:
        try:
            result = await self._send("textDocument/definition", {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
            })
            if result is None:
                return []
            if isinstance(result, list):
                return result
            return [result]
        except Exception as exc:
            logger.debug("LSP definition failed: %s", exc)
            return []

    async def references(self, file_uri: str, line: int, character: int) -> list[dict]:
        try:
            result = await self._send("textDocument/references", {
                "textDocument": {"uri": file_uri},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": True},
            })
            return result if isinstance(result, list) else []
        except Exception as exc:
            logger.debug("LSP references failed: %s", exc)
            return []

    async def diagnostics(self, file_uri: str) -> list[dict]:
        """Request diagnostics via workspace/diagnostic (LSP 3.17+)."""
        try:
            result = await self._send("workspace/diagnostic", {
                "identifier": "",
                "previousResultIds": [],
            })
            if isinstance(result, dict):
                items = result.get("items", [])
                return [i for i in items if i.get("uri") == file_uri]
            return []
        except Exception as exc:
            logger.debug("LSP diagnostics failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # JSON-RPC internals
    # ------------------------------------------------------------------

    def _encode_message(self, msg: dict) -> bytes:
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
        return header + body

    async def _send(self, method: str, params: dict, timeout: float = 10.0) -> dict:
        self._request_id += 1
        req_id = self._request_id
        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut

        raw = self._encode_message(msg)
        assert self._proc.stdin is not None
        self._proc.stdin.write(raw)
        await self._proc.stdin.drain()

        try:
            result = await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(req_id, None)

        if isinstance(result, dict) and "error" in result:
            raise LspClientError(f"LSP error {result['error']}")
        return result

    async def _notify(self, method: str, params: dict) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        raw = self._encode_message(msg)
        assert self._proc.stdin is not None
        self._proc.stdin.write(raw)
        await self._proc.stdin.drain()

    async def _read_message(self) -> dict:
        assert self._proc.stdout is not None
        # Read headers
        headers: dict[str, str] = {}
        while True:
            line_bytes = await self._proc.stdout.readline()
            line = line_bytes.decode("utf-8").rstrip("\r\n")
            if not line:
                break
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()

        length = int(headers.get("content-length", "0"))
        if length == 0:
            return {}
        body_bytes = await self._proc.stdout.readexactly(length)
        return json.loads(body_bytes.decode("utf-8"))

    async def _reader_loop(self) -> None:
        while True:
            try:
                msg = await self._read_message()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("LSP reader error: %s", exc)
                break

            msg_id = msg.get("id")
            if msg_id is not None and msg_id in self._pending:
                fut = self._pending.get(msg_id)
                if fut and not fut.done():
                    result = msg.get("result") if "result" in msg else msg.get("error")
                    fut.set_result(result)
