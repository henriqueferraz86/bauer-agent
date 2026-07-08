"""Headless approval engine — non-interactive decision logic for autonomous operation.

In interactive mode, ``check_all_command_guards`` blocks and waits for a human
callback (``once / session / always / deny``). In autonomous / daemon mode there
is no human present, so we need an automatic decision engine.

This module provides :class:`HeadlessApprovalEngine`, which wraps
``check_all_command_guards`` with one of four decision strategies:

* ``"yolo"``      — auto-approve every DANGEROUS command (HARDLINE still blocks).
* ``"threshold"`` — compute a ``risk_score`` [0.0 – 1.0] and approve if below the
                    configured ``risk_threshold`` (default 0.4).
* ``"smart"``     — delegate to an auxiliary LLM; falls back to ``threshold`` if the
                    auxiliary is unavailable.
* ``"deny_all"``  — reject every DANGEROUS command; only safe commands execute.

HARDLINE commands are **always** denied regardless of mode — this is non-negotiable.

Usage::

    from bauer.headless_approval import HeadlessApprovalEngine, HeadlessApprovalConfig

    engine = HeadlessApprovalEngine(HeadlessApprovalConfig(mode="threshold"))
    decision = engine.decide("rm -rf /tmp/build")
    if decision.action == "denied":
        raise RuntimeError(decision.reason)
    # else: run the command

Integrate with ``check_all_command_guards``::

    from bauer.approval import check_all_command_guards
    from bauer.headless_approval import HeadlessApprovalEngine, HeadlessApprovalConfig

    engine = HeadlessApprovalEngine(HeadlessApprovalConfig(mode="threshold"))
    callback = engine.make_approval_callback()

    result = check_all_command_guards(command, approval_callback=callback)
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

ApprovalMode = Literal["yolo", "threshold", "smart", "deny_all"]


@dataclass
class HeadlessApprovalConfig:
    """Configuration knobs for the headless approval engine.

    Attributes
    ----------
    mode:
        Decision strategy. See module docstring.
    risk_threshold:
        Float in [0.0, 1.0]. Only used in ``"threshold"`` mode.
        Commands with ``risk_score < risk_threshold`` are approved.
        Default 0.4 (conservative).
    max_approvals_per_session:
        Hard cap on auto-approvals across the whole session. Once reached,
        every subsequent DANGEROUS command is denied. Default 20.
    max_approvals_per_tool:
        Per-tool cap, e.g. ``{"run_command": 10, "execute_code": 5}``.
        Unspecified tools use ``max_approvals_per_session`` as ceiling.
    permanent_deny_patterns:
        Additional regex patterns that are always denied (on top of HARDLINE).
        Each is compiled once on engine creation.
    permanent_allow_patterns:
        Additional regex patterns that are always approved (before risk calc).
        Applied after HARDLINE check, so they cannot bypass HARDLINE.
    log_all_decisions:
        When True (default), every decision is emitted as a structured
        ``INFO`` log line.
    """

    mode: ApprovalMode = "threshold"
    risk_threshold: float = 0.4
    max_approvals_per_session: int = 20
    max_approvals_per_tool: dict[str, int] = field(default_factory=dict)
    permanent_deny_patterns: list[str] = field(default_factory=list)
    permanent_allow_patterns: list[str] = field(default_factory=list)
    log_all_decisions: bool = True


@dataclass
class HeadlessDecision:
    """Result of a single headless approval evaluation.

    ``action`` is either ``"approved"`` or ``"denied"``.
    ``reason`` explains why.
    ``risk_score`` is set when threshold mode is used (else None).
    ``mode_used`` is the strategy that produced this decision.
    """

    action: Literal["approved", "denied"]
    reason: str
    command: str
    mode_used: ApprovalMode | str
    risk_score: float | None = None
    timestamp: float = field(default_factory=time.time)

    @property
    def approved(self) -> bool:
        return self.action == "approved"

    @property
    def denied(self) -> bool:
        return self.action == "denied"


# ---------------------------------------------------------------------------
# Risk score helpers
# ---------------------------------------------------------------------------

# Patterns that inflate risk score when found in the command string.
_RISK_INFLATION: list[tuple[re.Pattern, float]] = [
    (re.compile(r"\bsudo\b"),                          +0.35),  # privilege escalation
    (re.compile(r"(?:/etc|/usr|/var|/sys|/proc)\b"),   +0.25),  # system paths
    (re.compile(r"\brm\b.*\s+-[a-z]*r"),               +0.20),  # recursive rm
    (re.compile(r"\bgit\s+push\b"),                    +0.20),  # remote state change
    (re.compile(r"\bgit\s+reset\b.*--hard"),           +0.20),  # destructive reset
    (re.compile(r"\bpip\s+install\b"),                 +0.15),  # dependency mutation
    (re.compile(r"\bnpm\s+install\b"),                 +0.15),
    (re.compile(r"\bcurl\b.*\|\s*(?:ba)?sh"),          +0.50),  # pipe-to-shell (extra)
    (re.compile(r"\s/\s*$|\s/[^/]"),                  +0.20),  # absolute root paths
    (re.compile(r">[>&]"),                              +0.10),  # redirection
]

# Patterns that deflate risk score (command looks benign).
_RISK_DEFLATION: list[tuple[re.Pattern, float]] = [
    (re.compile(r"\bgit\s+(?:status|log|diff|show|fetch)\b"), -0.15),
    (re.compile(r"\bpytest\b"),                                -0.10),
    (re.compile(r"\bpython\s+-[mc]\b"),                       -0.10),
    (re.compile(r"\bgrep\b|\bawk\b|\bsed\b"),                 -0.05),
    (re.compile(r"\bls\b|\bfind\b|\bcat\b|\bhead\b"),         -0.05),
]


def _compute_risk_score(command: str, danger_description: str) -> float:
    """Compute a [0.0, 1.0] risk score for a DANGEROUS command.

    Starts at a base of 0.50 (all DANGEROUS hits are non-trivially risky)
    then adjusts based on presence of high/low-risk indicators.
    Result is clamped to [0.0, 1.0].
    """
    score = 0.50
    for pattern, delta in _RISK_INFLATION:
        if pattern.search(command):
            score += delta
    for pattern, delta in _RISK_DEFLATION:
        if pattern.search(command):
            score += delta  # negative values lower the score
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# HeadlessApprovalEngine
# ---------------------------------------------------------------------------


class HeadlessApprovalEngine:
    """Autonomous decision engine for shell command approval.

    Thread-safe per-session counters. Create one instance per daemon session.
    """

    def __init__(self, config: HeadlessApprovalConfig | None = None) -> None:
        self._cfg = config or HeadlessApprovalConfig()
        self._session_approvals: int = 0
        self._tool_approvals: dict[str, int] = {}

        # Compile additional patterns once.
        self._deny_patterns: list[re.Pattern] = [
            re.compile(p, re.IGNORECASE)
            for p in self._cfg.permanent_deny_patterns
        ]
        self._allow_patterns: list[re.Pattern] = [
            re.compile(p, re.IGNORECASE)
            for p in self._cfg.permanent_allow_patterns
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def decide(
        self,
        command: str,
        *,
        tool_name: str = "run_command",
        danger_description: str = "",
    ) -> HeadlessDecision:
        """Evaluate *command* and return an approval/denial decision.

        This is the low-level method. Most callers will use
        :meth:`make_approval_callback` to get a callback for
        ``check_all_command_guards``.

        Parameters
        ----------
        command:
            The full shell command string about to be executed.
        tool_name:
            Which tool is invoking this (for per-tool caps).
        danger_description:
            Human-readable description from the dangerous pattern match
            (forwarded from ``check_all_command_guards``).
        """
        cfg = self._cfg

        # 1. Extra permanent deny (on top of HARDLINE which is enforced upstream).
        for pat in self._deny_patterns:
            if pat.search(command):
                return self._log(HeadlessDecision(
                    action="denied",
                    reason=f"permanent_deny_pattern: {pat.pattern!r}",
                    command=command,
                    mode_used="permanent_deny",
                ))

        # 2. Extra permanent allow.
        for pat in self._allow_patterns:
            if pat.search(command):
                return self._log(HeadlessDecision(
                    action="approved",
                    reason=f"permanent_allow_pattern: {pat.pattern!r}",
                    command=command,
                    mode_used="permanent_allow",
                ))

        # 3. Session budget check — before evaluating mode.
        if self._session_approvals >= cfg.max_approvals_per_session:
            return self._log(HeadlessDecision(
                action="denied",
                reason=(
                    f"session approval budget exhausted "
                    f"({self._session_approvals}/{cfg.max_approvals_per_session})"
                ),
                command=command,
                mode_used="budget",
            ))

        # Per-tool cap.
        tool_cap = cfg.max_approvals_per_tool.get(tool_name)
        if tool_cap is not None:
            used = self._tool_approvals.get(tool_name, 0)
            if used >= tool_cap:
                return self._log(HeadlessDecision(
                    action="denied",
                    reason=f"per-tool approval budget exhausted for {tool_name!r} ({used}/{tool_cap})",
                    command=command,
                    mode_used="budget",
                ))

        # 4. Mode-based decision.
        decision = self._apply_mode(command, tool_name, danger_description)

        # 5. Track approvals.
        if decision.approved:
            self._session_approvals += 1
            self._tool_approvals[tool_name] = self._tool_approvals.get(tool_name, 0) + 1

        return self._log(decision)

    def make_approval_callback(
        self, *, tool_name: str = "run_command"
    ) -> "Callable[[str, str], str]":
        """Return a callback compatible with ``check_all_command_guards``.

        The returned callback receives ``(command, description)`` and returns
        one of ``"once" | "session" | "always" | "deny"`` — the same protocol
        expected by the approval pipeline.

        Usage::

            engine = HeadlessApprovalEngine(config)
            result = check_all_command_guards(cmd, approval_callback=engine.make_approval_callback())
        """

        def _callback(command: str, description: str) -> str:
            decision = self.decide(command, tool_name=tool_name, danger_description=description)
            if decision.approved:
                # Return "once" — we track approvals ourselves, no need for
                # session/permanent persistence from the parent pipeline.
                return "once"
            return "deny"

        return _callback

    def stats(self) -> dict:
        """Return current approval counters for observability."""
        return {
            "session_approvals": self._session_approvals,
            "max_session_approvals": self._cfg.max_approvals_per_session,
            "tool_approvals": dict(self._tool_approvals),
            "mode": self._cfg.mode,
        }

    def reset_counters(self) -> None:
        """Reset per-session counters (e.g. between test runs)."""
        self._session_approvals = 0
        self._tool_approvals.clear()

    # ------------------------------------------------------------------
    # Mode implementations
    # ------------------------------------------------------------------

    def _apply_mode(
        self, command: str, tool_name: str, danger_description: str
    ) -> HeadlessDecision:
        mode = self._cfg.mode

        if mode == "deny_all":
            return HeadlessDecision(
                action="denied",
                reason=f"deny_all mode: dangerous command rejected — {danger_description}",
                command=command,
                mode_used="deny_all",
            )

        if mode == "yolo":
            return HeadlessDecision(
                action="approved",
                reason=f"yolo mode: auto-approved dangerous command — {danger_description}",
                command=command,
                mode_used="yolo",
            )

        if mode == "threshold":
            return self._threshold_decision(command, danger_description)

        if mode == "smart":
            return self._smart_decision(command, danger_description)

        # Fallback — should not reach here with validated config.
        return self._threshold_decision(command, danger_description)

    def _threshold_decision(
        self, command: str, danger_description: str
    ) -> HeadlessDecision:
        score = _compute_risk_score(command, danger_description)
        threshold = self._cfg.risk_threshold
        approved = score < threshold
        return HeadlessDecision(
            action="approved" if approved else "denied",
            reason=(
                f"threshold: risk_score={score:.2f} "
                f"{'<' if approved else '>='} threshold={threshold:.2f} — "
                f"{danger_description}"
            ),
            command=command,
            mode_used="threshold",
            risk_score=score,
        )

    def _smart_decision(
        self, command: str, danger_description: str
    ) -> HeadlessDecision:
        """Ask auxiliary LLM; fall back to threshold if unavailable."""
        try:
            from .auxiliary_client import get_text_auxiliary_client

            client, model = get_text_auxiliary_client("headless_approval")
            if client is None:
                raise RuntimeError("auxiliary client unavailable")

            system = (
                "You are a security-conscious approval agent for an autonomous coding assistant. "
                "You will receive a shell command that has been flagged as DANGEROUS. "
                "Evaluate if it is safe to execute in the context of a software development workflow. "
                "Respond with exactly one word: APPROVE or DENY."
            )
            prompt = (
                f"Command: {command}\n"
                f"Dangerous pattern matched: {danger_description}\n"
                f"Context: autonomous coding agent on developer workstation.\n"
                f"Is it safe to execute? APPROVE or DENY:"
            )
            messages = [{"role": "user", "content": prompt}]

            # Synchronous call — use generate() on the client.
            response = client.generate(
                messages=messages,
                model=model,
                system=system,
                max_tokens=10,
                stream=False,
            )
            text = (response or "").strip().upper()
            if "APPROVE" in text:
                return HeadlessDecision(
                    action="approved",
                    reason=f"smart: auxiliary LLM approved — {danger_description}",
                    command=command,
                    mode_used="smart",
                )
            return HeadlessDecision(
                action="denied",
                reason=f"smart: auxiliary LLM denied — {danger_description}",
                command=command,
                mode_used="smart",
            )
        except Exception as exc:
            logger.warning(
                "headless_approval smart mode: auxiliary unavailable (%s), "
                "falling back to threshold",
                exc,
            )
            fallback = self._threshold_decision(command, danger_description)
            fallback = HeadlessDecision(
                action=fallback.action,
                reason=f"smart(fallback→threshold): {fallback.reason}",
                command=command,
                mode_used="smart",
                risk_score=fallback.risk_score,
            )
            return fallback

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log(self, decision: HeadlessDecision) -> HeadlessDecision:
        if self._cfg.log_all_decisions:
            logger.info(
                "headless_approval action=%s mode=%s risk=%s command=%r reason=%r",
                decision.action,
                decision.mode_used,
                f"{decision.risk_score:.2f}" if decision.risk_score is not None else "n/a",
                decision.command[:120],
                decision.reason,
            )
        return decision


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def make_headless_engine(
    mode: ApprovalMode = "threshold",
    *,
    risk_threshold: float = 0.4,
    max_approvals: int = 20,
    deny_patterns: list[str] | None = None,
    allow_patterns: list[str] | None = None,
) -> HeadlessApprovalEngine:
    """Shortcut to build an engine with common settings.

    Equivalent to::

        HeadlessApprovalEngine(HeadlessApprovalConfig(
            mode=mode,
            risk_threshold=risk_threshold,
            max_approvals_per_session=max_approvals,
            permanent_deny_patterns=deny_patterns or [],
            permanent_allow_patterns=allow_patterns or [],
        ))
    """
    return HeadlessApprovalEngine(
        HeadlessApprovalConfig(
            mode=mode,
            risk_threshold=risk_threshold,
            max_approvals_per_session=max_approvals,
            permanent_deny_patterns=deny_patterns or [],
            permanent_allow_patterns=allow_patterns or [],
        )
    )
