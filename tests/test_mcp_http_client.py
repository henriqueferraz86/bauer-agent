"""Tests for bauer/mcp_http_client.py — HTTP/SSE MCP transport."""

from __future__ import annotations

import json
import uuid
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers to build fake httpx responses
# ---------------------------------------------------------------------------


def _make_httpx_response(data: dict, status: int = 200):
    """Return a mock httpx.Response with .json() and .status_code."""
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = data
    resp.text = json.dumps(data)
    return resp


def _jsonrpc_response(result: dict, req_id: str | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": req_id or str(uuid.uuid4()),
        "result": result,
    }


def _jsonrpc_error(code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": "1",
        "error": {"code": code, "message": message},
    }


# ---------------------------------------------------------------------------
# Import guard — skip if httpx not installed
# ---------------------------------------------------------------------------


httpx = pytest.importorskip("httpx", reason="httpx not installed")


from bauer.mcp_http_client import McpHttpClient, McpHttpError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def initialized_client():
    """McpHttpClient with initialize() pre-patched to succeed."""
    client = McpHttpClient("https://mcp.example.com")
    client._initialized = True
    client._server_info = {"name": "test-server", "version": "1.0"}
    client._capabilities = {"tools": {}}
    return client


# ---------------------------------------------------------------------------
# __init__ / repr
# ---------------------------------------------------------------------------


def test_client_repr():
    client = McpHttpClient("https://example.com")
    r = repr(client)
    assert "McpHttpClient" in r
    assert "example.com" in r


def test_client_url_stripped():
    client = McpHttpClient("https://example.com/")
    assert not client._url.endswith("/")


def test_client_custom_headers():
    client = McpHttpClient(
        "https://example.com",
        headers={"Authorization": "Bearer sk-test"},
    )
    assert client._headers.get("Authorization") == "Bearer sk-test"


def test_client_default_content_type():
    client = McpHttpClient("https://example.com")
    assert client._headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# initialize()
# ---------------------------------------------------------------------------


def test_initialize_success():
    client = McpHttpClient("https://mcp.example.com")
    init_response = _jsonrpc_response({
        "serverInfo": {"name": "TestServer", "version": "1.0"},
        "capabilities": {"tools": {}},
    })

    with patch("httpx.post", return_value=_make_httpx_response(init_response)):
        result = client.initialize()

    assert client._initialized is True
    assert client._server_info == {"name": "TestServer", "version": "1.0"}


def test_initialize_error_response():
    client = McpHttpClient("https://mcp.example.com")
    err_response = _jsonrpc_error(-32600, "Invalid request")

    with patch("httpx.post", return_value=_make_httpx_response(err_response)):
        with pytest.raises(McpHttpError, match="initialize failed"):
            client.initialize()


def test_initialize_http_error():
    client = McpHttpClient("https://mcp.example.com")
    with patch("httpx.post", side_effect=httpx.ConnectError("connection refused")):
        with pytest.raises(McpHttpError):
            client.initialize()


# ---------------------------------------------------------------------------
# list_tools()
# ---------------------------------------------------------------------------


def test_list_tools_returns_tools(initialized_client):
    tools_response = _jsonrpc_response({
        "tools": [
            {"name": "read_file", "description": "Read a file", "inputSchema": {}},
            {"name": "write_file", "description": "Write a file", "inputSchema": {}},
        ]
    })
    with patch("httpx.post", return_value=_make_httpx_response(tools_response)):
        tools = initialized_client.list_tools()

    assert len(tools) == 2
    assert tools[0]["name"] == "read_file"


def test_list_tools_caches_results(initialized_client):
    tools_response = _jsonrpc_response({"tools": [{"name": "tool_x", "description": ""}]})
    with patch("httpx.post", return_value=_make_httpx_response(tools_response)) as mock_post:
        initialized_client.list_tools()
        initialized_client.list_tools()  # second call should use cache
    # httpx.post should only be called once (first time)
    assert mock_post.call_count == 1


def test_list_tools_force_refresh(initialized_client):
    tools_response = _jsonrpc_response({"tools": [{"name": "tool_x", "description": ""}]})
    with patch("httpx.post", return_value=_make_httpx_response(tools_response)) as mock_post:
        initialized_client.list_tools()
        initialized_client.list_tools(force_refresh=True)
    # Should call twice — cache bypassed
    assert mock_post.call_count == 2


def test_list_tools_error(initialized_client):
    err_response = _jsonrpc_error(-32601, "Method not found")
    with patch("httpx.post", return_value=_make_httpx_response(err_response)):
        with pytest.raises(McpHttpError, match="tools/list failed"):
            initialized_client.list_tools(force_refresh=True)


