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
                 cancel_event: threading.Event | None = None,
                 channel: str | None = None, owner: str | None = None,
                 chat_id: str | None = None, project_id: str | None = None,
                 private_chat: bool = False) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self.workspace = workspace
        self.check_callback = check_callback
        self.cancel_event = cancel_event if cancel_event is not None else threading.Event()
        self._reason: str | None = None
        self._lock = threading.Lock()
        self._wait_condition = threading.Condition(self._lock)
        self._approval_wait_id: str | None = None
        self._approval_wait_digest: str | None = None
        self._approval_wait_deadline: float | str | None = None
        self._approval_wait_started_at: float | None = None
        self._approval_wait_total = 0.0
        self._generation_parent: ExecutionControl | None = None
        self._processes: dict[int, tuple[Any, Callable[[], None]]] = {}
        self._termination_threads: dict[int, threading.Thread] = {}
        self.max_tool_calls: int | None = None
        self._tool_calls = 0

        # Optional channel identity is populated by ChannelExecution. These
        # fields let channel-specific approval surfaces establish provenance
        # without guessing it from a session key.
        self._channel = channel
        self._owner = owner
        self._chat_id = chat_id
        self._project_id = project_id
        self._private_chat = private_chat

    @property
    def channel(self) -> str | None:
        return self._channel

    @property
    def owner(self) -> str | None:
        return self._owner

    @property
    def chat_id(self) -> str | None:
        return self._chat_id

    @property
    def project_id(self) -> str | None:
        return self._project_id

    @property
    def private_chat(self) -> bool:
        return self._private_chat

    @property
    def approval_wait_active(self) -> bool:
        with self._lock:
            return self._approval_wait_id is not None

    @property
    def approval_wait_id(self) -> str | None:
        with self._lock:
            return self._approval_wait_id

    @property
    def approval_wait_digest(self) -> str | None:
        with self._lock:
            return self._approval_wait_digest

    @property
    def approval_wait_deadline(self) -> float | str | None:
        with self._lock:
            return self._approval_wait_deadline

    def begin_approval_wait(self, approval_id: str, digest: str,
                            deadline: float | str | None = None) -> bool:
        """Pause the gateway generation clock while a human decides.

        Only one approval can suspend a live ToolRouter worker at a time. A
        repeated registration for the same approval is idempotent and does
        not reset the pause interval.
        """
        if not approval_id or not digest:
            return False
        with self._wait_condition:
            if self._approval_wait_id is not None:
                return (self._approval_wait_id == approval_id
                        and self._approval_wait_digest == digest)
            self._approval_wait_id = approval_id
            self._approval_wait_digest = digest
            self._approval_wait_deadline = deadline
            self._approval_wait_started_at = time.monotonic()
            self._wait_condition.notify_all()
        parent = self._generation_parent
        if parent is not None and not parent.begin_approval_wait(approval_id, digest, deadline):
            self.end_approval_wait(approval_id)
            return False
        return True

    def end_approval_wait(self, approval_id: str) -> bool:
        """Resume the generation clock iff ``approval_id`` owns the pause."""
        with self._wait_condition:
            if self._approval_wait_id != approval_id:
                return False
            started = self._approval_wait_started_at
            if started is not None:
                self._approval_wait_total += max(0.0, time.monotonic() - started)
            self._approval_wait_id = None
            self._approval_wait_digest = None
            self._approval_wait_deadline = None
            self._approval_wait_started_at = None
            self._wait_condition.notify_all()
        parent = self._generation_parent
        if parent is not None:
            parent.end_approval_wait(approval_id)
        return True

    def wait_for_generation(self, done: threading.Event, timeout: float) -> bool:
        """Wait for ``done`` while charging only time outside human waits.

        This is deliberately a gateway generation timeout only. Kernel
        runtime/budget checks and cooperative cancellation keep their own
        clocks and are not modified by this pause.
        """
        allowance = max(0.0, timeout)
        started = time.monotonic()
        with self._wait_condition:
            paused_at_start = self._approval_wait_total
            while not done.is_set():
                now = time.monotonic()
                current_pause = (max(0.0, now - self._approval_wait_started_at)
                                 if self._approval_wait_started_at is not None else 0.0)
                paused = self._approval_wait_total - paused_at_start + current_pause
                charged = max(0.0, now - started - paused)
                remaining = allowance - charged
                if remaining <= 0:
                    return False
                if self._approval_wait_id is not None:
                    self._wait_condition.wait()
                else:
                    self._wait_condition.wait(timeout=remaining)
            return True

    def notify_generation_waiters(self) -> None:
        """Wake gateway waiters after the owned batch completes."""
        with self._wait_condition:
            self._wait_condition.notify_all()

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
            self._wait_condition.notify_all()
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
