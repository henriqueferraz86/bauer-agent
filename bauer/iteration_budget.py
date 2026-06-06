"""Iteration budget for agent loops — thread-safe counter with refund.

Inspired by Hermes Agent's `agent/iteration_budget.py`. The budget caps how many
times the agent can call the LLM within a single turn. Each LLM call consumes 1
from the budget; tool calls that are pure RPC (e.g. `execute_code`, file I/O)
should refund their consumption so they don't starve the agent.

Subagents (delegate_task) receive their own independent budget, capped by
`delegation.max_iterations` (separate from the parent's budget).

Usage::

    budget = IterationBudget(max_total=30)

    while budget.remaining > 0:
        budget.consume()
        response = call_llm(...)
        if response.tool_calls:
            for tc in response.tool_calls:
                result = router.execute(tc)
                if tc["function"]["name"] == "execute_code":
                    budget.refund()  # RPC tool — doesn't count toward budget
        else:
            break  # final text response

    # When budget is exhausted, force-finish:
    if budget.remaining == 0:
        # Last call without tools — let the model summarise what it found
        ...
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass
class IterationBudget:
    """Thread-safe consumable iteration counter with refund support.

    Attributes:
        max_total: hard cap on total iterations (consume/refund respect this).
        consumed: running total of consumed iterations (never exceeds max_total).

    All mutations go through `_lock` so multiple threads (e.g. parallel tool
    execution from `run_one_turn`) can safely share a budget.
    """

    max_total: int
    consumed: int = 0

    def __post_init__(self) -> None:
        if self.max_total < 0:
            raise ValueError(f"max_total must be >= 0, got {self.max_total}")
        if self.consumed < 0:
            raise ValueError(f"consumed must be >= 0, got {self.consumed}")
        # threading.Lock is not picklable / dataclass-friendly, so init in __post_init__
        self._lock = threading.Lock()

    @property
    def remaining(self) -> int:
        """Iterations left before budget exhaustion (never negative)."""
        with self._lock:
            return max(0, self.max_total - self.consumed)

    @property
    def exhausted(self) -> bool:
        """True when no iterations remain."""
        return self.remaining == 0

    def consume(self, n: int = 1) -> int:
        """Consume `n` iterations. Returns iterations actually consumed.

        Caps at `max_total` — never goes negative. Useful when caller wants to
        know whether the consumption was honoured fully or clamped.

            consumed = budget.consume()
            if consumed == 0:
                # Budget was already exhausted before this call
                ...
        """
        if n < 0:
            raise ValueError(f"n must be >= 0, got {n}")
        with self._lock:
            allowed = min(n, self.max_total - self.consumed)
            if allowed > 0:
                self.consumed += allowed
            return max(0, allowed)

    def refund(self, n: int = 1) -> int:
        """Refund `n` iterations (after RPC-style tool calls). Returns refunded.

        Cannot refund more than was consumed. Useful when an iteration didn't
        actually produce model output (e.g. tool result substitutes for a
        model call).
        """
        if n < 0:
            raise ValueError(f"n must be >= 0, got {n}")
        with self._lock:
            refunded = min(n, self.consumed)
            self.consumed -= refunded
            return refunded

    def reset(self) -> None:
        """Reset to zero consumption. For test isolation."""
        with self._lock:
            self.consumed = 0

    def __repr__(self) -> str:
        return f"IterationBudget(consumed={self.consumed}/{self.max_total}, remaining={self.remaining})"
