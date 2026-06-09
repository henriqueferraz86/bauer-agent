"""Tests for `bauer/tool_guardrails.py`."""

from __future__ import annotations

import pytest

from bauer.tool_guardrails import (
    GuardrailConfig,
    ToolCallGuardrailController,
    ToolCallGuardrailDecision,
    ToolCallSignature,
    _DEFAULT_READONLY_TOOLS,
)


# ---------------------------------------------------------------------------
# Decision helpers
# ---------------------------------------------------------------------------


def _drive_failures(
    g: ToolCallGuardrailController,
    name: str,
    args: dict,
    n: int,
) -> ToolCallGuardrailDecision:
    """Simulate N failed calls, return the last after_call() decision."""
    last: ToolCallGuardrailDecision | None = None
    for _ in range(n):
        before = g.before_call(name, args)
        if before.should_halt:
            return before
        last = g.after_call(name, args, "[Erro: x]", failed=True)
    assert last is not None
    return last


def _drive_readonly_calls(
    g: ToolCallGuardrailController,
    name: str,
    args: dict,
    result: str,
    n: int,
) -> ToolCallGuardrailDecision:
    """Simulate N successful read-only calls with the same result."""
    last: ToolCallGuardrailDecision | None = None
    for _ in range(n):
        before = g.before_call(name, args)
        if before.should_halt:
            return before
        last = g.after_call(name, args, result, failed=False)
    assert last is not None
    return last


# ---------------------------------------------------------------------------
# Signature canonicalisation
# ---------------------------------------------------------------------------


def test_signature_stable_for_equivalent_args():
    """Different dict key orders produce the same signature."""
    g = ToolCallGuardrailController()
    s1 = g._signature("read_file", {"a": 1, "b": 2})
    s2 = g._signature("read_file", {"b": 2, "a": 1})
    assert s1 == s2


def test_signature_differs_for_different_args():
    g = ToolCallGuardrailController()
    s1 = g._signature("read_file", {"path": "a.txt"})
    s2 = g._signature("read_file", {"path": "b.txt"})
    assert s1 != s2


def test_signature_differs_for_different_tools():
    g = ToolCallGuardrailController()
    s1 = g._signature("read_file", {"path": "a"})
    s2 = g._signature("list_dir",  {"path": "a"})
    assert s1 != s2


def test_signature_handles_non_serialisable_args():
    """Defensive: weird args still produce *some* fingerprint."""
    class _X:
        pass
    g = ToolCallGuardrailController()
    sig = g._signature("read_file", {"obj": _X()})
    assert isinstance(sig, ToolCallSignature)
    assert sig.tool_name == "read_file"


# ---------------------------------------------------------------------------
# Decision properties
# ---------------------------------------------------------------------------


def test_decision_allows_execution_for_allow_and_warn():
    sig = ToolCallSignature("t", "h")
    allow = ToolCallGuardrailDecision("allow", "ok", "", "t", 0, sig)
    warn  = ToolCallGuardrailDecision("warn",  "x",  "m", "t", 1, sig)
    assert allow.allows_execution is True
    assert warn.allows_execution is True


def test_decision_should_halt_for_block_and_halt():
    sig = ToolCallSignature("t", "h")
    block = ToolCallGuardrailDecision("block", "x", "m", "t", 5, sig)
    halt  = ToolCallGuardrailDecision("halt",  "x", "m", "t", 9, sig)
    assert block.should_halt is True
    assert halt.should_halt is True


# ---------------------------------------------------------------------------
# Exact-failure loop
# ---------------------------------------------------------------------------


def test_first_failure_is_silent_below_warn_threshold():
    """One failure under default warn=2 should not surface a warning."""
    g = ToolCallGuardrailController()
    before = g.before_call("read_file", {"path": "x"})
    assert before.action == "allow"
    after = g.after_call("read_file", {"path": "x"}, "[Erro: y]", failed=True)
    assert after.action == "allow"


def test_exact_failure_warn_at_default_threshold():
    """Default warn=2 → second failure with same args triggers warn."""
    g = ToolCallGuardrailController()
    decision = _drive_failures(g, "read_file", {"path": "x"}, 2)
    assert decision.action == "warn"
    assert decision.code == "repeated_exact_failure_warning"


