"""LspManager — launches and manages an LSP server process."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from .client import LspClient
from .servers import LspServerConfig

logger = logging.getLogger(__name__)


class LspManager:
    """Launches and maintains a single LSP server process.

    Usage::

        mgr = await LspManager.start(KNOWN_SERVERS["pyright"], workspace)
        client = mgr.client()
        result = await client.hover(uri, line, char)
        await mgr.stop()
    """

    def __init__(self, proc: asyncio.subprocess.Process, client: LspClient) -> None:
        self._proc = proc
        self._client = client

    @classmethod
    async def start(
        cls,
        server: LspServerConfig,
        workspace: str | Path,
    ) -> "LspManager":
        """Start the LSP server and send initialize."""
        cmd = server.cmd
        if not shutil.which(cmd[0]):
            raise FileNotFoundError(
                f"LSP server '{cmd[0]}' not found. {server.install_hint}"
            )

        env = os.environ.copy()
        env.update(server.extra_env)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )

        client = LspClient(proc)
        await client.start()

        root_uri = Path(workspace).as_uri()
        try:
            await client.initialize(root_uri)
            await client.initialized()
        except Exception as exc:
            logger.warning("LSP initialize failed: %s", exc)

        return cls(proc, client)

    def client(self) -> LspClient:
        return self._client

    async def stop(self) -> None:
        await self._client.shutdown()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._proc.kill()

    @property
    def is_running(self) -> bool:
        return self._proc.returncode is None


# ---------------------------------------------------------------------------
# Module-level singleton manager (lazy, per workspace)
# ---------------------------------------------------------------------------

_managers: dict[str, LspManager] = {}


async def get_or_start(server: LspServerConfig, workspace: str) -> LspManager | None:
    """Return existing manager for workspace or start a new one.

    Returns None if the server binary is not found.
    """
    key = f"{server.cmd[0]}:{workspace}"
    mgr = _managers.get(key)
    if mgr and mgr.is_running:
        return mgr

    try:
        mgr = await LspManager.start(server, workspace)
        _managers[key] = mgr
        return mgr
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.debug("Failed to start LSP manager: %s", exc)
        return None
