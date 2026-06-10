"""Circuit Breaker — prevents cascading failures when a provider is down.

State machine per provider:

    CLOSED ──(N failures)──► OPEN ──(reset_timeout)──► HALF_OPEN
      ▲                                                      │
      └──────────────(success)──────────────────────────────┘
      ▲                              │
      └──(failure in HALF_OPEN)──────┘ → back to OPEN

Usage::

    from bauer.circuit_breaker import CircuitBreaker, CircuitOpenError

    cb = CircuitBreaker(failure_threshold=5, reset_timeout=60.0)

    try:
        with cb.call("anthropic"):
            result = client.chat(...)
    except CircuitOpenError:
        # provider is open — skip to fallback immediately
        ...
    except Exception as exc:
        # real failure — circuit breaker recorded it
        ...

The ``@circuit_protected`` decorator wraps a callable and records
success/failure automatically::

    @circuit_protected("groq", cb)
    def call_groq(...):
        ...
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generator


# ---------------------------------------------------------------------------
# State enum
# ---------------------------------------------------------------------------


class CBState(Enum):
    CLOSED = "closed"        # normal — letting calls through
    OPEN = "open"            # tripped — blocking all calls immediately
    HALF_OPEN = "half_open"  # probing — letting one call through to test


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CircuitOpenError(Exception):
    """Raised when a call is blocked because the circuit is OPEN."""

    def __init__(self, provider: str, opens_at: float) -> None:
        super().__init__(
            f"Circuit for '{provider}' is OPEN — blocked until "
            f"{time.strftime('%H:%M:%S', time.localtime(opens_at))}"
        )
        self.provider = provider
        self.opens_at = opens_at


# ---------------------------------------------------------------------------
# Internal per-provider state
# ---------------------------------------------------------------------------


@dataclass
class _ProviderState:
    state: CBState = CBState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_opened_at: float = 0.0
    success_count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self) -> None:
        self.state = CBState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.success_count = 0

    def trip(self, now: float) -> None:
        self.state = CBState.OPEN
        self.last_opened_at = now

    def half_open(self) -> None:
        self.state = CBState.HALF_OPEN
        self.failure_count = 0


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Thread-safe circuit breaker for multiple named providers.

    Parameters
    ----------
    failure_threshold:
        Number of consecutive failures before opening the circuit.
    reset_timeout:
        Seconds to wait before transitioning OPEN → HALF_OPEN.
    success_threshold:
        Number of successes in HALF_OPEN required to close the circuit.
    excluded_exceptions:
        Exception types that should NOT count as failures
        (e.g. ``ValueError`` from bad user input).
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0,
        success_threshold: int = 1,
        excluded_exceptions: tuple[type[BaseException], ...] = (),
    ) -> None:
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._success_threshold = success_threshold
        self._excluded = excluded_exceptions
        self._states: dict[str, _ProviderState] = {}
        self._global_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def state(self, provider: str) -> CBState:
        """Return the current effective state, transitioning OPEN→HALF_OPEN if timed out."""
        ps = self._get(provider)
        with ps.lock:
            return self._evaluate_state(ps)

    def failure_count(self, provider: str) -> int:
        return self._get(provider).failure_count

    def is_open(self, provider: str) -> bool:
        """Return True if calls to *provider* should be blocked immediately."""
        ps = self._get(provider)
        with ps.lock:
            return self._evaluate_state(ps) == CBState.OPEN

    def record_success(self, provider: str) -> None:
        """Signal a successful call — may close the circuit."""
        ps = self._get(provider)
        with ps.lock:
            st = self._evaluate_state(ps)
            if st == CBState.HALF_OPEN:
                ps.success_count += 1
                if ps.success_count >= self._success_threshold:
                    ps.reset()
            elif st == CBState.CLOSED:
                ps.failure_count = 0  # consecutive reset

    def record_failure(self, provider: str, exc: BaseException | None = None) -> None:
        """Signal a failed call — may open the circuit."""
        if exc is not None and isinstance(exc, self._excluded):
            return
        ps = self._get(provider)
        with ps.lock:
            now = time.monotonic()
            ps.failure_count += 1
            ps.last_failure_time = now
            st = self._evaluate_state(ps)
            if st == CBState.HALF_OPEN:
                # Single failure in HALF_OPEN → back to OPEN
                ps.trip(now)
            elif st == CBState.CLOSED and ps.failure_count >= self._failure_threshold:
                ps.trip(now)

    def reset(self, provider: str) -> None:
        """Manually force the circuit CLOSED (e.g. after operator acknowledges)."""
        ps = self._get(provider)
        with ps.lock:
            ps.reset()

    def status_all(self) -> dict[str, dict[str, Any]]:
        """Return a dict of {provider: {state, failures, last_failure}} for all tracked providers."""
        result: dict[str, dict[str, Any]] = {}
        for name, ps in list(self._states.items()):
            with ps.lock:
                st = self._evaluate_state(ps)
                result[name] = {
                    "state": st.value,
                    "failure_count": ps.failure_count,
                    "last_failure_time": ps.last_failure_time,
                    "last_opened_at": ps.last_opened_at,
                }
        return result

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    @contextmanager
    def call(self, provider: str) -> Generator[None, None, None]:
        """Context manager that gates a block of code behind the circuit.

        Raises :exc:`CircuitOpenError` immediately if the circuit is OPEN.
        Records success/failure automatically.
        """
        ps = self._get(provider)
        with ps.lock:
            st = self._evaluate_state(ps)
            if st == CBState.OPEN:
                reopen_at = ps.last_opened_at + self._reset_timeout
                raise CircuitOpenError(provider, reopen_at)
            # HALF_OPEN: allow exactly one probe through (no lock held during call)

        try:
            yield
        except BaseException as exc:
            if not isinstance(exc, self._excluded):
                self.record_failure(provider, exc)
            raise
        else:
            self.record_success(provider)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get(self, provider: str) -> _ProviderState:
        if provider not in self._states:
            with self._global_lock:
                if provider not in self._states:
                    self._states[provider] = _ProviderState()
        return self._states[provider]

    def _evaluate_state(self, ps: _ProviderState) -> CBState:
        """Evaluate the current effective state, transitioning OPEN → HALF_OPEN if timed out.

        Must be called while holding ``ps.lock``.
        """
        if ps.state == CBState.OPEN:
            elapsed = time.monotonic() - ps.last_opened_at
            if elapsed >= self._reset_timeout:
                ps.half_open()
        return ps.state


# ---------------------------------------------------------------------------
# Module-level singleton (shared across the whole process)
# ---------------------------------------------------------------------------

#: Global circuit breaker with sensible defaults.
#: Import and use directly::
#:
#:     from bauer.circuit_breaker import global_cb
#:     with global_cb.call("anthropic"):
#:         ...
global_cb = CircuitBreaker(
    failure_threshold=5,
    reset_timeout=60.0,
    success_threshold=1,
    excluded_exceptions=(KeyboardInterrupt, SystemExit),
)


# ---------------------------------------------------------------------------
# Decorator helper
# ---------------------------------------------------------------------------


def circuit_protected(provider: str, cb: CircuitBreaker | None = None):
    """Decorator: wrap a function with circuit-breaker protection.

    Parameters
    ----------
    provider:
        Provider name to track (e.g. ``"anthropic"``).
    cb:
        CircuitBreaker instance.  Defaults to :data:`global_cb`.
    """
    _cb = cb or global_cb

    def decorator(fn):
        import functools

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with _cb.call(provider):
                return fn(*args, **kwargs)

        return wrapper

    return decorator
