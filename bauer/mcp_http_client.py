"""MCP HTTP client — connects to remote MCP servers via HTTP/SSE.

Implements the MCP Streamable HTTP transport spec:
  https://spec.modelcontextprotocol.io/specification/basic/transports/#streamable-http

API is deliberately identical to ``McpClient`` (stdio) so both can be used
interchangeably through ``McpManager``.

Usage::

    from bauer.mcp_http_client import McpHttpClient

    client = McpHttpClient("https://mcp.stripe.com", headers={"Authorization": "Bearer sk_..."})
    await client.initialize()
    tools = await client.list_tools()      # [{"name": "...", "description": "..."}]
    result = await client.call_tool("list_customers", {"limit": 5})

Or synchronously (wraps asyncio.run internally)::

    client = McpHttpClient("https://mcp.example.com")
    tools = client.list_tools_sync()
    result = client.call_tool_sync("my_tool", {"arg": "val"})
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any, Iterator


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class McpHttpError(Exception):
    """Raised when the MCP HTTP server returns an error or is unreachable."""


# ---------------------------------------------------------------------------
# McpHttpClient
# ---------------------------------------------------------------------------


class McpHttpClient:
    """JSON-RPC 2.0 over HTTP client for remote MCP servers.

    Supports:
      - Standard HTTP POST for request/response (non-streaming)
      - SSE streaming GET for server-sent notifications
      - Automatic ``initialize`` handshake on first use
      - Tool listing with cache (invalidated after 60s)
      - Compatible with ``McpManager`` API

    Parameters
    ----------
    url:
        Base URL of the MCP server (e.g. ``https://mcp.stripe.com``).
        The client will POST to ``{url}`` directly.
    headers:
        Extra HTTP headers (e.g. ``Authorization: Bearer ...``).
    timeout:
        Request timeout in seconds.  Default 30.
    sse_path:
        Path suffix for SSE streaming endpoint.  Default ``/sse``.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        sse_path: str = "/sse",
    ) -> None:
        self._url = url.rstrip("/")
        self._headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "bauer-agent/1.0",
        }
        if headers:
            self._headers.update(headers)
        self._timeout = timeout
        self._sse_path = sse_path

        self._initialized = False
        self._server_info: dict[str, Any] = {}
        self._capabilities: dict[str, Any] = {}

        # Tool cache
        self._tools_cache: list[dict[str, Any]] = []
        self._tools_cache_time: float = 0.0
        self._tools_cache_ttl: float = 60.0

        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> dict[str, Any]:
        """Send the MCP initialize request and store server capabilities.

        Called automatically by :meth:`list_tools` / :meth:`call_tool` if needed.
        """
        resp = self._jsonrpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "roots": {"listChanged": False},
                "sampling": {},
            },
            "clientInfo": {
                "name": "bauer-agent",
                "version": "1.0",
            },
        })
        if "error" in resp:
            raise McpHttpError(f"initialize failed: {resp['error']}")
        result = resp.get("result", {})
        self._server_info = result.get("serverInfo", {})
        self._capabilities = result.get("capabilities", {})

        # Send initialized notification (fire-and-forget)
        try:
            self._notify("notifications/initialized", {})
        except Exception:
            pass

        self._initialized = True
        return result

    def close(self) -> None:
        """No-op for HTTP client (no persistent connection to close)."""

    def __enter__(self) -> "McpHttpClient":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Tool operations
    # ------------------------------------------------------------------

    def list_tools(self, *, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Return the list of tools from the server.

        Results are cached for :attr:`_tools_cache_ttl` seconds.
        """
        self._ensure_initialized()
        now = time.monotonic()
        with self._lock:
            if (
                not force_refresh
                and self._tools_cache
                and (now - self._tools_cache_time) < self._tools_cache_ttl
            ):
                return list(self._tools_cache)

        resp = self._jsonrpc("tools/list", {})
        if "error" in resp:
            raise McpHttpError(f"tools/list failed: {resp['error']}")
        tools = resp.get("result", {}).get("tools", [])
        with self._lock:
            self._tools_cache = tools
            self._tools_cache_time = now
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Invoke a tool and return its result content.

        Parameters
        ----------
        name:
            Tool name as returned by :meth:`list_tools`.
        arguments:
            Tool arguments dict.  ``None`` → ``{}``.

        Returns the ``content`` field of the tool result (list of content blocks).
        Raises :exc:`McpHttpError` if the server returns an error.
        """
        self._ensure_initialized()
        resp = self._jsonrpc("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        if "error" in resp:
            raise McpHttpError(f"tools/call '{name}' failed: {resp['error']}")
        result = resp.get("result", {})
        if result.get("isError"):
            content = result.get("content", [])
            texts = [c.get("text", "") for c in content if c.get("type") == "text"]
            raise McpHttpError(f"Tool '{name}' returned error: {' '.join(texts)}")
        return result.get("content", [])

    def call_tool_text(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Like :meth:`call_tool` but returns a plain text string.

        Concatenates all ``text`` content blocks; falls back to JSON serialization.
        """
        content = self.call_tool(name, arguments)
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        if texts:
            return "\n".join(texts)
        return json.dumps(content, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Sync convenience wrappers (used by McpManager which is synchronous)
    # ------------------------------------------------------------------

    def list_tools_sync(self) -> list[dict[str, Any]]:
        return self.list_tools()

    def call_tool_sync(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        return self.call_tool_text(name, arguments)

    # ------------------------------------------------------------------
    # SSE streaming events
    # ------------------------------------------------------------------

    def iter_sse_events(self, *, timeout: float | None = None) -> Iterator[dict[str, Any]]:
        """Connect to the SSE endpoint and yield parsed events as dicts.

        This is a generator — each ``yield`` returns one event dict with
        ``event`` (str) and ``data`` (parsed JSON or raw str) keys.

        Raises :exc:`McpHttpError` if the SSE endpoint is unreachable.
        """
        try:
            import httpx
        except ImportError as exc:
            raise McpHttpError("httpx is required for SSE streaming") from exc

        sse_url = self._url + self._sse_path
        try:
            with httpx.stream(
                "GET",
                sse_url,
                headers=self._headers,
                timeout=timeout or self._timeout,
            ) as resp:
                if resp.status_code != 200:
                    raise McpHttpError(
                        f"SSE endpoint {sse_url} returned HTTP {resp.status_code}"
                    )
                event_type = "message"
                data_lines: list[str] = []
                for line in resp.iter_lines():
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif line == "":
                        if data_lines:
                            raw = "\n".join(data_lines)
                            try:
                                parsed = json.loads(raw)
                            except json.JSONDecodeError:
                                parsed = raw
                            yield {"event": event_type, "data": parsed}
                            event_type = "message"
                            data_lines = []
        except McpHttpError:
            raise
        except Exception as exc:
            raise McpHttpError(f"SSE stream error: {exc}") from exc

    # ------------------------------------------------------------------
    # Internal JSON-RPC helpers
    # ------------------------------------------------------------------

    def _jsonrpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            import httpx
        except ImportError as exc:
            raise McpHttpError(
                "httpx is required for McpHttpClient. "
                "Install with: pip install httpx"
            ) from exc

        req_id = str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        try:
            resp = httpx.post(
                self._url,
                json=payload,
                headers=self._headers,
                timeout=self._timeout,
                follow_redirects=True,
            )
        except httpx.TimeoutException as exc:
            raise McpHttpError(f"Request timed out: {method}") from exc
        except httpx.RequestError as exc:
            raise McpHttpError(f"Connection error ({method}): {exc}") from exc

        if resp.status_code == 404:
            # Server may use a path suffix — try /mcp
            try:
                resp2 = httpx.post(
                    self._url + "/mcp",
                    json=payload,
                    headers=self._headers,
                    timeout=self._timeout,
                )
                if resp2.status_code == 200:
                    self._url = self._url + "/mcp"
                    return resp2.json()
            except Exception:
                pass

        if resp.status_code not in (200, 202):
            raise McpHttpError(
                f"HTTP {resp.status_code} from {self._url} [{method}]: "
                f"{resp.text[:200]}"
            )

        try:
            return resp.json()
        except json.JSONDecodeError as exc:
            raise McpHttpError(f"Invalid JSON response from {self._url}: {exc}") from exc

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Fire-and-forget notification (no id, no response expected)."""
        try:
            import httpx
            payload = {"jsonrpc": "2.0", "method": method, "params": params}
            httpx.post(
                self._url,
                json=payload,
                headers=self._headers,
                timeout=5.0,
            )
        except Exception:
            pass  # notifications are best-effort

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self.initialize()

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        status = "initialized" if self._initialized else "not initialized"
        return f"McpHttpClient(url={self._url!r}, {status})"
