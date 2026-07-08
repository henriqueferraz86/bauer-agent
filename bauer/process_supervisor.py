"""Process supervisor — tracks worker restart history with exponential backoff.

Used by :class:`bauer.daemon.BauerDaemon` to decide whether a crashed worker
should be restarted or the daemon should give up and escalate.

Design
------
Each :class:`ProcessSupervisor` instance is tied to *one* logical worker
slot.  When the worker raises an exception, the daemon calls
:meth:`record_failure`.  Before deciding whether to restart, it calls
:meth:`should_restart`.  If restarting, it awaits :meth:`wait_backoff` to
give the system time to recover.

Backoff formula: ``min(backoff_base * 2 ** (failures - 1) + jitter, backoff_cap)``

Example::

    sup = ProcessSupervisor(worker_id=1, max_restarts=5, backoff_base=10.0)
    while True:
        try:
            await do_work()
        except Exception as exc:
            if not sup.should_restart(exc):
                break           # give up
            await sup.wait_backoff()   # sleep, then re-enter loop
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class FailureRecord:
    exc_type: str
    exc_msg: str
    timestamp: float


@dataclass
class ProcessSupervisor:
    """Per-worker restart tracker with exponential backoff.

    Attributes
    ----------
    worker_id:
        Human-readable identifier for logging (e.g. ``1`` or ``"worker_1"``).
    max_restarts:
        Maximum number of consecutive restarts before giving up.  Default 5.
    backoff_base:
        Base wait seconds for the first restart.  Doubles each successive
        restart up to ``backoff_cap``.  Default 10.0 s.
    backoff_cap:
        Maximum wait seconds per backoff interval.  Default 300.0 s (5 min).
    reset_after_success_seconds:
        If the worker runs successfully for at least this many seconds before
        failing again, the restart counter is reset to 0.  Default 60.0 s.
    """

    worker_id: int | str = 0
    max_restarts: int = 5
    backoff_base: float = 10.0
    backoff_cap: float = 300.0
    reset_after_success_seconds: float = 60.0

    _consecutive_failures: int = field(default=0, repr=False)
    _total_failures: int = field(default=0, repr=False)
    _last_failure_time: float = field(default=0.0, repr=False)
    _last_success_start: float = field(default_factory=time.monotonic, repr=False)
    _failure_history: list[FailureRecord] = field(default_factory=list, repr=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_failure(self, exc: Exception) -> None:
        """Record that the worker just crashed with *exc*."""
        now = time.monotonic()

        # Reset counter if it ran long enough between failures.
        if (
            self._consecutive_failures > 0
            and (now - self._last_success_start) >= self.reset_after_success_seconds
        ):
            logger.info(
                "supervisor[%s]: worker was stable for %.0fs before this failure — "
                "resetting consecutive failure counter",
                self.worker_id,
                now - self._last_success_start,
            )
            self._consecutive_failures = 0

        self._consecutive_failures += 1
        self._total_failures += 1
        self._last_failure_time = now
        self._failure_history.append(
            FailureRecord(
                exc_type=type(exc).__name__,
                exc_msg=str(exc)[:200],
                timestamp=now,
            )
        )
        logger.warning(
            "supervisor[%s]: failure #%d (consecutive=%d/%d): %s: %s",
            self.worker_id,
            self._total_failures,
            self._consecutive_failures,
            self.max_restarts,
            type(exc).__name__,
            str(exc)[:120],
        )

    def should_restart(self, exc: Exception | None = None) -> bool:
        """Return True if the worker should be restarted.

        Call *after* :meth:`record_failure`.  Returns False once the
        consecutive failure count exceeds ``max_restarts``.
        """
        if exc is not None:
            self.record_failure(exc)
        ok = self._consecutive_failures <= self.max_restarts
        if not ok:
            logger.error(
                "supervisor[%s]: exceeded max_restarts=%d — stopping worker",
                self.worker_id,
                self.max_restarts,
            )
        return ok

    async def wait_backoff(self) -> None:
        """Sleep for the appropriate backoff interval (async)."""
        n = max(1, self._consecutive_failures)
        delay = min(self.backoff_base * (2 ** (n - 1)), self.backoff_cap)
        # Add ±10% jitter to spread restarts if multiple workers crash together.
        jitter = delay * 0.10 * (2 * random.random() - 1)
        actual = max(0.0, delay + jitter)
        logger.info(
            "supervisor[%s]: backing off for %.1fs (failure #%d)",
            self.worker_id,
            actual,
            self._consecutive_failures,
        )
        await asyncio.sleep(actual)

    def record_success_start(self) -> None:
        """Call when the worker successfully starts a new work cycle."""
        self._last_success_start = time.monotonic()

    def reset(self) -> None:
        """Reset all failure counters (e.g. after a deliberate restart)."""
        self._consecutive_failures = 0
        self._total_failures = 0
        self._last_failure_time = 0.0
        self._last_success_start = time.monotonic()
        self._failure_history.clear()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def total_failures(self) -> int:
        return self._total_failures

    @property
    def is_healthy(self) -> bool:
        return self._consecutive_failures < self.max_restarts

    def backoff_seconds_for(self, failure_n: int) -> float:
        """Return the theoretical backoff for failure number *failure_n* (1-based)."""
        return min(self.backoff_base * (2 ** (max(1, failure_n) - 1)), self.backoff_cap)

    def stats(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "consecutive_failures": self._consecutive_failures,
            "total_failures": self._total_failures,
            "max_restarts": self.max_restarts,
            "is_healthy": self.is_healthy,
            "last_failure": (
                self._failure_history[-1].exc_type if self._failure_history else None
            ),
        }
