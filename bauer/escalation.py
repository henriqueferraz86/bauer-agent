"""Escalation engine — route agent alerts to humans and external systems.

When the autonomous agent encounters situations it cannot handle alone
(budget exhaustion, repeated failures, security anomalies, etc.) it
*escalates*.  This module provides:

* :class:`EscalationRule` — condition + severity + channel routing
* :class:`EscalationEngine` — deduplicates, applies cooldown, dispatches
* Built-in channels: ``log``, ``callback``, ``webhook``

Deduplication
-------------
The same ``(reason, context_hash)`` is not re-escalated within
``cooldown_seconds``.  This prevents thundering-herd alerts when e.g.
the budget is checked every 5 s and is always EXHAUSTED.

Usage::

    from bauer.escalation import EscalationEngine, EscalationRule, Severity

    engine = EscalationEngine()
    engine.add_rule(EscalationRule(
        name="budget_warn",
        reason_pattern="budget_exhausted",
        severity=Severity.CRITICAL,
        channels=["log", "callback"],
    ))

    async def my_callback(event):
        send_slack(event.message)

    engine.set_callback(my_callback)

    await engine.escalate("budget_exhausted", {"cost": 5.00})
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class EscalationEvent:
    """Emitted when an escalation fires."""

    reason: str
    severity: Severity
    context: dict[str, Any]
    message: str
    rule_name: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "severity": self.severity.value,
            "context": self.context,
            "message": self.message,
            "rule_name": self.rule_name,
            "timestamp": self.timestamp,
        }


EscalationCallback = Callable[[EscalationEvent], Awaitable[None]]


@dataclass
class EscalationRule:
    """One escalation rule.

    Attributes
    ----------
    name:
        Unique rule identifier.
    reason_pattern:
        Regex pattern matched against the escalation reason string.
        If it matches, this rule fires.  Use ``".*"`` to match all.
    severity:
        How serious this escalation is.
    channels:
        Where to send the alert.  Possible values: ``"log"``,
        ``"callback"``, ``"webhook"``.
    cooldown_seconds:
        Minimum seconds between repeated firings for the same
        ``(reason, context_hash)``.  Default 60.
    enabled:
        If False, the rule is ignored.
    message_template:
        Optional template string.  Supports ``{reason}`` and
        ``{severity}`` substitutions.  Default: auto-generated.
    """

    name: str
    reason_pattern: str = ".*"
    severity: Severity = Severity.WARNING
    channels: list[str] = field(default_factory=lambda: ["log"])
    cooldown_seconds: float = 60.0
    enabled: bool = True
    message_template: str = ""

    def matches(self, reason: str) -> bool:
        if not self.enabled:
            return False
        try:
            return bool(re.fullmatch(self.reason_pattern, reason))
        except re.error:
            return reason == self.reason_pattern

    def format_message(self, reason: str, context: dict[str, Any]) -> str:
        if self.message_template:
            try:
                return self.message_template.format(
                    reason=reason, severity=self.severity.value, **context
                )
            except (KeyError, ValueError):
                pass
        return f"[{self.severity.value.upper()}] {reason}"


# ---------------------------------------------------------------------------
# EscalationEngine
# ---------------------------------------------------------------------------


class EscalationEngine:
    """Manage escalation rules and dispatch events.

    Parameters
    ----------
    callback:
        Async callable called for every matched escalation (regardless
        of channel; channels control *additional* routing).
    webhook_url:
        Optional URL for the ``"webhook"`` channel.  If None and webhook
        is in channels, the webhook step is skipped.
    default_cooldown_seconds:
        Default cooldown applied when a rule doesn't specify one.
    """

    def __init__(
        self,
        callback: EscalationCallback | None = None,
        *,
        webhook_url: str | None = None,
        default_cooldown_seconds: float = 60.0,
    ) -> None:
        self._rules: list[EscalationRule] = []
        self._callback = callback
        self._webhook_url = webhook_url
        self._default_cooldown = default_cooldown_seconds

        # Cooldown tracking: (rule_name, context_hash) → last_fired_time
        self._last_fired: dict[str, float] = {}

        # History
        self._history: list[EscalationEvent] = []

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def add_rule(self, rule: EscalationRule) -> None:
        """Register an escalation rule."""
        self._rules.append(rule)

    def remove_rule(self, name: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.name != name]
        return len(self._rules) < before

    def set_callback(self, callback: EscalationCallback) -> None:
        """Set or replace the async callback."""
        self._callback = callback

    def set_webhook_url(self, url: str) -> None:
        self._webhook_url = url

    def list_rules(self) -> list[EscalationRule]:
        return list(self._rules)

    # ------------------------------------------------------------------
    # Escalation
    # ------------------------------------------------------------------

    async def escalate(
        self,
        reason: str,
        context: dict[str, Any] | None = None,
    ) -> list[EscalationEvent]:
        """Evaluate all rules against *reason* and dispatch matching ones.

        Returns the list of events that were actually fired (after
        cooldown deduplication).
        """
        ctx = context or {}
        fired: list[EscalationEvent] = []

        for rule in self._rules:
            if not rule.matches(reason):
                continue

            cooldown = rule.cooldown_seconds if rule.cooldown_seconds >= 0 else self._default_cooldown
            key = self._dedup_key(rule.name, reason, ctx)
            now = time.time()

            if key in self._last_fired:
                elapsed = now - self._last_fired[key]
                if elapsed < cooldown:
                    logger.debug(
                        "escalation[%s] suppressed by cooldown (%.0fs remaining)",
                        rule.name, cooldown - elapsed,
                    )
                    continue

            self._last_fired[key] = now
            event = EscalationEvent(
                reason=reason,
                severity=rule.severity,
                context=ctx,
                message=rule.format_message(reason, ctx),
                rule_name=rule.name,
            )
            self._history.append(event)
            fired.append(event)

            await self._dispatch(event, rule.channels)

        return fired

    # ------------------------------------------------------------------
    # History / stats
    # ------------------------------------------------------------------

    def history(self, *, limit: int = 50) -> list[EscalationEvent]:
        """Return recent events (most recent last)."""
        return self._history[-limit:]

    def count(self, *, severity: Severity | None = None) -> int:
        if severity is None:
            return len(self._history)
        return sum(1 for e in self._history if e.severity == severity)

    def reset_cooldowns(self) -> None:
        """Clear all cooldown state (useful in tests)."""
        self._last_fired.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "rules": len(self._rules),
            "total_fired": len(self._history),
            "cooldown_entries": len(self._last_fired),
            "by_severity": {
                s.value: self.count(severity=s) for s in Severity
            },
        }

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    async def _dispatch(self, event: EscalationEvent, channels: list[str]) -> None:
        for channel in channels:
            if channel == "log":
                await self._dispatch_log(event)
            elif channel == "callback":
                await self._dispatch_callback(event)
            elif channel == "webhook":
                await self._dispatch_webhook(event)
            else:
                logger.warning("unknown escalation channel: %s", channel)

        # Always call callback if set (regardless of channel list)
        if "callback" not in channels and self._callback:
            await self._dispatch_callback(event)

    async def _dispatch_log(self, event: EscalationEvent) -> None:
        log_fn = {
            Severity.INFO: logger.info,
            Severity.WARNING: logger.warning,
            Severity.ERROR: logger.error,
            Severity.CRITICAL: logger.critical,
        }.get(event.severity, logger.warning)
        log_fn("ESCALATION[%s] %s", event.rule_name, event.message)

    async def _dispatch_callback(self, event: EscalationEvent) -> None:
        if self._callback is None:
            return
        try:
            await self._callback(event)
        except Exception as exc:
            logger.error("escalation callback raised: %s", exc)

    async def _dispatch_webhook(self, event: EscalationEvent) -> None:
        if not self._webhook_url:
            logger.debug("escalation webhook: no URL configured — skipping")
            return
        try:
            import urllib.request
            import urllib.error

            payload = json.dumps(event.to_dict()).encode()
            req = urllib.request.Request(
                self._webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=5))
            logger.debug("escalation webhook sent to %s", self._webhook_url)
        except Exception as exc:
            logger.error("escalation webhook failed: %s", exc)

    # ------------------------------------------------------------------
    # Dedup key
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup_key(rule_name: str, reason: str, context: dict[str, Any]) -> str:
        ctx_hash = hashlib.md5(
            json.dumps(context, sort_keys=True, default=str).encode()
        ).hexdigest()[:8]
        return f"{rule_name}:{reason}:{ctx_hash}"


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_default_escalation_engine(
    callback: EscalationCallback | None = None,
) -> EscalationEngine:
    """Create an engine with sensible default rules for the daemon."""
    engine = EscalationEngine(callback=callback)

    engine.add_rule(EscalationRule(
        name="budget_exhausted",
        reason_pattern="budget_exhausted",
        severity=Severity.CRITICAL,
        channels=["log", "callback"],
        cooldown_seconds=300.0,
    ))
    engine.add_rule(EscalationRule(
        name="worker_dead",
        reason_pattern=r"worker_\d+_exceeded_restarts",
        severity=Severity.ERROR,
        channels=["log", "callback"],
        cooldown_seconds=60.0,
    ))
    engine.add_rule(EscalationRule(
        name="critical_diagnostics",
        reason_pattern="critical_diagnostics",
        severity=Severity.ERROR,
        channels=["log", "callback"],
        cooldown_seconds=120.0,
    ))
    engine.add_rule(EscalationRule(
        name="catch_all",
        reason_pattern=".*",
        severity=Severity.WARNING,
        channels=["log"],
        cooldown_seconds=30.0,
    ))

    return engine