def test_exact_failure_block_at_default_threshold():
    """After 5 failures with same args, the next before_call blocks."""
    g = ToolCallGuardrailController()
    # Drive 5 failures via after_call (signature count reaches 5).
    for _ in range(5):
        g.after_call("read_file", {"path": "x"}, "[Erro]", failed=True)
    # 6th attempt must be blocked PRE-execution.
    decision = g.before_call("read_file", {"path": "x"})
    assert decision.action == "block"
    assert decision.code == "repeated_exact_failure_block"
    assert decision.should_halt is True


def test_exact_failure_does_not_fire_on_different_args():
    """Failures with different args don't aggregate into the exact-signature
    counter — they hit same-tool counter instead."""
    g = ToolCallGuardrailController()
    g.after_call("read_file", {"path": "a"}, "[Erro]", failed=True)
    g.after_call("read_file", {"path": "a"}, "[Erro]", failed=True)
    # Different args — exact_count for {path: 'b'} starts fresh.
    decision = g.after_call("read_file", {"path": "b"}, "[Erro]", failed=True)
    # Should NOT be repeated_exact_failure_warning (that's per-signature).
    assert decision.code != "repeated_exact_failure_warning"


# ---------------------------------------------------------------------------
# Same-tool failure
# ---------------------------------------------------------------------------


def test_same_tool_warn_at_default_threshold():
    """3 failures across any args on the same tool triggers same-tool warn."""
    g = ToolCallGuardrailController()
    g.after_call("read_file", {"path": "a"}, "[Erro]", failed=True)
    g.after_call("read_file", {"path": "b"}, "[Erro]", failed=True)
    decision = g.after_call("read_file", {"path": "c"}, "[Erro]", failed=True)
    assert decision.action == "warn"
    assert decision.code == "repeated_same_tool_warning"


def test_same_tool_block_at_default_threshold():
    """8 failures across any args blocks the 9th call."""
    g = ToolCallGuardrailController()
    for i in range(8):
        g.after_call("read_file", {"path": f"f{i}"}, "[Erro]", failed=True)
    decision = g.before_call("read_file", {"path": "another"})
    assert decision.action == "block"
    assert decision.code == "repeated_same_tool_block"


# ---------------------------------------------------------------------------
# Idempotent no-progress
# ---------------------------------------------------------------------------


def test_idempotent_warn_when_readonly_tool_repeats():
    """A read-only tool returning the same result twice triggers warn."""
    g = ToolCallGuardrailController()
    g.after_call("read_file", {"path": "x"}, "same data\nhello",
                  failed=False)
    decision = g.after_call("read_file", {"path": "x"}, "same data\nhello",
                              failed=False)
    assert decision.action == "warn"
    assert decision.code == "idempotent_no_progress_warning"


def test_idempotent_block_at_threshold():
    """5 identical reads from same args → block on the 5th."""
    g = ToolCallGuardrailController()
    # 5 reads with identical body — last one should be 'block' once count=5.
    decisions = []
    for _ in range(5):
        decisions.append(
            g.after_call("read_file", {"path": "x"}, "x", failed=False)
        )
    assert decisions[-1].action == "block"
    assert decisions[-1].code == "idempotent_no_progress_block"


def test_idempotent_does_not_fire_on_writes():
    """write_file / execute_code are NOT read-only — no-progress is silent."""
    g = ToolCallGuardrailController()
    for _ in range(10):
        decision = g.after_call("write_file",
                                  {"path": "a", "content": "x"},
                                  "[ok] wrote 1 bytes",
                                  failed=False)
        assert decision.action == "allow"


def test_idempotent_does_not_fire_when_result_differs():
    """Same args, different results = healthy — no warning."""
    g = ToolCallGuardrailController()
    g.after_call("read_file", {"path": "x"}, "first", failed=False)
    decision = g.after_call("read_file", {"path": "x"}, "second", failed=False)
    assert decision.action == "allow"


