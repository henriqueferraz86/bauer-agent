"""Tests for G26 — LSP client enhanced methods.

Covers: workspace_symbols, completion, code_actions, did_open/did_close
in LspClient, and the 3 new tools registered in ToolRouter.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bauer.lsp.client import LspClient, LspClientError
from bauer.lsp.servers import KNOWN_SERVERS, LspServerConfig, server_for_file


# ---------------------------------------------------------------------------
# LspClient — new methods (unit tests with mocked process)
# ---------------------------------------------------------------------------


def _make_client(responses: list[dict]) -> LspClient:
    """Build a LspClient whose _send method is mocked."""
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdout = MagicMock()
    proc.returncode = None

    client = LspClient(proc)
    # Mock _send to return pre-baked responses in order
    response_iter = iter(responses)

    async def _fake_send(method, params, timeout=10.0):
        try:
            resp = next(response_iter)
        except StopIteration:
            return {}
        if isinstance(resp, Exception):
            raise resp
        return resp

    client._send = _fake_send  # type: ignore[method-assign]
    client._notify = AsyncMock()
    return client


class TestLspClientWorkspaceSymbols:
    def test_returns_list_of_symbols(self):
        symbols = [
            {"name": "MyClass", "kind": 5, "location": {"uri": "file:///x.py"}},
            {"name": "my_func", "kind": 12, "location": {"uri": "file:///x.py"}},
        ]
        client = _make_client([symbols])

        result = asyncio.run(
            client.workspace_symbols("My")
        )
        assert result == symbols

    def test_returns_empty_on_exception(self):
        client = _make_client([RuntimeError("boom")])
        result = asyncio.run(
            client.workspace_symbols("anything")
        )
        assert result == []

    def test_returns_empty_on_none_response(self):
        client = _make_client([None])
        result = asyncio.run(
            client.workspace_symbols("q")
        )
        assert result == []

    def test_sends_correct_method(self):
        sent: list[tuple] = []

        async def _capture_send(method, params, timeout=10.0):
            sent.append((method, params))
            return []

        client = _make_client([])
        client._send = _capture_send  # type: ignore[method-assign]

        asyncio.run(client.workspace_symbols("Foo"))
        assert sent[0][0] == "workspace/symbol"
        assert sent[0][1]["query"] == "Foo"


class TestLspClientCompletion:
    def test_returns_list_format(self):
        items = [{"label": "print", "kind": 3}, {"label": "pass", "kind": 14}]
        client = _make_client([items])
        result = asyncio.run(
            client.completion("file:///x.py", 5, 3)
        )
        assert result == items

    def test_handles_completion_list_format(self):
        items = [{"label": "foo"}]
        client = _make_client([{"isIncomplete": False, "items": items}])
        result = asyncio.run(
            client.completion("file:///x.py", 0, 0)
        )
        assert result == items

    def test_returns_empty_on_none(self):
        client = _make_client([None])
        result = asyncio.run(
            client.completion("file:///x.py", 0, 0)
        )
        assert result == []

    def test_returns_empty_on_exception(self):
        client = _make_client([RuntimeError("nope")])
        result = asyncio.run(
            client.completion("file:///x.py", 0, 0)
        )
        assert result == []

    def test_sends_correct_params(self):
        sent: list[tuple] = []

        async def _capture(method, params, timeout=10.0):
            sent.append((method, params))
            return []

        client = _make_client([])
        client._send = _capture  # type: ignore[method-assign]
        asyncio.run(
            client.completion("file:///x.py", 10, 5)
        )
        assert sent[0][0] == "textDocument/completion"
        pos = sent[0][1]["position"]
        assert pos == {"line": 10, "character": 5}


class TestLspClientCodeActions:
    def test_returns_list_of_actions(self):
        actions = [
            {"title": "Add import", "kind": "quickfix"},
            {"title": "Rename symbol", "kind": "refactor"},
        ]
        client = _make_client([actions])
        result = asyncio.run(
            client.code_actions("file:///x.py", 5, 0, 5, 10)
        )
        assert result == actions

    def test_returns_empty_on_exception(self):
        client = _make_client([RuntimeError("no")])
        result = asyncio.run(
            client.code_actions("file:///x.py", 0, 0, 0, 0)
        )
        assert result == []

    def test_sends_correct_range(self):
        sent: list[tuple] = []

        async def _capture(method, params, timeout=10.0):
            sent.append((method, params))
            return []

        client = _make_client([])
        client._send = _capture  # type: ignore[method-assign]
        asyncio.run(
            client.code_actions("file:///x.py", 3, 1, 5, 20)
        )
        assert sent[0][0] == "textDocument/codeAction"
        rng = sent[0][1]["range"]
        assert rng["start"] == {"line": 3, "character": 1}
        assert rng["end"] == {"line": 5, "character": 20}

    def test_default_diagnostics_empty(self):
        sent: list[tuple] = []

        async def _capture(method, params, timeout=10.0):
            sent.append((method, params))
            return []

        client = _make_client([])
        client._send = _capture  # type: ignore[method-assign]
        asyncio.run(
            client.code_actions("file:///x.py", 0, 0, 0, 0)
        )
        assert sent[0][1]["context"]["diagnostics"] == []


class TestLspClientDidOpenClose:
    def test_did_open_sends_notification(self):
        client = _make_client([])

        asyncio.run(
            client.did_open("file:///x.py", "python", "x = 1\n")
        )
        client._notify.assert_called_once()
        method, params = client._notify.call_args[0]
        assert method == "textDocument/didOpen"
        assert params["textDocument"]["uri"] == "file:///x.py"
        assert params["textDocument"]["languageId"] == "python"
        assert params["textDocument"]["text"] == "x = 1\n"

    def test_did_close_sends_notification(self):
        client = _make_client([])

        asyncio.run(
            client.did_close("file:///x.py")
        )
        client._notify.assert_called_once()
        method, params = client._notify.call_args[0]
        assert method == "textDocument/didClose"
        assert params["textDocument"]["uri"] == "file:///x.py"


# ---------------------------------------------------------------------------
# LspClient — encoding/decoding (regression tests)
# ---------------------------------------------------------------------------


class TestLspClientEncoding:
    def test_encode_message_has_content_length_header(self):
        proc = MagicMock()
        client = LspClient(proc)
        msg = {"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}
        raw = client._encode_message(msg)
        assert b"Content-Length:" in raw
        body_start = raw.index(b"\r\n\r\n") + 4
        body = json.loads(raw[body_start:])
        assert body["method"] == "test"

    def test_encode_message_length_matches_body(self):
        proc = MagicMock()
        client = LspClient(proc)
        msg = {"jsonrpc": "2.0", "id": 2, "method": "x", "params": {"a": "b"}}
        raw = client._encode_message(msg)
        header_end = raw.index(b"\r\n\r\n") + 4
        header = raw[:header_end].decode("utf-8")
        body = raw[header_end:]
        declared_length = int(
            next(h for h in header.split("\r\n") if "Content-Length" in h)
            .split(":")[1]
            .strip()
        )
        assert declared_length == len(body)


# ---------------------------------------------------------------------------
# ToolRouter — new tools registered
# ---------------------------------------------------------------------------


class TestToolRouterLspNewTools:
    def _make_router(self, tmp_path):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=tmp_path)

    def test_lsp_workspace_symbols_registered(self, tmp_path):
        router = self._make_router(tmp_path)
        assert "lsp_workspace_symbols" in router.available_tools()

    def test_lsp_completion_registered(self, tmp_path):
        router = self._make_router(tmp_path)
        assert "lsp_completion" in router.available_tools()

    def test_lsp_code_actions_registered(self, tmp_path):
        router = self._make_router(tmp_path)
        assert "lsp_code_actions" in router.available_tools()

    def test_lsp_workspace_symbols_no_query_raises(self, tmp_path):
        from bauer.tool_router import ToolError
        router = self._make_router(tmp_path)
        with pytest.raises((ToolError, Exception)):
            router.execute({"action": "lsp_workspace_symbols", "args": {"query": ""}})

    def test_lsp_completion_no_file_raises(self, tmp_path):
        from bauer.tool_router import ToolError
        router = self._make_router(tmp_path)
        with pytest.raises((ToolError, Exception)):
            router.execute({"action": "lsp_completion", "args": {"file": ""}})

    def test_lsp_code_actions_no_file_raises(self, tmp_path):
        from bauer.tool_router import ToolError
        router = self._make_router(tmp_path)
        with pytest.raises((ToolError, Exception)):
            router.execute({"action": "lsp_code_actions", "args": {"file": ""}})

    def test_lsp_workspace_symbols_no_server_returns_error(self, tmp_path):
        router = self._make_router(tmp_path)
        with patch("bauer.tool_router.ToolRouter._lsp_call", return_value=None):
            result = router.execute({"action": "lsp_workspace_symbols", "args": {"query": "Foo"}})
        data = json.loads(result)
        assert "error" in data

    def test_lsp_completion_no_server_returns_error(self, tmp_path):
        router = self._make_router(tmp_path)
        f = tmp_path / "x.py"
        f.write_text("x = 1\n")
        with patch("bauer.tool_router.ToolRouter._lsp_call", return_value=None):
            result = router.execute({"action": "lsp_completion", "args": {"file": "x.py"}})
        data = json.loads(result)
        assert "error" in data

    def test_lsp_code_actions_no_server_returns_error(self, tmp_path):
        router = self._make_router(tmp_path)
        f = tmp_path / "x.py"
        f.write_text("x = 1\n")
        with patch("bauer.tool_router.ToolRouter._lsp_call", return_value=None):
            result = router.execute({
                "action": "lsp_code_actions",
                "args": {"file": "x.py", "start_line": 0, "start_char": 0},
            })
        data = json.loads(result)
        assert "error" in data

    def test_lsp_workspace_symbols_returns_json(self, tmp_path):
        router = self._make_router(tmp_path)
        fake_symbols = [{"name": "Foo", "kind": 5}]
        with patch("bauer.tool_router.ToolRouter._lsp_call", return_value=fake_symbols):
            result = router.execute({"action": "lsp_workspace_symbols", "args": {"query": "Foo"}})
        data = json.loads(result)
        assert data == fake_symbols

    def test_lsp_completion_returns_items(self, tmp_path):
        router = self._make_router(tmp_path)
        f = tmp_path / "x.py"
        f.write_text("x = 1\n")
        fake_items = [{"label": "print"}]
        with patch("bauer.tool_router.ToolRouter._lsp_call", return_value=fake_items):
            result = router.execute({
                "action": "lsp_completion",
                "args": {"file": "x.py", "line": 0, "character": 0},
            })
        data = json.loads(result)
        assert data == fake_items

    def test_all_lsp_tools_have_low_risk(self, tmp_path):
        from bauer.tool_router import _TOOL_SECURITY
        for tool in ("lsp_workspace_symbols", "lsp_completion", "lsp_code_actions"):
            meta = _TOOL_SECURITY.get(tool, {})
            assert meta.get("risk") == "low", f"{tool} should have low risk"
            assert meta.get("approval") is False, f"{tool} should not need approval"
