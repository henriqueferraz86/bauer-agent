"""Tests for G31 — lsp_format and lsp_rename in LspClient and ToolRouter."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bauer.lsp.client import LspClient, LspClientError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(responses: list) -> LspClient:
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.stdout = MagicMock()
    proc.returncode = None

    client = LspClient(proc)
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


# ---------------------------------------------------------------------------
# TestFormatDocument
# ---------------------------------------------------------------------------

class TestFormatDocument:
    def test_returns_list_of_edits(self):
        edits = [
            {"range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 3}},
             "newText": "foo"},
        ]
        client = _make_client([edits])
        result = asyncio.run(client.format_document("file:///x.py"))
        assert result == edits

    def test_returns_empty_on_none_response(self):
        client = _make_client([None])
        result = asyncio.run(client.format_document("file:///x.py"))
        assert result == []

    def test_returns_empty_on_non_list_response(self):
        client = _make_client([{"error": "unsupported"}])
        result = asyncio.run(client.format_document("file:///x.py"))
        assert result == []

    def test_returns_empty_on_exception(self):
        client = _make_client([LspClientError("fail")])
        result = asyncio.run(client.format_document("file:///x.py"))
        assert result == []

    def test_sends_correct_method(self):
        sent: list[tuple] = []

        async def _capture(method, params, timeout=10.0):
            sent.append((method, params))
            return []

        client = _make_client([])
        client._send = _capture  # type: ignore[method-assign]
        asyncio.run(client.format_document("file:///x.py", tab_size=2, insert_spaces=False))
        assert sent[0][0] == "textDocument/formatting"
        assert sent[0][1]["options"]["tabSize"] == 2
        assert sent[0][1]["options"]["insertSpaces"] is False

    def test_default_options(self):
        sent: list[tuple] = []

        async def _capture(method, params, timeout=10.0):
            sent.append((method, params))
            return []

        client = _make_client([])
        client._send = _capture  # type: ignore[method-assign]
        asyncio.run(client.format_document("file:///x.py"))
        opts = sent[0][1]["options"]
        assert opts["tabSize"] == 4
        assert opts["insertSpaces"] is True

    def test_multiple_edits(self):
        edits = [{"newText": f"line{i}"} for i in range(5)]
        client = _make_client([edits])
        result = asyncio.run(client.format_document("file:///x.py"))
        assert len(result) == 5

    def test_no_server_response_empty(self):
        client = _make_client([{}])
        result = asyncio.run(client.format_document("file:///x.py"))
        assert result == []


# ---------------------------------------------------------------------------
# TestRenameSymbol
# ---------------------------------------------------------------------------

class TestRenameSymbol:
    def test_returns_workspace_edit(self):
        workspace_edit = {
            "changes": {
                "file:///x.py": [
                    {"range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                     "newText": "bar"}
                ]
            }
        }
        client = _make_client([workspace_edit])
        result = asyncio.run(client.rename_symbol("file:///x.py", 0, 4, "bar"))
        assert result == workspace_edit
        assert "changes" in result

    def test_returns_none_on_exception(self):
        client = _make_client([LspClientError("fail")])
        result = asyncio.run(client.rename_symbol("file:///x.py", 0, 4, "bar"))
        assert result is None

    def test_returns_none_on_non_dict_response(self):
        client = _make_client([[]])
        result = asyncio.run(client.rename_symbol("file:///x.py", 0, 4, "bar"))
        assert result is None

    def test_sends_correct_method(self):
        sent: list[tuple] = []

        async def _capture(method, params, timeout=10.0):
            sent.append((method, params))
            return {"changes": {}}

        client = _make_client([])
        client._send = _capture  # type: ignore[method-assign]
        asyncio.run(client.rename_symbol("file:///x.py", 5, 3, "new_name"))
        assert sent[0][0] == "textDocument/rename"
        p = sent[0][1]
        assert p["newName"] == "new_name"
        assert p["position"] == {"line": 5, "character": 3}

    def test_document_changes_format(self):
        workspace_edit = {
            "documentChanges": [
                {"textDocument": {"uri": "file:///x.py"}, "edits": []}
            ]
        }
        client = _make_client([workspace_edit])
        result = asyncio.run(client.rename_symbol("file:///x.py", 0, 0, "new"))
        assert result is not None
        assert "documentChanges" in result

    def test_none_response_returns_none(self):
        client = _make_client([None])
        result = asyncio.run(client.rename_symbol("file:///x.py", 0, 0, "new"))
        assert result is None


# ---------------------------------------------------------------------------
# TestToolRouterLspFormat
# ---------------------------------------------------------------------------

class TestToolRouterLspFormat:
    def _make_router(self, tmp_path: Path):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=tmp_path)

    def _exec(self, router, action: str, args: dict) -> str:
        return router.execute({"action": action, "args": args})

    def test_lsp_format_in_available_tools(self, tmp_path):
        router = self._make_router(tmp_path)
        assert "lsp_format" in router.available_tools()

    def test_lsp_rename_in_available_tools(self, tmp_path):
        router = self._make_router(tmp_path)
        assert "lsp_rename" in router.available_tools()

    def test_lsp_format_requires_file_arg(self, tmp_path):
        from bauer.tool_router import ToolError
        router = self._make_router(tmp_path)
        with pytest.raises(ToolError, match="requer"):
            self._exec(router, "lsp_format", {})

    def test_lsp_rename_requires_file_arg(self, tmp_path):
        from bauer.tool_router import ToolError
        router = self._make_router(tmp_path)
        with pytest.raises(ToolError, match="requer"):
            self._exec(router, "lsp_rename", {"new_name": "bar"})

    def test_lsp_rename_requires_new_name(self, tmp_path):
        from bauer.tool_router import ToolError
        (tmp_path / "x.py").write_text("foo = 1\n")
        router = self._make_router(tmp_path)
        with pytest.raises(ToolError, match="requer"):
            self._exec(router, "lsp_rename", {"file": "x.py", "line": 0, "character": 0})

    def test_lsp_format_server_not_running_returns_json(self, tmp_path):
        (tmp_path / "x.py").write_text("foo=1\n")
        router = self._make_router(tmp_path)
        with patch.object(router, "_lsp_call", return_value=None):
            result = self._exec(router, "lsp_format", {"file": "x.py"})
        data = json.loads(result)
        assert "error" in data

    def test_lsp_rename_server_not_running_returns_json(self, tmp_path):
        (tmp_path / "x.py").write_text("foo = 1\n")
        router = self._make_router(tmp_path)
        with patch.object(router, "_lsp_call", return_value=None):
            result = self._exec(router, "lsp_rename", {"file": "x.py", "line": 0, "character": 0, "new_name": "bar"})
        data = json.loads(result)
        assert "error" in data

    def test_lsp_format_returns_edits_json(self, tmp_path):
        (tmp_path / "x.py").write_text("foo=1\n")
        router = self._make_router(tmp_path)
        edits = [{"newText": "foo = 1\n"}]
        with patch.object(router, "_lsp_call", return_value=edits):
            result = self._exec(router, "lsp_format", {"file": "x.py"})
        data = json.loads(result)
        assert isinstance(data, list)
        assert data[0]["newText"] == "foo = 1\n"

    def test_lsp_rename_returns_workspace_edit_json(self, tmp_path):
        (tmp_path / "x.py").write_text("foo = 1\n")
        router = self._make_router(tmp_path)
        workspace_edit = {"changes": {"file:///x.py": []}}
        with patch.object(router, "_lsp_call", return_value=workspace_edit):
            result = self._exec(router, "lsp_rename", {"file": "x.py", "line": 0, "character": 0, "new_name": "bar"})
        data = json.loads(result)
        assert "changes" in data

    def test_lsp_format_passes_options(self, tmp_path):
        (tmp_path / "x.py").write_text("foo=1\n")
        router = self._make_router(tmp_path)
        calls = []

        def _capture(method, file_rel, line, char, **kwargs):
            calls.append((method, kwargs))
            return []

        with patch.object(router, "_lsp_call", side_effect=_capture):
            self._exec(router, "lsp_format", {"file": "x.py", "tab_size": 2, "insert_spaces": False})

        assert calls[0][0] == "format_document"
        assert calls[0][1]["tab_size"] == 2
        assert calls[0][1]["insert_spaces"] is False

    def test_lsp_rename_passes_new_name(self, tmp_path):
        (tmp_path / "x.py").write_text("foo = 1\n")
        router = self._make_router(tmp_path)
        calls = []

        def _capture(method, file_rel, line, char, **kwargs):
            calls.append((method, kwargs))
            return {"changes": {}}

        with patch.object(router, "_lsp_call", side_effect=_capture):
            self._exec(router, "lsp_rename", {"file": "x.py", "line": 1, "character": 2, "new_name": "baz"})

        assert calls[0][0] == "rename_symbol"
        assert calls[0][1]["new_name"] == "baz"

    def test_lsp_format_has_hint_in_error(self, tmp_path):
        (tmp_path / "x.py").write_text("foo=1\n")
        router = self._make_router(tmp_path)
        with patch.object(router, "_lsp_call", return_value=None):
            result = self._exec(router, "lsp_format", {"file": "x.py"})
        data = json.loads(result)
        assert "hint" in data
