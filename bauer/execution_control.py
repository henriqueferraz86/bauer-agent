"""Cooperative interruption scoped to one governed execution.

No control means the existing CLI/web execution path is unchanged. A control
never declares a run terminal: the owner waits for its workers and lets the
Kernel record the final status and cost.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .provider_retry import ProviderRetryInterrupted

logger = logging.getLogger("bauer.execution_control")
_active_control: ContextVar[ExecutionControl | None] = ContextVar("bauer_execution_control", default=None)


class ExecutionControlError(RuntimeError):
    """Governance could not verify that this execution may continue."""


class ExecutionControl:
    def __init__(self, run_id: str | None = None, session_id: str = "",
                 workspace: Path | None = None, *,
                 check_callback: Callable[[], str | None] | None = None,
                 cancel_event: threading.Event | None = None) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self.workspace = workspace
        self.check_callback = check_callback
        self.cancel_event = cancel_event if cancel_event is not None else threading.Event()
        self._reason: str | None = None
        self._lock = threading.Lock()
        self._processes: dict[int, tuple[Any, Callable[[], None]]] = {}
        self._termination_threads: dict[int, threading.Thread] = {}
        self.max_tool_calls: int | None = None
        self._tool_calls = 0

    @property
    def tool_calls(self) -> int:
        with self._lock:
            return self._tool_calls

    def before_tool(self) -> None:
        """Reserve one real dispatch, atomically, immediately before its body."""
        self.checkpoint()
        with self._lock:
            exhausted = self.max_tool_calls is not None and self._tool_calls >= self.max_tool_calls
            if not exhausted:
                self._tool_calls += 1
        if exhausted:
            self.request_cancel("budget_exhausted")
            self.checkpoint()

    def request_cancel(self, reason: str = "cancelled") -> None:
        normalized = reason if reason in {"kill_switch", "budget_exhausted"} else "cancelled"
        with self._lock:
            if self._reason is None:
                self._reason = normalized
            self.cancel_event.set()
            processes = list(self._processes.values())
        for process, terminate in processes:
            self._start_termination(process, terminate)

    def _start_termination(self, process: Any, terminate: Callable[[], None]) -> None:
        with self._lock:
            key = id(process)
            if key in self._termination_threads:
                return
            worker = threading.Thread(target=lambda: self._terminate(terminate),
                                      name=f"bauer-process-stop:{self.run_id or 'pending'}", daemon=True)
            self._termination_threads[key] = worker
            # Start under the lock, so close() cannot join an unstarted thread.
            worker.start()

    @staticmethod
    def _terminate(terminate: Callable[[], None]) -> None:
        try:
            terminate()
        except Exception:
            logger.debug("Managed process termination failed", exc_info=True)

    def check(self) -> str | None:
        if self.cancel_event.is_set():
            with self._lock:
                return self._reason or "cancelled"
        if self.check_callback is not None:
            try:
                reason = self.check_callback()
            except Exception as exc:
                raise ExecutionControlError("Execution control is unavailable") from exc
            if reason:
                self.request_cancel(reason)
                with self._lock:
                    return self._reason
        return None

    def checkpoint(self) -> None:
        reason = self.check()
        if reason == "kill_switch":
            raise ProviderRetryInterrupted("kill_switch")
        if reason == "budget_exhausted":
            raise ProviderRetryInterrupted("budget_exhausted")
        if reason:
            raise ProviderRetryInterrupted("cancelled")

    @contextmanager
    def activate(self) -> Iterator[ExecutionControl]:
        token = _active_control.set(self)
        try:
            yield self
        finally:
            _active_control.reset(token)

    def register_process(self, process: Any, terminate_callback: Callable[[], None]) -> None:
        with self._lock:
            self._processes[id(process)] = (process, terminate_callback)
            stopped = self.cancel_event.is_set()
        if stopped:
            self._start_termination(process, terminate_callback)

    def unregister_process(self, process: Any) -> None:
        with self._lock:
            self._processes.pop(id(process), None)

    def close(self) -> None:
        """Join owned process cleanup without changing a successful outcome.

        Without cancellation, a process deliberately left running in the
        background (e.g. ``process action=start``) must survive past this
        turn — this only settles cleanup already triggered by a cancel.
        """
        if not self.cancel_event.is_set():
            return
        with self._lock:
            processes = list(self._processes.values())
        for process, terminate in processes:
            self._start_termination(process, terminate)
        with self._lock:
            workers = list(self._termination_threads.values())
        for worker in workers:
            worker.join()
        for process, _ in processes:
            wait = getattr(process, "wait", None)
            if callable(wait):
                wait()
            self.unregister_process(process)


def current_control() -> ExecutionControl | None:
    return _active_control.get()


def checkpoint_execution() -> None:
    control = current_control()
    if control is not None:
        control.checkpoint()


def controlled_sleep(seconds: float) -> None:
    control = current_control()
    if control is None:
        time.sleep(seconds)
        return
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        control.checkpoint()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        control.cancel_event.wait(min(0.1, remaining))