# ---------------------------------------------------------------------------
# call_tool()
# ---------------------------------------------------------------------------


def test_call_tool_success(initialized_client):
    tool_response = _jsonrpc_response({
        "content": [
            {"type": "text", "text": "file contents here"}
        ],
        "isError": False,
    })
    with patch("httpx.post", return_value=_make_httpx_response(tool_response)):
        result = initialized_client.call_tool("read_file", {"path": "/tmp/test.txt"})

    assert isinstance(result, list)
    assert result[0]["text"] == "file contents here"


def test_call_tool_text(initialized_client):
    tool_response = _jsonrpc_response({
        "content": [
            {"type": "text", "text": "line one"},
            {"type": "text", "text": "line two"},
        ],
        "isError": False,
    })
    with patch("httpx.post", return_value=_make_httpx_response(tool_response)):
        text = initialized_client.call_tool_text("read_file", {"path": "/tmp/test.txt"})

    assert "line one" in text
    assert "line two" in text


def test_call_tool_is_error(initialized_client):
    err_response = _jsonrpc_response({
        "content": [{"type": "text", "text": "File not found"}],
        "isError": True,
    })
    with patch("httpx.post", return_value=_make_httpx_response(err_response)):
        with pytest.raises(McpHttpError, match="error"):
            initialized_client.call_tool("read_file", {"path": "/nonexistent"})


def test_call_tool_jsonrpc_error(initialized_client):
    err_response = _jsonrpc_error(-32602, "Invalid params")
    with patch("httpx.post", return_value=_make_httpx_response(err_response)):
        with pytest.raises(McpHttpError, match="tools/call"):
            initialized_client.call_tool("bad_tool", {})


def test_call_tool_timeout(initialized_client):
    with patch("httpx.post", side_effect=httpx.TimeoutException("timed out")):
        with pytest.raises(McpHttpError, match="timed out"):
            initialized_client.call_tool("slow_tool", {})


# ---------------------------------------------------------------------------
# Sync wrappers
# ---------------------------------------------------------------------------


def test_list_tools_sync(initialized_client):
    tools_response = _jsonrpc_response({"tools": [{"name": "t1", "description": "d1"}]})
    with patch("httpx.post", return_value=_make_httpx_response(tools_response)):
        tools = initialized_client.list_tools_sync()
    assert isinstance(tools, list)


def test_call_tool_sync(initialized_client):
    tool_response = _jsonrpc_response({
        "content": [{"type": "text", "text": "sync result"}],
        "isError": False,
    })
    with patch("httpx.post", return_value=_make_httpx_response(tool_response)):
        result = initialized_client.call_tool_sync("some_tool", {})
    assert result == "sync result"


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


def test_context_manager_enter_exit():
    client = McpHttpClient("https://example.com")
    client._initialized = True
    with client as c:
        assert c is client
    # close() is a no-op for HTTP — should not raise


# ---------------------------------------------------------------------------
# McpManager HTTP routing
# ---------------------------------------------------------------------------


def test_mcp_manager_add_http_server():
    from bauer.mcp_client import McpManager
    manager = McpManager()
    manager.add_http_server("stripe", "https://mcp.stripe.com", headers={"Authorization": "Bearer sk"})
    assert "stripe" in manager.server_names()


def test_mcp_manager_get_http_client():
    from bauer.mcp_client import McpManager
    manager = McpManager()
    manager.add_http_server("test_http", "https://mcp.test.com")
    client = manager.get_client("test_http")
    assert isinstance(client, McpHttpClient)
    assert "test.com" in client._url


def test_mcp_manager_from_config_url_key():
    """from_config should route servers with 'url' key to McpHttpClient."""
    from bauer.mcp_client import McpManager
    config = {
        "servers": {
            "my_http_server": {
                "url": "https://mcp.myserver.com",
                "timeout": 45,
            }
        }
    }
    manager = McpManager.from_config(config)
    assert "my_http_server" in manager.server_names()
    client = manager.get_client("my_http_server")
    assert isinstance(client, McpHttpClient)


def test_mcp_manager_from_config_command_key():
    """from_config should route servers with 'command' key to McpClient (stdio)."""
    from bauer.mcp_client import McpManager, McpClient
    config = {
        "servers": {
            "my_stdio_server": {
                "command": ["python", "-m", "fake_server"],
            }
        }
    }
    manager = McpManager.from_config(config)
    assert "my_stdio_server" in manager.server_names()
    # It's registered as a stdio server (not yet started)
    assert "my_stdio_server" in manager._configs