def test_idempotent_fires_with_custom_readonly_set():
    """Caller-defined read-only set works for tools not in the default."""
    cfg = GuardrailConfig(readonly_tools=frozenset({"custom_query"}))
    g = ToolCallGuardrailController(cfg)
    g.after_call("custom_query", {"q": "x"}, "result", failed=False)
    decision = g.after_call("custom_query", {"q": "x"}, "result",
                              failed=False)
    assert decision.action == "warn"


# ---------------------------------------------------------------------------
# Hard stop
# ---------------------------------------------------------------------------


def test_hard_stop_at_default_threshold():
    """12 aggregate failures triggers halt on the 13th call."""
    g = ToolCallGuardrailController()
    # 12 failures across mixed tools.
    for i in range(12):
        g.after_call(f"tool_{i % 3}", {"i": i}, "[Erro]", failed=True)
    decision = g.before_call("yet_another_tool", {"x": 1})
    assert decision.action == "halt"
    assert decision.code == "hard_stop_total_failures"
    assert decision.should_halt is True


def test_hard_stop_can_be_disabled():
    """hard_stop_enabled=False bypasses the aggregate ceiling."""
    cfg = GuardrailConfig(hard_stop_enabled=False)
    g = ToolCallGuardrailController(cfg)
    for i in range(20):
        g.after_call(f"tool_{i}", {}, "[Erro]", failed=True)
    decision = g.before_call("safe_tool", {})
    # Other thresholds may still fire, but it shouldn't be 'halt'.
    assert decision.action != "halt"


# ---------------------------------------------------------------------------
# warnings_enabled toggle
# ---------------------------------------------------------------------------


def test_warnings_disabled_returns_allow_at_warn_threshold():
    cfg = GuardrailConfig(warnings_enabled=False)
    g = ToolCallGuardrailController(cfg)
    decision = _drive_failures(g, "read_file", {"path": "x"}, 2)
    assert decision.action == "allow"


def test_warnings_disabled_does_not_affect_block():
    """Block decisions still fire even when warnings are off."""
    cfg = GuardrailConfig(warnings_enabled=False)
    g = ToolCallGuardrailController(cfg)
    for _ in range(5):
        g.after_call("read_file", {"path": "x"}, "[Erro]", failed=True)
    decision = g.before_call("read_file", {"path": "x"})
    assert decision.action == "block"


# ---------------------------------------------------------------------------
# Configuration knobs
# ---------------------------------------------------------------------------


def test_custom_thresholds_respected():
    """Lower warn threshold fires sooner."""
    cfg = GuardrailConfig(exact_failure_warn_threshold=1)
    g = ToolCallGuardrailController(cfg)
    decision = g.after_call("read_file", {"path": "x"}, "[Erro]", failed=True)
    assert decision.action == "warn"


def test_negative_threshold_clamped_to_zero():
    """Defensive: negative thresholds get raised to zero in __post_init__."""
    cfg = GuardrailConfig(exact_failure_warn_threshold=-3)
    assert cfg.exact_failure_warn_threshold == 0


def test_default_readonly_tools_includes_common_reads():
    """Sanity-check the shipped read-only set."""
    assert "read_file" in _DEFAULT_READONLY_TOOLS
    assert "list_dir" in _DEFAULT_READONLY_TOOLS
    assert "web_search" in _DEFAULT_READONLY_TOOLS
    # Side-effectful tools must NOT be in the read-only set.
    assert "write_file" not in _DEFAULT_READONLY_TOOLS
    assert "execute_code" not in _DEFAULT_READONLY_TOOLS
    assert "run_command" not in _DEFAULT_READONLY_TOOLS


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


def test_reset_clears_all_counters():
    """After reset() the controller behaves like brand new."""
    g = ToolCallGuardrailController()
    for _ in range(3):
        g.after_call("read_file", {"path": "x"}, "[Erro]", failed=True)
    assert g._total_failures == 3
    g.reset()
    assert g._total_failures == 0
    # Now a single failure should be silent again.
    decision = g.after_call("read_file", {"path": "x"}, "[Erro]", failed=True)
    assert decision.action == "allow"
