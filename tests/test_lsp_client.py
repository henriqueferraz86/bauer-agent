"""G15: testes para o LSP client, manager e integração com ToolRouter."""
from __future__ import annotations

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# LspClient internals
# ---------------------------------------------------------------------------

class TestLspClientEncoding:

    def test_encode_message_has_content_length_header(self):
        from bauer.lsp.client import LspClient
        proc = MagicMock()
        client = LspClient(proc)
        msg = {"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}
        encoded = client._encode_message(msg)
        assert b"Content-Length:" in encoded
        assert b"\r\n\r\n" in encoded

    def test_encode_message_content_length_matches_body(self):
        from bauer.lsp.client import LspClient
        proc = MagicMock()
        client = LspClient(proc)
        msg = {"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}
        encoded = client._encode_message(msg)
        header, _, body = encoded.partition(b"\r\n\r\n")
        length_line = next(l for l in header.split(b"\r\n") if l.startswith(b"Content-Length:"))
        declared_len = int(length_line.split(b":")[1].strip())
        assert declared_len == len(body)

    def test_encode_message_is_valid_json_body(self):
        from bauer.lsp.client import LspClient
        proc = MagicMock()
        client = LspClient(proc)
        msg = {"jsonrpc": "2.0", "id": 42, "method": "foo/bar", "params": {"key": "value"}}
        encoded = client._encode_message(msg)
        _, _, body = encoded.partition(b"\r\n\r\n")
        parsed = json.loads(body.decode("utf-8"))
        assert parsed["id"] == 42
        assert parsed["method"] == "foo/bar"

    @pytest.mark.asyncio
    async def test_read_message_parses_content_length_frame(self):
        from bauer.lsp.client import LspClient
        proc = MagicMock()
        client = LspClient(proc)

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"hover": "int"}}).encode()
        header = f"Content-Length: {len(body)}\r\n\r\n".encode()
        stream_data = header + body

        async def mock_readline():
            # Simulate line-by-line reading
            lines = (header.decode() + "\r\n").encode().split(b"\r\n")
            for line in lines:
                yield line + b"\r\n"

        # Use a simpler approach with AsyncMock
        import asyncio as _asyncio

        lines_to_yield = [b"Content-Length: " + str(len(body)).encode() + b"\r\n", b"\r\n"]
        line_index = 0

        async def readline():
            nonlocal line_index
            if line_index < len(lines_to_yield):
                result = lines_to_yield[line_index]
                line_index += 1
                return result
            return b""

        proc.stdout = MagicMock()
        proc.stdout.readline = readline
        proc.stdout.readexactly = AsyncMock(return_value=body)

        msg = await client._read_message()
        assert msg.get("result") is not None


# ---------------------------------------------------------------------------
# LspServerConfig and KNOWN_SERVERS
# ---------------------------------------------------------------------------

class TestKnownServers:

    def test_known_servers_not_empty(self):
        from bauer.lsp.servers import KNOWN_SERVERS
        assert len(KNOWN_SERVERS) >= 4

    def test_pyright_in_known_servers(self):
        from bauer.lsp.servers import KNOWN_SERVERS
        assert "pyright" in KNOWN_SERVERS
        assert KNOWN_SERVERS["pyright"].lang == "python"

    def test_typescript_in_known_servers(self):
        from bauer.lsp.servers import KNOWN_SERVERS
        assert "typescript" in KNOWN_SERVERS

    def test_rust_analyzer_in_known_servers(self):
        from bauer.lsp.servers import KNOWN_SERVERS
        assert "rust-analyzer" in KNOWN_SERVERS

    def test_all_servers_have_cmd(self):
        from bauer.lsp.servers import KNOWN_SERVERS
        for name, cfg in KNOWN_SERVERS.items():
            assert len(cfg.cmd) >= 1, f"{name} has empty cmd"

    def test_all_servers_have_lang(self):
        from bauer.lsp.servers import KNOWN_SERVERS
        for name, cfg in KNOWN_SERVERS.items():
            assert cfg.lang, f"{name} has empty lang"

    def test_server_for_python_file(self):
        from bauer.lsp.servers import server_for_file
        cfg = server_for_file("main.py")
        assert cfg is not None
        assert cfg.lang == "python"

    def test_server_for_typescript_file(self):
        from bauer.lsp.servers import server_for_file
        cfg = server_for_file("app.ts")
        assert cfg is not None
        assert cfg.lang == "typescript"

    def test_server_for_rust_file(self):
        from bauer.lsp.servers import server_for_file
        cfg = server_for_file("main.rs")
        assert cfg is not None
        assert cfg.lang == "rust"

    def test_server_for_unknown_extension_returns_none(self):
        from bauer.lsp.servers import server_for_file
        cfg = server_for_file("file.xyz123")
        assert cfg is None

    def test_server_for_language_python(self):
        from bauer.lsp.servers import server_for_language
        cfg = server_for_language("python")
        assert cfg is not None

    def test_server_for_language_unknown_returns_none(self):
        from bauer.lsp.servers import server_for_language
        cfg = server_for_language("brainfuck")
        assert cfg is None


