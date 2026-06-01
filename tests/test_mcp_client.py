"""Testes do McpClient e McpManager (MCP-1).

Estratégia: mock do subprocess servidor — nenhum servidor MCP real necessário.
O mock responde com JSON-RPC 2.0 válido no formato do protocolo MCP.

Cobre:
- McpServerConfig: validação, command como str e list
- McpClient: start/stop, initialize, list_tools, call_tool, context manager
- McpClient: erro de conexão (comando inexistente)
- McpClient: erro de tool (isError=true no resultado)
- McpClient: timeout
- McpManager: add_server, server_names, get_client, list_all_tools, stop_all
- McpManager.from_config(): dict e McpSection Pydantic
- _blocks_to_text: str, lista de blocks, None
- tool_router._resolve_mcp_server: env var e mcp_config
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.mcp_client import (
    McpClient,
    McpConnectionError,
    McpError,
    McpManager,
    McpServerConfig,
    McpTimeoutError,
    McpToolError,
    _blocks_to_text,
)


# ---------------------------------------------------------------------------
# Helpers — servidor MCP fake via subprocess
# ---------------------------------------------------------------------------

_SERVER_SCRIPT = """\
import sys
import json

def respond(id_, result):
    msg = json.dumps({"jsonrpc": "2.0", "id": id_, "result": result})
    sys.stdout.write(msg + "\\n")
    sys.stdout.flush()

