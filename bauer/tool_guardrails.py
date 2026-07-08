"""Tool-call guardrails — prevent failure loops and no-progress oscillation.

Different from `agent._detect_loop` (which counts *consecutive* identical
calls): this module's `ToolCallGuardrailController` tracks **cumulative**
failures and **per-signature** read-only results across the lifetime of a
single agent turn (or session, depending on caller scope).

Four anomaly patterns are detected:

1. **Exact failure loop** — the same tool with the same args has failed N
   times. Warn at 2, block at 5 (defaults).
2. **Same-tool failure** — any form of a tool has failed M times. Warn at
   3, block at 8.
3. **Idempotent no-progress** — a read-only tool keeps returning the same
   result for the same args. Warn at 2, block at 5.
4. **Hard stop** — aggregate failure count across all tools (configurable
   ceiling) — halts the turn outright when crossed.

Caller pattern::

    from bauer.tool_guardrails import (
        GuardrailConfig, ToolCallGuardrailController,
    )

    guardrails = ToolCallGuardrailController(GuardrailConfig())

    decision = guardrails.before_call(name, args)
    if decision.should_halt:
        ctx.add_user(decision.message)
        break

    try:
        result = router.execute({"action": name, "args": args})
        failed = False
    except (ToolError, SandboxError) as exc:
        result = f"[Erro: {exc}]"
        failed = True

    after = guardrails.after_call(name, args, result, failed)
    if after.action == "warn":
        ctx.add_user(after.message)
    if after.should_halt:
        ctx.add_user(after.message)
        break

The controller is stateful per instance. Build a new one per turn (or per
session) so previous turns' counts don't poison the current one.

Inspired by Hermes Agent's `agent/tool_guardrails.py`.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


# Built-in read-only tool names. These are checked for no-progress (same
# args + same result repeatedly = no information gain). Callers can override
# via GuardrailConfig.readonly_tools.
_DEFAULT_READONLY_TOOLS: frozenset[str] = frozenset({
    "read_file", "list_dir", "glob_files", "regex_search", "search_text",
    "web_search", "web_fetch", "http_request", "session_search",
    "memory", "todo", "calculate", "datetime_now", "json_query",
    "skills_list", "skill_view",
})


@dataclass(frozen=True)
class ToolCallSignature:
    """Fingerprint of a tool call: tool name + hash of canonicalised args.

    Two calls with the same `tool_name` and semantically-equivalent `args`
    produce the same signature. Hash is truncated to 16 hex chars (64 bits)
    — collision probability across a single turn is negligible.
    """
    tool_name: str
    args_hash: str


@dataclass(frozen=True)
class ToolCallGuardrailDecision:
    """Result of a guardrail check.

    Inspect `.action` for the imperative; `.allows_execution` and
    `.should_halt` are convenience properties so callers don't pattern-match
    on the string.

    Action values:
        "allow" — nothing to surface; proceed silently
        "warn"  — surface `message` to the user / model but proceed
        "block" — refuse this specific call; user may retry differently
        "halt"  — terminate the whole turn immediately

    `code` is machine-readable (e.g. "repeated_exact_failure_warning") so
    callers can route on it without scraping `message` text.
    """
    action: str
    code: str
    message: str
    tool_name: str
    count: int
    signature: ToolCallSignature

    @property
    def allows_execution(self) -> bool:
        """True if the tool should still be executed (allow + warn)."""
        return self.action in {"allow", "warn"}

    @property
    def should_halt(self) -> bool:
        """True if the turn should terminate (block + halt)."""
        return self.action in {"block", "halt"}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class GuardrailConfig:
    """Tunable thresholds for the controller. Defaults match Hermes.

    Each pattern has a separate warn / block threshold so callers can
    surface a soft hint before refusing outright. Setting warn > block is a
    no-op (the block fires first).

    `readonly_tools`: tools whose results are compared for no-progress. If a
    read-only tool returns the SAME bytes for the SAME args N+ times, the
    no-progress check fires. Tools that produce side effects (write_file,
    execute_code, run_command) should never be in this set.

    `hard_stop_total_failures`: aggregate ceiling across ALL tools.
    """
    exact_failure_warn_threshold: int = 2
    exact_failure_block_threshold: int = 5
    same_tool_warn_threshold: int = 3
    same_tool_block_threshold: int = 8
    idempotent_warn_threshold: int = 2
    idempotent_block_threshold: int = 5
    hard_stop_total_failures: int = 12
    warnings_enabled: bool = True
    hard_stop_enabled: bool = True
    readonly_tools: frozenset[str] = field(
        default_factory=lambda: _DEFAULT_READONLY_TOOLS
    )

    def __post_init__(self) -> None:
        # Negative thresholds are nonsense — silently raise to 1. Zero is
        # allowed (means "block on first occurrence").
        for attr in (
            "exact_failure_warn_threshold", "exact_failure_block_threshold",
            "same_tool_warn_threshold",     "same_tool_block_threshold",
            "idempotent_warn_threshold",    "idempotent_block_threshold",
            "hard_stop_total_failures",
        ):
            v = getattr(self, attr)
            if v < 0:
                object.__setattr__(self, attr, 0)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class ToolCallGuardrailController:
    """Stateful guardrail tracker. Build once per turn (or session).

    Not thread-safe — caller serialises tool calls. Mutating methods are
    `before_call`, `after_call`, and `reset`.
    """

    def __init__(self, config: GuardrailConfig | None = None):
        self.config = config or GuardrailConfig()
        # Per-signature failure counts (same tool + same args).
        self._failed_signatures: Counter[ToolCallSignature] = Counter()
        # Per-tool-name failure counts (any args).
        self._failed_names: Counter[str] = Counter()
        # Per-signature read-only result counts: {sig: {result_hash: count}}.
        # Tracks how often the SAME signature returned the SAME bytes.
        self._readonly_results: dict[ToolCallSignature, Counter[str]] = {}
        # Aggregate failure count across all tools (drives hard_stop).
        self._total_failures: int = 0

    def reset(self) -> None:
        """Zero every counter — for tests or new-session boundaries."""
        self._failed_signatures.clear()
        self._failed_names.clear()
        self._readonly_results.clear()
        self._total_failures = 0

    # ----- Pre-call check ------------------------------------------------

    def before_call(
        self,
        tool_name: str,
        args: Mapping[str, Any],
    ) -> ToolCallGuardrailDecision:
        """Decide whether to allow a call BEFORE running it.

        Returns 'block' (specific call refused) or 'halt' (whole turn) when
        cumulative failures cross block thresholds. Returns 'allow' otherwise
        — warnings are emitted from `after_call`, not here, since they
        depend on the result of this very call.
        """
        sig = self._signature(tool_name, args)

        # Hard stop dominates everything else.
        if (
            self.config.hard_stop_enabled
            and self._total_failures >= self.config.hard_stop_total_failures
        ):
            return ToolCallGuardrailDecision(
                action="halt",
                code="hard_stop_total_failures",
                message=(
                    f"[SYSTEM HALT] {self._total_failures} tool failures so "
                    f"far this turn (limit: {self.config.hard_stop_total_failures}). "
                    f"Stopping to avoid further damage. Re-evaluate strategy."
                ),
                tool_name=tool_name,
                count=self._total_failures,
                signature=sig,
            )

        exact_count = self._failed_signatures[sig]
        if exact_count >= self.config.exact_failure_block_threshold:
            return ToolCallGuardrailDecision(
                action="block",
                code="repeated_exact_failure_block",
                message=(
                    f"[BLOCK] Tool '{tool_name}' has already failed "
                    f"{exact_count} times with these exact args. Try "
                    f"different args or a different tool."
                ),
                tool_name=tool_name,
                count=exact_count,
                signature=sig,
            )

        name_count = self._failed_names[tool_name]
        if name_count >= self.config.same_tool_block_threshold:
            return ToolCallGuardrailDecision(
                action="block",
                code="repeated_same_tool_block",
                message=(
                    f"[BLOCK] Tool '{tool_name}' has failed {name_count} "
                    f"times this turn (any args). Switch tools or stop."
                ),
                tool_name=tool_name,
                count=name_count,
                signature=sig,
            )

        return _allow(tool_name, sig)

    # ----- Post-call update + warning check ------------------------------

    def after_call(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        failed: bool,
    ) -> ToolCallGuardrailDecision:
        """Update counters and return a warn/block decision based on the new state.

        Call this AFTER the tool ran. `failed=True` increments failure
        counters; `failed=False` updates the no-progress tracker for
        read-only tools. The returned decision lets the caller emit a warning
        message or stop the turn if a threshold was just crossed.
        """
        sig = self._signature(tool_name, args)

        if failed:
            self._failed_signatures[sig] += 1
            self._failed_names[tool_name] += 1
            self._total_failures += 1

            # Check post-update thresholds for warn level. Block-level was
            # already checked in before_call; if we're here, it didn't fire,
            # but THIS failure may cross the warn line.
            exact_count = self._failed_signatures[sig]
            if exact_count >= self.config.exact_failure_warn_threshold:
                if self.config.warnings_enabled:
                    return ToolCallGuardrailDecision(
                        action="warn",
                        code="repeated_exact_failure_warning",
                        message=(
                            f"[WARN] '{tool_name}' has failed {exact_count} "
                            f"times with these exact args. Next attempt with "
                            f"these args may be blocked."
                        ),
                        tool_name=tool_name,
                        count=exact_count,
                        signature=sig,
                    )

            name_count = self._failed_names[tool_name]
            if name_count >= self.config.same_tool_warn_threshold:
                if self.config.warnings_enabled:
                    return ToolCallGuardrailDecision(
                        action="warn",
                        code="repeated_same_tool_warning",
                        message=(
                            f"[WARN] '{tool_name}' has failed {name_count} "
                            f"times this turn. Consider a different tool."
                        ),
                        tool_name=tool_name,
                        count=name_count,
                        signature=sig,
                    )
            return _allow(tool_name, sig)

        # Success path — track read-only no-progress.
        if tool_name in self.config.readonly_tools:
            result_hash = self._hash_result(result)
            bucket = self._readonly_results.setdefault(sig, Counter())
            bucket[result_hash] += 1
            same = bucket[result_hash]

            if same >= self.config.idempotent_block_threshold:
                return ToolCallGuardrailDecision(
                    action="block",
                    code="idempotent_no_progress_block",
                    message=(
                        f"[BLOCK] '{tool_name}' returned the same result "
                        f"{same} times with these args — no new information. "
                        f"Try different args or a different tool."
                    ),
                    tool_name=tool_name,
                    count=same,
                    signature=sig,
                )

            if same >= self.config.idempotent_warn_threshold:
                if self.config.warnings_enabled:
                    return ToolCallGuardrailDecision(
                        action="warn",
                        code="idempotent_no_progress_warning",
                        message=(
                            f"[WARN] '{tool_name}' keeps returning the same "
                            f"result for these args ({same} times). The data "
                            f"isn't changing — consider another approach."
                        ),
                        tool_name=tool_name,
                        count=same,
                        signature=sig,
                    )

        return _allow(tool_name, sig)

    # ----- Helpers -------------------------------------------------------

    def _signature(
        self, tool_name: str, args: Mapping[str, Any]
    ) -> ToolCallSignature:
        """Canonicalise args + hash so equivalent calls share a signature."""
        try:
            canonical = json.dumps(
                dict(args or {}),
                sort_keys=True, ensure_ascii=False, default=str,
            )
        except (TypeError, ValueError):
            # Defensive: extremely odd arg types still need *some* fingerprint.
            canonical = repr(args)
        h = hashlib.sha256(canonical.encode("utf-8", errors="replace")).hexdigest()
        return ToolCallSignature(tool_name=tool_name, args_hash=h[:16])

    @staticmethod
    def _hash_result(result: str) -> str:
        """Hash the first 4 KiB of the result.

        Truncation keeps the hash stable across irrelevant differences in
        long outputs (e.g. timestamps in the tail) without leaking memory
        on huge results.
        """
        payload = (result or "")[:4096].encode("utf-8", errors="replace")
        return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _allow(tool_name: str, sig: ToolCallSignature) -> ToolCallGuardrailDecision:
    """Cheap allow-decision factory used in the common path."""
    return ToolCallGuardrailDecision(
        action="allow",
        code="ok",
        message="",
        tool_name=tool_name,
        count=0,
        signature=sig,
    )
