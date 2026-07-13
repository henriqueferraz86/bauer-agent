"""Autonomous operation budget — hard limits on cost, time, and compute.

When Bauer operates autonomously (daemon mode, goal execution) it must respect
hard spending limits that cannot be bypassed by the agent itself.  This module
tracks cumulative consumption and signals ``EXHAUSTED`` when any limit is hit.

All limits are *hard* — once ``is_exhausted`` is True the agent **must** stop.
Soft warnings at ``warn_at_*_pct`` give the agent (or an operator watching logs)
a chance to react gracefully before the hard cut-off.

Usage::

    from bauer.autonomous_budget import AutonomousBudget, BudgetStatus

    budget = AutonomousBudget(max_cost_usd=5.0, max_wall_seconds=3600)

    # After each LLM call:
    status = budget.consume_llm_call(cost_usd=0.023, output_tokens=420)
    if status == BudgetStatus.EXHAUSTED:
        raise BudgetExhaustedError(budget.summary())

    # After each tool call:
    status = budget.consume_tool_call()

Thread safety
-------------
The instance is **not** thread-safe by default.  In the daemon's asyncio event
loop all consumption happens on a single thread, so no lock is needed.  If you
call from multiple threads, wrap accesses in a ``threading.Lock``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class BudgetStatus(Enum):
    """Return code from :meth:`AutonomousBudget.consume_llm_call` and friends."""

    OK = "ok"
    """Within all limits."""

    WARNING = "warning"
    """At least one soft threshold crossed (e.g. ≥80% of some limit)."""

    EXHAUSTED = "exhausted"
    """At least one hard limit reached — the agent must stop."""


class BudgetExhaustedError(RuntimeError):
    """Raised when the caller tries to consume a budget that is already exhausted."""


@dataclass
class BudgetSnapshot:
    """Immutable snapshot of the current budget state."""

    cost_usd: float
    max_cost_usd: float

    elapsed_seconds: float
    max_wall_seconds: int

    llm_calls: int
    max_llm_calls: int

    tool_calls: int
    max_tool_calls: int

    output_tokens: int
    max_output_tokens: int

    status: BudgetStatus

    @property
    def cost_pct(self) -> float:
        return (self.cost_usd / self.max_cost_usd * 100) if self.max_cost_usd > 0 else 0.0

    @property
    def time_pct(self) -> float:
        return (self.elapsed_seconds / self.max_wall_seconds * 100) if self.max_wall_seconds > 0 else 0.0

    def __str__(self) -> str:
        return (
            f"Budget [{self.status.value.upper()}] "
            f"cost=${self.cost_usd:.4f}/{self.max_cost_usd:.2f} "
            f"({self.cost_pct:.0f}%) | "
            f"time={self.elapsed_seconds:.0f}s/{self.max_wall_seconds}s | "
            f"llm={self.llm_calls}/{self.max_llm_calls} | "
            f"tools={self.tool_calls}/{self.max_tool_calls} | "
            f"tokens={self.output_tokens}/{self.max_output_tokens}"
        )


# ---------------------------------------------------------------------------
# AutonomousBudget
# ---------------------------------------------------------------------------


@dataclass
class AutonomousBudget:
    """Tracks cumulative resource consumption against hard limits.

    Attributes
    ----------
    max_cost_usd:
        Maximum total LLM cost in US dollars. Default $5.00.
    max_wall_seconds:
        Maximum wall-clock time in seconds since the budget was created (or
        last reset). Default 3600 (1 hour).
    max_llm_calls:
        Maximum number of LLM API calls (each ``chat`` / ``stream``).
        Default 200.
    max_tool_calls:
        Maximum number of tool executions. Default 500.
    max_output_tokens:
        Maximum total output (completion) tokens generated. Default 500_000.
    warn_at_cost_pct:
        Soft-warning threshold as a fraction of ``max_cost_usd`` [0, 1].
        Default 0.80 (80%).
    warn_at_time_pct:
        Soft-warning threshold as a fraction of ``max_wall_seconds``.
        Default 0.80.
    """

    max_cost_usd: float = 5.0
    max_wall_seconds: int = 3600
    max_llm_calls: int = 200
    max_tool_calls: int = 500
    max_output_tokens: int = 500_000

    warn_at_cost_pct: float = 0.80
    warn_at_time_pct: float = 0.80

    # Internal counters — not meant to be set directly (use consume_* methods).
    _cost_usd: float = field(default=0.0, repr=False)
    _start_time: float = field(default_factory=time.monotonic, repr=False)
    _llm_calls: int = field(default=0, repr=False)
    _tool_calls: int = field(default=0, repr=False)
    _output_tokens: int = field(default=0, repr=False)

    # ------------------------------------------------------------------
    # Consumption
    # ------------------------------------------------------------------

    def consume_llm_call(
        self,
        cost_usd: float = 0.0,
        output_tokens: int = 0,
    ) -> BudgetStatus:
        """Record one LLM API call and return the new :class:`BudgetStatus`.

        Should be called *after* each successful LLM response is received.

        Parameters
        ----------
        cost_usd:
            Incremental cost for this call in USD (from ``account_usage.py``).
            Pass 0.0 if cost tracking is unavailable.
        output_tokens:
            Number of completion tokens returned by the provider.
        """
        if self.is_exhausted:
            raise BudgetExhaustedError(
                f"Budget already exhausted — cannot consume more. {self.snapshot()}"
            )
        self._cost_usd += max(0.0, cost_usd)
        self._output_tokens += max(0, output_tokens)
        self._llm_calls += 1
        return self._compute_status()

    def consume_tool_call(self) -> BudgetStatus:
        """Record one tool execution and return the new :class:`BudgetStatus`."""
        if self.is_exhausted:
            raise BudgetExhaustedError(
                f"Budget already exhausted. {self.snapshot()}"
            )
        self._tool_calls += 1
        return self._compute_status()

    def consume_cost(self, cost_usd: float) -> BudgetStatus:
        """Record an incremental cost without a full LLM call (e.g., embeddings)."""
        if self.is_exhausted:
            raise BudgetExhaustedError(
                f"Budget already exhausted. {self.snapshot()}"
            )
        self._cost_usd += max(0.0, cost_usd)
        return self._compute_status()

    # ------------------------------------------------------------------
    # Status / introspection
    # ------------------------------------------------------------------

    @property
    def elapsed_seconds(self) -> float:
        """Wall-clock seconds since this budget was created or last reset."""
        return time.monotonic() - self._start_time

    @property
    def is_exhausted(self) -> bool:
        """True if any hard limit has been reached."""
        return self._compute_status() == BudgetStatus.EXHAUSTED

    @property
    def is_warning(self) -> bool:
        """True if any soft threshold has been crossed but no hard limit yet."""
        return self._compute_status() == BudgetStatus.WARNING

    def exhausted_dimension(self) -> "str | None":
        """Frase PT dizendo QUAL limite estourou (ou None se nenhum) — para a UI
        dizer exatamente o que aumentar. Segue a mesma ordem de _compute_status."""
        if self._cost_usd >= self.max_cost_usd:
            return f"custo estimado (US$ {self.max_cost_usd:.2f})"
        if self.elapsed_seconds >= self.max_wall_seconds:
            return f"tempo ({int(self.max_wall_seconds // 60)} min)"
        if self._llm_calls >= self.max_llm_calls:
            return f"chamadas ao modelo ({self.max_llm_calls})"
        if self._tool_calls >= self.max_tool_calls:
            return f"ferramentas ({self.max_tool_calls})"
        if self._output_tokens >= self.max_output_tokens:
            return f"tokens de saída ({self.max_output_tokens})"
        return None

    def remaining_cost_usd(self) -> float:
        return max(0.0, self.max_cost_usd - self._cost_usd)

    def remaining_seconds(self) -> float:
        return max(0.0, self.max_wall_seconds - self.elapsed_seconds)

    def remaining_llm_calls(self) -> int:
        return max(0, self.max_llm_calls - self._llm_calls)

    def remaining_tool_calls(self) -> int:
        return max(0, self.max_tool_calls - self._tool_calls)

    def snapshot(self) -> BudgetSnapshot:
        """Return an immutable snapshot of the current state."""
        return BudgetSnapshot(
            cost_usd=self._cost_usd,
            max_cost_usd=self.max_cost_usd,
            elapsed_seconds=self.elapsed_seconds,
            max_wall_seconds=self.max_wall_seconds,
            llm_calls=self._llm_calls,
            max_llm_calls=self.max_llm_calls,
            tool_calls=self._tool_calls,
            max_tool_calls=self.max_tool_calls,
            output_tokens=self._output_tokens,
            max_output_tokens=self.max_output_tokens,
            status=self._compute_status(),
        )

    def summary(self) -> str:
        """Return a one-line human-readable status summary."""
        return str(self.snapshot())

    def to_dict(self) -> dict[str, Any]:
        """Serialisable dict for persistence / logging."""
        snap = self.snapshot()
        return {
            "status": snap.status.value,
            "cost_usd": snap.cost_usd,
            "max_cost_usd": snap.max_cost_usd,
            "elapsed_seconds": round(snap.elapsed_seconds, 1),
            "max_wall_seconds": snap.max_wall_seconds,
            "llm_calls": snap.llm_calls,
            "max_llm_calls": snap.max_llm_calls,
            "tool_calls": snap.tool_calls,
            "max_tool_calls": snap.max_tool_calls,
            "output_tokens": snap.output_tokens,
            "max_output_tokens": snap.max_output_tokens,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset all counters and restart the wall-clock timer."""
        self._cost_usd = 0.0
        self._start_time = time.monotonic()
        self._llm_calls = 0
        self._tool_calls = 0
        self._output_tokens = 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compute_status(self) -> BudgetStatus:
        # Hard limits — any one exhausted → EXHAUSTED.
        if self._cost_usd >= self.max_cost_usd:
            return BudgetStatus.EXHAUSTED
        if self.elapsed_seconds >= self.max_wall_seconds:
            return BudgetStatus.EXHAUSTED
        if self._llm_calls >= self.max_llm_calls:
            return BudgetStatus.EXHAUSTED
        if self._tool_calls >= self.max_tool_calls:
            return BudgetStatus.EXHAUSTED
        if self._output_tokens >= self.max_output_tokens:
            return BudgetStatus.EXHAUSTED

        # Soft warnings — any one close → WARNING.
        if self.max_cost_usd > 0 and self._cost_usd / self.max_cost_usd >= self.warn_at_cost_pct:
            return BudgetStatus.WARNING
        if self.max_wall_seconds > 0 and self.elapsed_seconds / self.max_wall_seconds >= self.warn_at_time_pct:
            return BudgetStatus.WARNING

        return BudgetStatus.OK


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_budget(
    *,
    cost_usd: float = 5.0,
    wall_seconds: int = 3600,
    llm_calls: int = 200,
    tool_calls: int = 500,
    output_tokens: int = 500_000,
    warn_pct: float = 0.80,
) -> AutonomousBudget:
    """Convenience factory with named parameters."""
    return AutonomousBudget(
        max_cost_usd=cost_usd,
        max_wall_seconds=wall_seconds,
        max_llm_calls=llm_calls,
        max_tool_calls=tool_calls,
        max_output_tokens=output_tokens,
        warn_at_cost_pct=warn_pct,
        warn_at_time_pct=warn_pct,
    )