def error(id_, code, msg):
    obj = json.dumps({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": msg}})
    sys.stdout.write(obj + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except Exception:
        continue
    method = req.get("method", "")
    id_ = req.get("id")
    if id_ is None:
        # notificação — ignora
        continue
    if method == "initialize":
        respond(id_, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-mcp", "version": "0.1"},
        })
    elif method == "tools/list":
        respond(id_, {
            "tools": [
                {
                    "name": "echo",
                    "description": "Ecoa texto",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                    },
                }
            ]
        })
    elif method == "tools/call":
        tool_name = req.get("params", {}).get("name", "")
        arguments = req.get("params", {}).get("arguments", {})
        if tool_name == "echo":
            respond(id_, {
                "content": [{"type": "text", "text": arguments.get("text", "")}]
            })
        elif tool_name == "error_tool":
            respond(id_, {
                "isError": True,
                "content": [{"type": "text", "text": "tool failed"}],
            })
        else:
            error(id_, -32601, f"tool not found: {tool_name}")
    else:
        error(id_, -32601, f"unknown method: {method}")
"""


def _make_config(name: str = "test") -> McpServerConfig:
    """Cria McpServerConfig apontando para o script fake."""
    return McpServerConfig(
        name=name,
        command=[sys.executable, "-c", _SERVER_SCRIPT],
        timeout=10.0,
    )


# ---------------------------------------------------------------------------
# _blocks_to_text
# ---------------------------------------------------------------------------

class TestBlocksToText:
    def test_none_returns_empty(self):
        assert _blocks_to_text(None) == ""

    def test_str_passthrough(self):
        assert _blocks_to_text("hello") == "hello"

    def test_text_blocks(self):
        blocks = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        result = _blocks_to_text(blocks)
        assert "a" in result
        assert "b" in result

    def test_image_block_summarized(self):
        blocks = [{"type": "image", "mimeType": "image/png"}]
        result = _blocks_to_text(blocks)
        assert "imagem" in result

    def test_unknown_block_with_text_key(self):
        blocks = [{"type": "custom", "text": "valor"}]
        assert "valor" in _blocks_to_text(blocks)

    def test_empty_list_returns_empty(self):
        assert _blocks_to_text([]) == ""


# ---------------------------------------------------------------------------
# McpServerConfig
# ---------------------------------------------------------------------------

class TestMcpServerConfig:
    def test_defaults(self):
        cfg = McpServerConfig(name="srv", command=["python", "-m", "x"])
        assert cfg.timeout == 30.0
        assert cfg.env == {}
        assert cfg.cwd is None

    def test_custom_timeout(self):
        cfg = McpServerConfig(name="s", command=["cmd"], timeout=60.0)
        assert cfg.timeout == 60.0

    def test_env_dict(self):
        cfg = McpServerConfig(name="s", command=["cmd"], env={"MY_VAR": "val"})
        assert cfg.env["MY_VAR"] == "val"


# ---------------------------------------------------------------------------
# McpClient — ciclo de vida e chamadas básicas
# ---------------------------------------------------------------------------

class TestMcpClientBasic:
    def test_start_and_stop(self):
        cfg = _make_config()
        client = McpClient(cfg)
        client.start()
        assert client.is_running
        client.stop()
        assert not client.is_running

    def test_context_manager(self):
        cfg = _make_config()
        with McpClient(cfg) as client:
            assert client.is_running
        assert not client.is_running

    def test_list_tools(self):
        cfg = _make_config()
        with McpClient(cfg) as client:
            tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "echo"

    def test_list_tools_cached(self):
        cfg = _make_config()
        with McpClient(cfg) as client:
            t1 = client.list_tools()
            t2 = client.list_tools()
        assert t1 is t2  # retorna mesmo objeto (cache)

    def test_list_tools_force_refresh(self):
        cfg = _make_config()
        with McpClient(cfg) as client:
            t1 = client.list_tools()
            t2 = client.list_tools(force_refresh=True)
        assert t1 == t2  # conteúdo igual

    def test_call_tool_echo(self):
        cfg = _make_config()
        with McpClient(cfg) as client:
            result = client.call_tool("echo", {"text": "hello world"})
        assert result == "hello world"

    def test_call_tool_empty_args(self):
        cfg = _make_config()
        with McpClient(cfg) as client:
            result = client.call_tool("echo", {})
        assert result == ""

    def test_call_tool_none_args(self):
        cfg = _make_config()
        with McpClient(cfg) as client:
            result = client.call_tool("echo", None)
        assert result == ""

    def test_call_tool_isError_raises(self):
        cfg = _make_config()
        with McpClient(cfg) as client:
            with pytest.raises(McpToolError, match="tool failed"):
                client.call_tool("error_tool", {})

    def test_call_tool_unknown_raises(self):
        cfg = _make_config()
        with McpClient(cfg) as client:
            with pytest.raises((McpToolError, McpError)):
                client.call_tool("nao_existe", {})

    def test_server_info_populated(self):
        cfg = _make_config()
        with McpClient(cfg) as client:
            info = client.server_info()
        # initialize retorna protocolVersion e serverInfo
        assert isinstance(info, dict)

    def test_start_idempotent(self):
        cfg = _make_config()
        client = McpClient(cfg)
        client.start()
        proc1 = client._proc
        client.start()  # não deve reiniciar
        proc2 = client._proc
        assert proc1 is proc2
        client.stop()

    def test_stop_idempotent(self):
        cfg = _make_config()
        client = McpClient(cfg)
        client.start()
        client.stop()
        client.stop()  # segunda chamada não deve levantar

    def test_ensure_started_auto_starts(self):
        cfg = _make_config()
        client = McpClient(cfg)
        # _ensure_started deve iniciar se não estiver rodando
        tools = client.list_tools()
        assert tools
        client.stop()


# ---------------------------------------------------------------------------
# McpClient — erros de conexão
# ---------------------------------------------------------------------------

class TestMcpClientErrors:
    def test_command_not_found_raises_connection_error(self):
        cfg = McpServerConfig(
            name="bad",
            command=["nao_existe_mesmo_que_procure", "--arg"],
            timeout=5.0,
        )
        client = McpClient(cfg)
        with pytest.raises(McpConnectionError, match="nao encontrado"):
            client.start()

    def test_timeout_raises_timeout_error(self):
        # Servidor que não responde ao initialize
        silent_script = "import sys; import time; time.sleep(100)"
        cfg = McpServerConfig(
            name="silent",
            command=[sys.executable, "-c", silent_script],
            timeout=0.5,
        )
        client = McpClient(cfg)
        with pytest.raises(McpTimeoutError):
            client.start()
        client.stop()


# ---------------------------------------------------------------------------
# McpManager
# ---------------------------------------------------------------------------

class TestMcpManager:
    def test_add_server_and_names(self):
        manager = McpManager()
        manager.add_server(McpServerConfig("a", ["cmd_a"]))
        manager.add_server(McpServerConfig("b", ["cmd_b"]))
        names = manager.server_names()
        assert "a" in names
        assert "b" in names

    def test_get_client_unknown_raises(self):
        manager = McpManager()
        with pytest.raises(McpError, match="nao configurado"):
            manager.get_client("nao_existe")

    def test_get_client_starts_server(self):
        manager = McpManager(configs=[_make_config("srv")])
        client = manager.get_client("srv")
        assert client.is_running
        manager.stop_all()

    def test_list_tools_via_manager(self):
        manager = McpManager(configs=[_make_config("srv")])
        tools = manager.list_tools("srv")
        assert any(t["name"] == "echo" for t in tools)
        manager.stop_all()

    def test_call_tool_via_manager(self):
        manager = McpManager(configs=[_make_config("srv")])
        result = manager.call_tool("srv", "echo", {"text": "manager test"})
        assert result == "manager test"
        manager.stop_all()

    def test_list_all_tools(self):
        manager = McpManager(configs=[_make_config("srv1"), _make_config("srv2")])
        all_tools = manager.list_all_tools()
        assert "srv1" in all_tools
        assert "srv2" in all_tools
        manager.stop_all()

    def test_stop_all(self):
        manager = McpManager(configs=[_make_config("s1"), _make_config("s2")])
        # Força conexão com ambos
        manager.get_client("s1")
        manager.get_client("s2")
        manager.stop_all()
        assert len(manager._clients) == 0

    def test_context_manager(self):
        with McpManager(configs=[_make_config("s")]) as manager:
            result = manager.call_tool("s", "echo", {"text": "cm"})
        assert result == "cm"
        assert not manager._clients

    def test_remove_server(self):
        manager = McpManager(configs=[_make_config("s1"), _make_config("s2")])
        manager.remove_server("s1")
        assert "s1" not in manager.server_names()
        manager.stop_all()

    def test_get_client_reuses_running(self):
        manager = McpManager(configs=[_make_config("s")])
        c1 = manager.get_client("s")
        c2 = manager.get_client("s")
        assert c1 is c2
        manager.stop_all()

    def test_list_all_tools_failed_server_excluded(self):
        """Servidores que falham são omitidos de list_all_tools sem exceção."""
        bad = McpServerConfig("bad", ["nao_existe_comando_xz"], timeout=1.0)
        good = _make_config("good")
        manager = McpManager(configs=[bad, good])
        # Não deve levantar exceção
        all_tools = manager.list_all_tools()
        assert "good" in all_tools
        manager.stop_all()


# ---------------------------------------------------------------------------
# McpManager.from_config
# ---------------------------------------------------------------------------

class TestFromConfig:
    def test_from_dict(self):
        cfg_dict = {
            "servers": {
                "my_server": {
                    "command": ["python", "-c", "pass"],
                    "timeout": 15,
                    "env": {"MY": "val"},
                }
            }
        }
        manager = McpManager.from_config(cfg_dict)
        assert "my_server" in manager.server_names()

    def test_from_none(self):
        manager = McpManager.from_config(None)
        assert manager.server_names() == []

    def test_from_empty_dict(self):
        manager = McpManager.from_config({"servers": {}})
        assert manager.server_names() == []

    def test_from_pydantic_like(self):
        """Simula objeto com atributo 'servers' (McpSection Pydantic)."""
        server_obj = MagicMock()
        server_obj.command = ["python", "-c", "pass"]
        server_obj.env = {}
        server_obj.timeout = 30
        server_obj.cwd = None

        config_obj = MagicMock()
        config_obj.servers = {"srv": server_obj}

        manager = McpManager.from_config(config_obj)
        assert "srv" in manager.server_names()

    def test_command_as_string_normalized(self):
        cfg_dict = {
            "servers": {
                "s": {"command": "python -c pass"}
            }
        }
        manager = McpManager.from_config(cfg_dict)
        srv_cfg = manager._configs.get("s")
        assert srv_cfg is not None
        assert srv_cfg.command == ["python", "-c", "pass"]


# ---------------------------------------------------------------------------
# ToolRouter._resolve_mcp_server — integração
# ---------------------------------------------------------------------------

class TestResolveServerCmd:
    @pytest.fixture
    def router(self, tmp_path: Path) -> object:
        from bauer.tool_router import ToolRouter
        ws = tmp_path / "workspace"
        ws.mkdir()
        return ToolRouter(workspace=ws, audit_enabled=False)

    def test_resolve_via_env_var(self, router, monkeypatch):
        monkeypatch.setenv("MCP_SERVER_MY_SRV", "python -c pass")
        cmd, env, timeout = router._resolve_mcp_server("my_srv")
        assert cmd == ["python", "-c", "pass"]
        assert env == {}
        assert timeout == 30.0

    def test_resolve_via_mcp_config_dict(self, router):
        mcp_mock = MagicMock()
        mcp_mock.servers = {
            "my_srv": MagicMock(
                command=["python", "-m", "srv"],
                env={"X": "1"},
                timeout=45.0,
            )
        }
        router._mcp_config = mcp_mock
        cmd, env, timeout = router._resolve_mcp_server("my_srv")
        assert cmd == ["python", "-m", "srv"]
        assert env == {"X": "1"}
        assert timeout == 45.0

    def test_resolve_missing_raises_tool_error(self, router):
        from bauer.tool_router import ToolError
        with pytest.raises(ToolError, match="nao configurado"):
            router._resolve_mcp_server("nao_existe_nao_mesmo")
