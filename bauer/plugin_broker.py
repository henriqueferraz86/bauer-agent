"""Policy and lifecycle broker for isolated managed plugins."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .plugin_hooks import VALID_HOOKS
from .plugin_manager import ManagedPlugin
from .plugin_process import (
    DEFAULT_MAX_MESSAGE_BYTES,
    DEFAULT_TIMEOUT_S,
    PluginProcess,
    PluginProcessConfig,
    PluginProcessError,
)


class PluginBrokerState(StrEnum):
    STOPPED = "stopped"
    READY = "ready"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class BrokerDispatch:
    plugin_id: str
    event: str
    delivered: bool
    reason: str = ""


@dataclass
class _Entry:
    plugin: ManagedPlugin
    process: PluginProcess
    state: PluginBrokerState = PluginBrokerState.STOPPED
    restarts: deque[float] = field(default_factory=deque)
    last_error: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)


class PluginBroker:
    """Keep managed plugin processes behind a capability-aware boundary."""

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        max_restarts: int = 1,
        restart_window_s: float = 60.0,
    ) -> None:
        if max_restarts < 0:
            raise ValueError("max_restarts must be non-negative")
        if restart_window_s <= 0:
            raise ValueError("restart_window_s must be positive")
        self.timeout_s = timeout_s
        self.max_message_bytes = max_message_bytes
        self.max_restarts = max_restarts
        self.restart_window_s = restart_window_s
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.RLock()

    def register(self, plugin: ManagedPlugin) -> None:
        """Register one enabled managed plugin without starting it eagerly."""
        plugin_id = plugin.manifest.id
        entrypoint = plugin.manifest.entry_point or f"{plugin_id}.py"
        path = (plugin.path / entrypoint).resolve()
        root = plugin.path.resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"plugin entrypoint is outside or missing: {entrypoint}")
        process = PluginProcess(
            PluginProcessConfig(
                plugin_id=plugin_id,
                entry_point=path,
                timeout_s=self.timeout_s,
                max_message_bytes=self.max_message_bytes,
                cwd=Path.cwd(),
            )
        )
        with self._lock:
            previous = self._entries.pop(plugin_id, None)
            if previous is not None:
                previous.process.close()
            self._entries[plugin_id] = _Entry(plugin=plugin, process=process)

    def unregister(self, plugin_id: str) -> None:
        with self._lock:
            entry = self._entries.pop(plugin_id.strip().lower(), None)
        if entry is not None:
            entry.process.close()

    def dispatch(self, event: str, payload: dict[str, Any] | None = None) -> list[BrokerDispatch]:
        """Dispatch a sanitized event to authorized plugins only."""
        event = event.strip()
        if event not in VALID_HOOKS:
            return []
        safe_payload = _sanitize_payload(payload or {})
        with self._lock:
            entries = list(self._entries.values())
        results: list[BrokerDispatch] = []
        for entry in entries:
            plugin_id = entry.plugin.manifest.id
            if not entry.plugin.enabled:
                results.append(BrokerDispatch(plugin_id, event, False, "disabled"))
                continue
            allowed, reason = _event_policy(entry.plugin, event)
            if not allowed:
                results.append(BrokerDispatch(plugin_id, event, False, reason))
                continue
            results.append(self._dispatch_entry(entry, event, safe_payload))
        return results

    def status(self) -> list[dict[str, Any]]:
        """Return operational state without plugin payloads or secrets."""
        with self._lock:
            entries = list(self._entries.values())
        result: list[dict[str, Any]] = []
        for entry in entries:
            with entry.lock:
                self._prune_restarts(entry)
                result.append(
                    {
                        "id": entry.plugin.manifest.id,
                        "version": entry.plugin.manifest.version,
                        "state": entry.state.value,
                        "pid": entry.process.pid,
                        "restarts": len(entry.restarts),
                        "last_error": entry.last_error,
                    }
                )
        return result

    def close(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
        for entry in entries:
            with entry.lock:
                entry.process.close()
                entry.state = PluginBrokerState.STOPPED

    def _dispatch_entry(self, entry: _Entry, event: str, payload: dict[str, Any]) -> BrokerDispatch:
        with entry.lock:
            try:
                self._ensure_ready(entry)
                entry.process.event(event, payload)
                return BrokerDispatch(entry.plugin.manifest.id, event, True)
            except PluginProcessError as exc:
                entry.state = PluginBrokerState.UNHEALTHY
                entry.last_error = str(exc)[:300]
                entry.process.close()
                if not self._can_restart(entry):
                    return BrokerDispatch(entry.plugin.manifest.id, event, False, "unhealthy")
                entry.restarts.append(time.monotonic())
                entry.process = self._new_process(entry.plugin)
                try:
                    self._ensure_ready(entry)
                    entry.process.event(event, payload)
                    return BrokerDispatch(entry.plugin.manifest.id, event, True, "restarted")
                except PluginProcessError as retry_exc:
                    entry.state = PluginBrokerState.UNHEALTHY
                    entry.last_error = str(retry_exc)[:300]
                    entry.process.close()
                    return BrokerDispatch(entry.plugin.manifest.id, event, False, "unhealthy")

    def _ensure_ready(self, entry: _Entry) -> None:
        if entry.state is PluginBrokerState.READY:
            return
        entry.process.start()
        entry.state = PluginBrokerState.READY
        entry.last_error = ""

    def _can_restart(self, entry: _Entry) -> bool:
        self._prune_restarts(entry)
        return len(entry.restarts) < self.max_restarts

    def _prune_restarts(self, entry: _Entry) -> None:
        cutoff = time.monotonic() - self.restart_window_s
        while entry.restarts and entry.restarts[0] < cutoff:
            entry.restarts.popleft()

    def _new_process(self, plugin: ManagedPlugin) -> PluginProcess:
        entrypoint = plugin.manifest.entry_point or f"{plugin.manifest.id}.py"
        return PluginProcess(
            PluginProcessConfig(
                plugin_id=plugin.manifest.id,
                entry_point=(plugin.path / entrypoint).resolve(),
                timeout_s=self.timeout_s,
                max_message_bytes=self.max_message_bytes,
                cwd=Path.cwd(),
            )
        )


def _event_policy(plugin: ManagedPlugin, event: str) -> tuple[bool, str]:
    if event in {"pre_tool_call", "post_tool_call"}:
        capability = "tools"
        permission = "runtime.tools"
    else:
        capability = "events"
        permission = "runtime.events"
    capabilities = set(plugin.manifest.capabilities)
    permissions = set(plugin.manifest.permissions)
    if capability not in capabilities:
        return False, f"missing_capability:{capability}"
    if permission not in permissions:
        return False, f"missing_permission:{permission}"
    return True, ""


_SENSITIVE_KEYS = frozenset(
    {"api_key", "authorization", "headers", "messages", "password", "prompt", "response", "secret", "token"}
)


def _sanitize_payload(value: Any, *, depth: int = 0) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    if depth > 4:
        return {}
    result: dict[str, Any] = {}
    for key, item in value.items():
        normalized = str(key).lower()
        if normalized in _SENSITIVE_KEYS:
            continue
        if isinstance(item, dict):
            result[str(key)] = _sanitize_payload(item, depth=depth + 1)
        elif isinstance(item, list):
            result[str(key)] = [part for part in item[:100] if isinstance(part, (str, int, float, bool))]
        elif item is None or isinstance(item, (str, int, float, bool)):
            result[str(key)] = item
    return result
