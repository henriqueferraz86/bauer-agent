"""Process boundary and JSONL protocol for managed plugins.

This module contains transport concerns only. Policy, event filtering and
restart decisions belong to ``plugin_broker`` and are intentionally kept out
of this first isolation slice.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT_S = 2.0
DEFAULT_MAX_MESSAGE_BYTES = 256 * 1024


class PluginProcessError(RuntimeError):
    """Base error for plugin process lifecycle and protocol failures."""


class PluginProcessTimeout(PluginProcessError):
    """The child did not answer within the configured timeout."""


class PluginProcessProtocolError(PluginProcessError):
    """The child returned a malformed or unrelated protocol message."""


class PluginProcessState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class PluginProcessConfig:
    """Validated transport settings for one managed plugin."""

    plugin_id: str
    entry_point: Path
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES
    python_executable: str = sys.executable
    cwd: Path | None = None

    def __post_init__(self) -> None:
        if not self.plugin_id.strip():
            raise ValueError("plugin_id is required")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        if self.max_message_bytes < 1024:
            raise ValueError("max_message_bytes must be at least 1024")


class PluginProcess:
    """Own one plugin worker and exchange bounded JSONL requests with it."""

    def __init__(self, config: PluginProcessConfig) -> None:
        self.config = config
        self.state = PluginProcessState.STOPPED
        self._process: subprocess.Popen[bytes] | None = None
        self._responses: queue.Queue[bytes | BaseException] = queue.Queue()
        self._reader: threading.Thread | None = None
        self._lock = threading.RLock()

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None and process.poll() is None else None

    def start(self) -> dict[str, Any]:
        """Start the worker and complete its protocol handshake."""
        with self._lock:
            if self.state is PluginProcessState.READY:
                return {"plugin_id": self.config.plugin_id, "protocol": PROTOCOL_VERSION}
            if self.state is PluginProcessState.STARTING:
                raise PluginProcessError("plugin process is already starting")
            self.state = PluginProcessState.STARTING
            command = [
                self.config.python_executable,
                "-m",
                "bauer.plugin_worker",
                "--plugin-id",
                self.config.plugin_id,
                "--entry-point",
                str(self.config.entry_point.resolve()),
            ]
            try:
                self._process = subprocess.Popen(
                    command,
                    cwd=str(self.config.cwd) if self.config.cwd is not None else None,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    close_fds=os.name != "nt",
                )
            except OSError as exc:
                self.state = PluginProcessState.UNHEALTHY
                raise PluginProcessError(f"could not start plugin process: {exc}") from exc
            self._reader = threading.Thread(
                target=self._read_stdout,
                name=f"bauer-plugin-{self.config.plugin_id}",
                daemon=True,
            )
            self._reader.start()

        try:
            return self._request("hello", {})
        except Exception:
            self.state = PluginProcessState.UNHEALTHY
            self.close()
            raise

    def health(self) -> dict[str, Any]:
        """Ask the child to prove that its request loop is responsive."""
        self._ensure_ready()
        return self._request("health", {})

    def event(self, event: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send one already-sanitized event payload to the child."""
        if not event.strip():
            raise ValueError("event is required")
        self._ensure_ready()
        return self._request("event", {"event": event, "payload": payload or {}})

    def close(self) -> None:
        """Request shutdown and terminate the child if it does not comply."""
        with self._lock:
            process = self._process
            if process is None:
                self.state = PluginProcessState.STOPPED
                return
            if process.poll() is None:
                try:
                    self._request("shutdown", {}, timeout=min(self.config.timeout_s, 1.0))
                except PluginProcessError:
                    process.terminate()
                try:
                    process.wait(timeout=min(self.config.timeout_s, 1.0))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
            self._process = None
            self.state = PluginProcessState.STOPPED

    def __enter__(self) -> "PluginProcess":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _ensure_ready(self) -> None:
        if self.state is not PluginProcessState.READY:
            raise PluginProcessError(f"plugin process is {self.state.value}")

    def _request(self, kind: str, payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        process = self._process
        if process is None or process.stdin is None:
            raise PluginProcessError("plugin process is not running")
        request_id = uuid.uuid4().hex
        message = {
            "protocol": PROTOCOL_VERSION,
            "request_id": request_id,
            "kind": kind,
            "payload": payload,
        }
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > self.config.max_message_bytes:
            raise PluginProcessProtocolError("outgoing plugin message exceeds configured limit")
        try:
            with self._lock:
                process.stdin.write(encoded)
                process.stdin.flush()
            response = self._responses.get(timeout=timeout or self.config.timeout_s)
        except queue.Empty as exc:
            raise PluginProcessTimeout(f"plugin '{self.config.plugin_id}' timed out on {kind}") from exc
        except (BrokenPipeError, OSError) as exc:
            self.state = PluginProcessState.UNHEALTHY
            raise PluginProcessError(f"plugin process pipe failed: {exc}") from exc
        if isinstance(response, BaseException):
            self.state = PluginProcessState.UNHEALTHY
            raise PluginProcessError(f"plugin process reader failed: {response}") from response
        result = self._decode_response(response, request_id)
        if kind == "hello":
            self.state = PluginProcessState.READY
        elif kind == "shutdown":
            self.state = PluginProcessState.STOPPED
        return result

    def _decode_response(self, raw: bytes, request_id: str) -> dict[str, Any]:
        if len(raw) > self.config.max_message_bytes:
            raise PluginProcessProtocolError("incoming plugin message exceeds configured limit")
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PluginProcessProtocolError("plugin returned invalid JSON") from exc
        if not isinstance(response, dict):
            raise PluginProcessProtocolError("plugin response must be an object")
        if response.get("protocol") != PROTOCOL_VERSION or response.get("request_id") != request_id:
            raise PluginProcessProtocolError("plugin response does not match the request")
        if response.get("ok") is not True:
            error = str(response.get("error", "unknown plugin error"))[:300]
            raise PluginProcessError(error)
        result = response.get("result", {})
        if not isinstance(result, dict):
            raise PluginProcessProtocolError("plugin result must be an object")
        return result

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            while True:
                line = process.stdout.readline(self.config.max_message_bytes + 1)
                if not line:
                    self._responses.put(PluginProcessError("plugin stdout closed unexpectedly"))
                    return
                if len(line) > self.config.max_message_bytes:
                    self._responses.put(PluginProcessProtocolError("incoming plugin line is too large"))
                    return
                self._responses.put(line.rstrip(b"\r\n"))
        except BaseException as exc:  # pragma: no cover - defensive thread boundary
            self._responses.put(exc)
