"""Trigger manager — event-driven task scheduling for the autonomous daemon.

Triggers watch external conditions and fire a callback when they're met.
The daemon uses triggers to start processing without constant polling.

Built-in trigger types
-----------------------
* :class:`CronTrigger` — fires on a schedule (cron-like, uses simple interval
  for now; a full cron parser is pluggable via ``croniter`` if installed).
* :class:`FilesystemTrigger` — fires when a path is created / modified /
  deleted (uses a polling loop; no OS-level inotify dependency).
* :class:`GitTrigger` — fires when a new commit appears on a branch.
* :class:`WebhookTrigger` — fires when an HTTP POST arrives on a local port.

All triggers share a common base class :class:`BaseTrigger` and emit
:class:`TriggerEvent` objects to a shared callback.

Usage::

    from bauer.trigger_manager import TriggerManager, CronTrigger

    manager = TriggerManager()
    manager.add(CronTrigger("every_minute", interval_seconds=60))

    async def handle_trigger(event):
        print(f"Triggered: {event.trigger_id} — {event.reason}")

    await manager.start(callback=handle_trigger)
    ...
    await manager.stop()
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class TriggerEvent:
    """Fired by a trigger when its condition is met."""

    trigger_id: str
    """Unique name for this trigger."""

    trigger_type: str
    """E.g. 'cron', 'filesystem', 'git', 'webhook'."""

    reason: str
    """Human-readable description of what fired the trigger."""

    payload: dict[str, Any] = field(default_factory=dict)
    """Optional structured data about the event."""

    timestamp: float = field(default_factory=time.time)


TriggerCallback = Callable[[TriggerEvent], Awaitable[None]]
"""``async (event: TriggerEvent) -> None``"""


# ---------------------------------------------------------------------------
# BaseTrigger
# ---------------------------------------------------------------------------


class BaseTrigger(ABC):
    """Abstract base for all trigger types.

    Parameters
    ----------
    trigger_id:
        Unique name for this trigger (used in events and logging).
    enabled:
        If False, the trigger is registered but never fires.
    """

    def __init__(self, trigger_id: str, *, enabled: bool = True) -> None:
        self.trigger_id = trigger_id
        self.enabled = enabled
        self._fire_count: int = 0
        self._last_fired: float | None = None

    @abstractmethod
    async def run(self, callback: TriggerCallback, shutdown: asyncio.Event) -> None:
        """Run the trigger loop until *shutdown* is set."""

    async def _fire(self, callback: TriggerCallback, reason: str,
                    payload: dict[str, Any] | None = None) -> None:
        self._fire_count += 1
        self._last_fired = time.time()
        event = TriggerEvent(
            trigger_id=self.trigger_id,
            trigger_type=type(self).__name__.replace("Trigger", "").lower(),
            reason=reason,
            payload=payload or {},
        )
        logger.debug("trigger[%s] fired: %s", self.trigger_id, reason)
        try:
            await callback(event)
        except Exception as exc:
            logger.error("trigger[%s] callback raised: %s", self.trigger_id, exc)

    @property
    def fire_count(self) -> int:
        return self._fire_count

    @property
    def last_fired(self) -> float | None:
        return self._last_fired

    def stats(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "trigger_type": type(self).__name__,
            "enabled": self.enabled,
            "fire_count": self._fire_count,
            "last_fired": self._last_fired,
        }


# ---------------------------------------------------------------------------
# CronTrigger
# ---------------------------------------------------------------------------


class CronTrigger(BaseTrigger):
    """Fire at a fixed interval.

    Parameters
    ----------
    trigger_id:
        Unique name.
    interval_seconds:
        How often to fire (wall-clock seconds).  Default 60.
    fire_on_start:
        If True, fire once immediately when the trigger starts.
        Default False.
    max_fires:
        Stop after this many fires.  ``None`` = unlimited.
    """

    def __init__(
        self,
        trigger_id: str,
        *,
        interval_seconds: float = 60.0,
        fire_on_start: bool = False,
        max_fires: int | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(trigger_id, enabled=enabled)
        self.interval_seconds = interval_seconds
        self.fire_on_start = fire_on_start
        self.max_fires = max_fires

    async def run(self, callback: TriggerCallback, shutdown: asyncio.Event) -> None:
        if not self.enabled:
            logger.debug("trigger[%s] disabled — not running", self.trigger_id)
            return

        if self.fire_on_start:
            await self._fire(callback, "cron_start")
            if self.max_fires is not None and self._fire_count >= self.max_fires:
                return

        while not shutdown.is_set():
            # Sleep in small steps so we respond to shutdown quickly.
            remaining = self.interval_seconds
            step = min(0.1, remaining)
            while remaining > 0 and not shutdown.is_set():
                await asyncio.sleep(step)
                remaining -= step
                step = min(0.1, remaining)

            if shutdown.is_set():
                break

            await self._fire(callback, f"cron_interval_{self.interval_seconds}s")
            if self.max_fires is not None and self._fire_count >= self.max_fires:
                logger.info("trigger[%s] reached max_fires=%d", self.trigger_id, self.max_fires)
                break


# ---------------------------------------------------------------------------
# FilesystemTrigger
# ---------------------------------------------------------------------------


class FilesystemTrigger(BaseTrigger):
    """Fire when a path is created, modified, or deleted.

    Uses polling (stat + content hash) — no OS-level dependencies.

    Parameters
    ----------
    trigger_id:
        Unique name.
    path:
        File or directory to watch.
    events:
        Set of event kinds to watch: ``{"created", "modified", "deleted"}``.
        Default: all three.
    poll_interval_seconds:
        How often to check the path.  Default 2.0.
    """

    def __init__(
        self,
        trigger_id: str,
        path: Path | str,
        *,
        events: set[str] | None = None,
        poll_interval_seconds: float = 2.0,
        enabled: bool = True,
    ) -> None:
        super().__init__(trigger_id, enabled=enabled)
        self._path = Path(path)
        self._events = events or {"created", "modified", "deleted"}
        self._poll_interval = poll_interval_seconds
        self._last_hash: str | None = None
        self._existed: bool = False

    async def run(self, callback: TriggerCallback, shutdown: asyncio.Event) -> None:
        if not self.enabled:
            return

        # Prime initial state
        self._existed = self._path.exists()
        self._last_hash = self._compute_hash() if self._existed else None

        while not shutdown.is_set():
            await self._sleep(self._poll_interval, shutdown)
            if shutdown.is_set():
                break

            exists_now = self._path.exists()
            current_hash = self._compute_hash() if exists_now else None

            if not self._existed and exists_now:
                if "created" in self._events:
                    await self._fire(callback, f"path_created: {self._path}",
                                     {"path": str(self._path), "event": "created"})
            elif self._existed and not exists_now:
                if "deleted" in self._events:
                    await self._fire(callback, f"path_deleted: {self._path}",
                                     {"path": str(self._path), "event": "deleted"})
            elif exists_now and current_hash != self._last_hash:
                if "modified" in self._events:
                    await self._fire(callback, f"path_modified: {self._path}",
                                     {"path": str(self._path), "event": "modified"})

            self._existed = exists_now
            self._last_hash = current_hash

    def _compute_hash(self) -> str:
        try:
            p = self._path
            if p.is_file():
                return hashlib.md5(p.read_bytes()).hexdigest()
            elif p.is_dir():
                h = hashlib.md5()
                for child in sorted(p.iterdir()):
                    h.update(child.name.encode())
                    h.update(str(child.stat().st_mtime).encode())
                return h.hexdigest()
        except OSError:
            pass
        return ""

    @staticmethod
    async def _sleep(seconds: float, shutdown: asyncio.Event) -> None:
        step = min(0.1, seconds)
        elapsed = 0.0
        while elapsed < seconds and not shutdown.is_set():
            await asyncio.sleep(step)
            elapsed += step


# ---------------------------------------------------------------------------
# GitTrigger
# ---------------------------------------------------------------------------


class GitTrigger(BaseTrigger):
    """Fire when the HEAD commit of a branch changes.

    Reads ``.git/refs/heads/<branch>`` or ``.git/HEAD`` directly
    (no subprocess dependency, fast and cross-platform).

    Parameters
    ----------
    trigger_id:
        Unique name.
    repo_path:
        Root of the git repository.  Default: current directory.
    branch:
        Branch to watch.  Default ``"master"``.
    poll_interval_seconds:
        How often to check for new commits.  Default 10.0.
    """

    def __init__(
        self,
        trigger_id: str,
        *,
        repo_path: Path | str = ".",
        branch: str = "master",
        poll_interval_seconds: float = 10.0,
        enabled: bool = True,
    ) -> None:
        super().__init__(trigger_id, enabled=enabled)
        self._repo = Path(repo_path)
        self._branch = branch
        self._poll_interval = poll_interval_seconds
        self._last_commit: str | None = None

    async def run(self, callback: TriggerCallback, shutdown: asyncio.Event) -> None:
        if not self.enabled:
            return

        self._last_commit = self._read_head()

        while not shutdown.is_set():
            await self._sleep(self._poll_interval, shutdown)
            if shutdown.is_set():
                break

            current = self._read_head()
            if current and current != self._last_commit:
                old = self._last_commit or "(none)"
                await self._fire(
                    callback,
                    f"new_commit on {self._branch}: {old[:8]}→{current[:8]}",
                    {"branch": self._branch, "old_commit": old, "new_commit": current},
                )
                self._last_commit = current

    def _read_head(self) -> str | None:
        # Try packed-refs first, then loose ref, then HEAD detached
        ref_file = self._repo / ".git" / "refs" / "heads" / self._branch
        try:
            if ref_file.exists():
                return ref_file.read_text().strip()
        except OSError:
            pass
        # Fall back: parse HEAD
        head_file = self._repo / ".git" / "HEAD"
        try:
            if head_file.exists():
                content = head_file.read_text().strip()
                if content.startswith("ref: refs/heads/"):
                    # Follow symbolic ref
                    branch = content.removeprefix("ref: refs/heads/")
                    if branch == self._branch:
                        # Already handled above; might need packed-refs
                        packed = self._repo / ".git" / "packed-refs"
                        if packed.exists():
                            for line in packed.read_text().splitlines():
                                if line.endswith(f"refs/heads/{self._branch}"):
                                    return line.split()[0]
                elif len(content) == 40:
                    return content
        except OSError:
            pass
        return None

    @staticmethod
    async def _sleep(seconds: float, shutdown: asyncio.Event) -> None:
        step = min(0.1, seconds)
        elapsed = 0.0
        while elapsed < seconds and not shutdown.is_set():
            await asyncio.sleep(step)
            elapsed += step


# ---------------------------------------------------------------------------
# WebhookTrigger
# ---------------------------------------------------------------------------


class WebhookTrigger(BaseTrigger):
    """Fire when an HTTP POST arrives on a local port.

    Listens on ``localhost:<port>`` for POST requests.  Any POST body
    is parsed as JSON (if possible) and included in the event payload.

    Parameters
    ----------
    trigger_id:
        Unique name.
    port:
        TCP port to listen on.  Default 9876.
    path:
        URL path to accept POSTs on.  Default ``"/trigger"``.
    secret:
        Optional shared secret.  If set, the request must include an
        ``X-Trigger-Secret`` header matching this value.
    """

    def __init__(
        self,
        trigger_id: str,
        *,
        port: int = 9876,
        path: str = "/trigger",
        secret: str | None = None,
        enabled: bool = True,
    ) -> None:
        super().__init__(trigger_id, enabled=enabled)
        self._port = port
        self._path = path.rstrip("/") or "/"
        self._secret = secret
        self._server: asyncio.Server | None = None

    async def run(self, callback: TriggerCallback, shutdown: asyncio.Event) -> None:
        if not self.enabled:
            return

        self._server = await asyncio.start_server(
            lambda r, w: self._handle(r, w, callback),
            "127.0.0.1", self._port,
        )
        logger.info(
            "trigger[%s] webhook listening on 127.0.0.1:%d%s",
            self.trigger_id, self._port, self._path,
        )

        async with self._server:
            # Wait until shutdown is requested (proper async wait).
            await shutdown.wait()

        logger.info("trigger[%s] webhook stopped", self.trigger_id)

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        callback: TriggerCallback,
    ) -> None:
        import json as _json

        try:
            # Read request line
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not request_line:
                writer.close()
                return

            parts = request_line.decode(errors="replace").strip().split()
            method = parts[0] if parts else ""
            url_path = parts[1] if len(parts) > 1 else "/"

            # Read headers
            headers: dict[str, str] = {}
            content_length = 0
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if line in (b"\r\n", b"\n", b""):
                    break
                if b":" in line:
                    k, _, v = line.decode(errors="replace").partition(":")
                    headers[k.strip().lower()] = v.strip()
                    if k.strip().lower() == "content-length":
                        try:
                            content_length = int(v.strip())
                        except ValueError:
                            pass

            # Validate method and path
            if method != "POST":
                writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
                await writer.drain()
                writer.close()
                return

            if url_path.rstrip("/") != self._path.rstrip("/"):
                writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")
                await writer.drain()
                writer.close()
                return

            # Validate secret
            if self._secret:
                provided = headers.get("x-trigger-secret", "")
                if provided != self._secret:
                    writer.write(b"HTTP/1.1 401 Unauthorized\r\n\r\n")
                    await writer.drain()
                    writer.close()
                    return

            # Read body
            body = b""
            if content_length > 0:
                body = await asyncio.wait_for(
                    reader.read(min(content_length, 65536)), timeout=5.0
                )

            payload: dict[str, Any] = {}
            if body:
                try:
                    payload = _json.loads(body)
                except Exception:
                    payload = {"raw": body.decode(errors="replace")}

            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK")
            await writer.drain()
            writer.close()

            await self._fire(callback, f"webhook_post to {self._path}", payload)

        except Exception as exc:
            logger.debug("trigger[%s] webhook handler error: %s", self.trigger_id, exc)
            try:
                writer.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# TriggerManager
# ---------------------------------------------------------------------------


class TriggerManager:
    """Manage a collection of triggers and run them concurrently.

    Usage::

        manager = TriggerManager()
        manager.add(CronTrigger("hourly", interval_seconds=3600))
        manager.add(FilesystemTrigger("watch_config", Path("config.yaml")))

        async def on_trigger(event):
            print(event.trigger_id, event.reason)

        await manager.start(callback=on_trigger)
        ...
        await manager.stop()
    """

    def __init__(self) -> None:
        self._triggers: dict[str, BaseTrigger] = {}
        self._shutdown: asyncio.Event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._running: bool = False

    def add(self, trigger: BaseTrigger) -> None:
        """Register a trigger.  Must be called before :meth:`start`."""
        if trigger.trigger_id in self._triggers:
            raise ValueError(f"Trigger '{trigger.trigger_id}' already registered")
        self._triggers[trigger.trigger_id] = trigger
        logger.debug("trigger_manager: registered %s", trigger.trigger_id)

    def remove(self, trigger_id: str) -> bool:
        """Unregister a trigger by ID.  Returns True if it existed."""
        existed = trigger_id in self._triggers
        self._triggers.pop(trigger_id, None)
        return existed

    def get(self, trigger_id: str) -> BaseTrigger | None:
        return self._triggers.get(trigger_id)

    def list_triggers(self) -> list[BaseTrigger]:
        return list(self._triggers.values())

    async def start(self, callback: TriggerCallback) -> None:
        """Start all triggers concurrently.

        This is non-blocking — triggers run as asyncio tasks.
        Call :meth:`stop` to shut them down.
        """
        if self._running:
            raise RuntimeError("TriggerManager already running")

        self._running = True
        self._shutdown.clear()

        for trigger in self._triggers.values():
            task = asyncio.create_task(
                trigger.run(callback, self._shutdown),
                name=f"trigger_{trigger.trigger_id}",
            )
            self._tasks.append(task)

        logger.info(
            "trigger_manager: started %d triggers", len(self._triggers)
        )

    async def stop(self) -> None:
        """Signal all triggers to stop and wait for them to finish."""
        self._shutdown.set()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._running = False
        logger.info("trigger_manager: stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    def stats(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "trigger_count": len(self._triggers),
            "triggers": [t.stats() for t in self._triggers.values()],
        }
