"""LSP (Language Server Protocol) client for Bauer Agent.

Provides JSON-RPC 2.0 client to communicate with LSP servers (pyright, jedi, etc.)
over stdio. Used by the lsp_* tool family in tool_router.py.

Usage::

    from bauer.lsp import LspManager, KNOWN_SERVERS
    mgr = await LspManager.start(KNOWN_SERVERS["pyright"], workspace="/path/to/project")
    result = await mgr.client().hover("file:///path/to/file.py", 10, 5)
    await mgr.stop()
"""

from .client import LspClient
from .manager import LspManager, LspServerConfig
from .servers import KNOWN_SERVERS

__all__ = ["LspClient", "LspManager", "LspServerConfig", "KNOWN_SERVERS"]