# ---------------------------------------------------------------------------
# LspManager
# ---------------------------------------------------------------------------

class TestLspManager:

    @pytest.mark.asyncio
    async def test_start_raises_when_server_not_found(self, tmp_path):
        from bauer.lsp.manager import LspManager
        from bauer.lsp.servers import LspServerConfig
        cfg = LspServerConfig(cmd=["nonexistent_lsp_server_xyz_abc"], lang="python")
        with pytest.raises(FileNotFoundError):
            await LspManager.start(cfg, tmp_path)

    @pytest.mark.asyncio
    async def test_get_or_start_returns_none_when_binary_missing(self, tmp_path):
        from bauer.lsp.manager import get_or_start
        from bauer.lsp.servers import LspServerConfig
        cfg = LspServerConfig(cmd=["nonexistent_lsp_binary_xyz"], lang="python")
        mgr = await get_or_start(cfg, str(tmp_path))
        assert mgr is None


# ---------------------------------------------------------------------------
# LSP tools in ToolRouter
# ---------------------------------------------------------------------------

class TestLspToolsInRouter:

    @pytest.fixture
    def router(self, tmp_path):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=tmp_path)

    @pytest.mark.parametrize("tool", ["lsp_hover", "lsp_definitions", "lsp_references", "lsp_diagnostics"])
    def test_lsp_tool_registered(self, router, tool):
        assert tool in router._tools

    @pytest.mark.parametrize("tool", ["lsp_hover", "lsp_definitions", "lsp_references", "lsp_diagnostics"])
    def test_lsp_tool_has_description(self, router, tool):
        entry = router._tools[tool]
        assert len(entry.get("description", "")) > 5

    @pytest.mark.parametrize("tool", ["lsp_hover", "lsp_definitions", "lsp_references"])
    def test_lsp_tool_returns_error_when_no_server(self, router, tmp_path, tool):
        (tmp_path / "test.py").write_text("def foo():\n    pass\n")
        result = router.execute({"action": tool, "args": {"file": "test.py", "line": 0, "character": 0}})
        assert isinstance(result, str)
        # Should return a JSON error, not crash
        parsed = json.loads(result)
        assert "error" in parsed

    def test_lsp_diagnostics_returns_error_when_no_server(self, router, tmp_path):
        (tmp_path / "test.py").write_text("x: str = 123\n")
        result = router.execute({"action": "lsp_diagnostics", "args": {"file": "test.py"}})
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert "error" in parsed

    @pytest.mark.parametrize("tool", ["lsp_hover", "lsp_definitions"])
    def test_lsp_tool_requires_file_arg(self, router, tool):
        from bauer.tool_router import ToolError
        # Sem 'file' → ToolError (execute propaga, nao retorna string).
        with pytest.raises(ToolError, match="requer"):
            router.execute({"action": tool, "args": {}})

    @pytest.mark.parametrize("tool", ["lsp_hover", "lsp_definitions", "lsp_references", "lsp_diagnostics"])
    def test_lsp_tool_not_requires_approval(self, tool):
        from bauer.tool_router import _TOOL_SECURITY
        perm = _TOOL_SECURITY.get(tool)
        assert perm is not None, f"{tool} not in _TOOL_SECURITY"
        assert perm["approval"] is False

    @pytest.mark.parametrize("tool", ["lsp_hover", "lsp_definitions", "lsp_references", "lsp_diagnostics"])
    def test_lsp_tool_risk_level_is_low(self, tool):
        from bauer.tool_router import _TOOL_SECURITY
        perm = _TOOL_SECURITY.get(tool)
        assert perm is not None
        assert perm["risk"] == "low"


# ---------------------------------------------------------------------------
# LSP module imports
# ---------------------------------------------------------------------------

def test_lsp_package_imports():
    from bauer.lsp import LspClient, LspManager, LspServerConfig, KNOWN_SERVERS
    assert LspClient is not None
    assert LspManager is not None
    assert len(KNOWN_SERVERS) >= 4


def test_lsp_client_instantiate():
    from bauer.lsp.client import LspClient
    proc = MagicMock()
    client = LspClient(proc)
    assert client is not None
    assert client._request_id == 0


def test_lsp_server_config_dataclass():
    from bauer.lsp.servers import LspServerConfig
    cfg = LspServerConfig(cmd=["test-lsp"], lang="test")
    assert cfg.cmd == ["test-lsp"]
    assert cfg.lang == "test"
    assert cfg.extra_env == {}
