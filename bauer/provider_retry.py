"""Bounded request retries with interruptible cooldown, never turn replay."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Literal

import httpx

from .error_classifier import FailReason, classify_api_error
from .ollama_client import OllamaError
from .openai_client import OpenAIClientError

MAX_CONSECUTIVE_RETRIES = 2
MAX_RETRY_DELAY_S = 20.0
POLL_INTERVAL_S = 0.2
RetryStopReason = Literal["cancelled", "kill_switch", "budget_exhausted"]
_RETRY_REASONS = {FailReason.PROVIDER_DOWN, FailReason.RATE_LIMIT, FailReason.SERVER_ERROR, FailReason.TIMEOUT}
_PROVIDER_ERRORS = (OpenAIClientError, OllamaError, httpx.HTTPError, TimeoutError, ConnectionError)


class ProviderRetryInterrupted(RuntimeError):
    """A request cooldown stopped without another provider call."""

    def __init__(self, reason: RetryStopReason = "cancelled"):
        if reason not in {"cancelled", "kill_switch", "budget_exhausted"}:
            raise ValueError("invalid provider retry interruption reason")
        self.reason = reason
        super().__init__(f"Provider retry interrupted: {reason}")


def is_retryable_provider_error(error: Exception) -> bool:
    if not isinstance(error, _PROVIDER_ERRORS):
        return False
    classified = classify_api_error(error)
    return classified.retryable and classified.reason in _RETRY_REASONS


def retry_delay_s(attempt: int) -> float:
    """Five/ten seconds plus a small jitter, capped for every caller."""
    base = 5.0 * (2 ** min(3, max(0, attempt - 1)))
    return min(MAX_RETRY_DELAY_S, base + random.uniform(0.0, 1.0))


def check_provider_retry_interrupt(
    *, interrupt_check: Callable[[], str | None] | None = None,
    budget_exhausted: Callable[[], bool] | None = None,
) -> None:
    reason = None
    if interrupt_check is not None:
        # This callback enforces stop, kill-switch and live cost limits. Its
        # failure must stop execution rather than silently grant another call.
        reason = interrupt_check()
    if reason:
        if reason == "kill_switch":
            raise ProviderRetryInterrupted("kill_switch")
        if reason == "budget_exhausted":
            raise ProviderRetryInterrupted("budget_exhausted")
        raise ProviderRetryInterrupted("cancelled")
    if budget_exhausted is not None and budget_exhausted():
        raise ProviderRetryInterrupted("budget_exhausted")


def wait_provider_retry(
    delay_s: float, *, interrupt_check: Callable[[], str | None] | None = None,
    budget_exhausted: Callable[[], bool] | None = None,
) -> None:
    deadline = time.monotonic() + max(0.0, min(MAX_RETRY_DELAY_S, delay_s))
    while True:
        check_provider_retry_interrupt(interrupt_check=interrupt_check, budget_exhausted=budget_exhausted)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(POLL_INTERVAL_S, remaining))
