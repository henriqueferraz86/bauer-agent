"""Tests for `bauer/kanban_diagnostics.py` — 7 stateless diagnostic rules."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pytest

from bauer.kanban_diagnostics import (
    Diagnostic,
    DiagnosticsConfig,
    compute_board_diagnostics,
    compute_task_diagnostics,
)


# ---------------------------------------------------------------------------
# Minimal Task stub (mirrors bauer.kanban_db.Task fields used by rules)
# ---------------------------------------------------------------------------


@dataclass
class _Task:
    id: str
    title: str
    body: str = ""
    status: str = "todo"
    consecutive_failures: int = 0
    last_failure_error: str = ""
    max_retries: int = 3
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0


def _task(**kw) -> _Task:
    """Factory for test tasks. created_at defaults to NOW unless overridden."""
    return _Task(**{"id": "t_test", "title": "Test task", **kw})


def _old_task(**kw) -> _Task:
    """Factory for tasks created a long time ago (1 hour)."""
    return _Task(**{
        "id": "t_test",
        "title": "Test task",
        "created_at": time.time() - 3600,
        **kw,
    })


def _status_event(new_status: str, offset: float = -1.0) -> dict:
    """Create a status_change event dict."""
    import json
    return {
        "kind": "status_change",
        "payload": json.dumps({"new_status": new_status}),
        "created_at": time.time() + offset,
    }


# ---------------------------------------------------------------------------
# Diagnostic dataclass
# ---------------------------------------------------------------------------


def test_diagnostic_is_warning():
    d = Diagnostic("r", "warning", "m", "t_1")
    assert d.is_warning is True
    assert d.is_error is False


def test_diagnostic_is_error():
    d = Diagnostic("r", "error", "m", "t_1")
    assert d.is_error is True


def test_diagnostic_critical_is_error():
    d = Diagnostic("r", "critical", "m", "t_1")
    assert d.is_error is True


# ---------------------------------------------------------------------------
# Rule 1: repeated_failures
# ---------------------------------------------------------------------------


def test_no_failures_no_diagnostic():
    task = _task(consecutive_failures=0)
    assert compute_task_diagnostics(task, [], []) == []


def test_failures_below_threshold_silent():
    task = _task(consecutive_failures=2)
    cfg = DiagnosticsConfig(failures_warning_threshold=3)
    assert compute_task_diagnostics(task, [], [], config=cfg) == []


def test_failures_at_warning_threshold():
    task = _task(consecutive_failures=3)
    cfg = DiagnosticsConfig(failures_warning_threshold=3, failures_error_threshold=5)
    diags = compute_task_diagnostics(task, [], [], config=cfg)
    rf = [d for d in diags if d.rule == "repeated_failures"]
    assert len(rf) == 1
    assert rf[0].severity == "warning"


def test_failures_at_error_threshold():
    task = _task(consecutive_failures=5)
    cfg = DiagnosticsConfig(failures_warning_threshold=3, failures_error_threshold=5)
    diags = compute_task_diagnostics(task, [], [], config=cfg)
    rf = [d for d in diags if d.rule == "repeated_failures"]
    assert len(rf) == 1
    assert rf[0].severity == "error"


def test_failures_metadata_contains_count():
    task = _task(consecutive_failures=4)
    diags = compute_task_diagnostics(task, [], [])
    rf = [d for d in diags if d.rule == "repeated_failures"]
    assert rf and rf[0].metadata["consecutive_failures"] == 4


# ---------------------------------------------------------------------------
# Rule 2: repeated_crashes
# ---------------------------------------------------------------------------


def test_no_runs_no_crash_diagnostic():
    task = _task()
    assert compute_task_diagnostics(task, [], []) == []


def test_single_crash_below_threshold():
    task = _task()
    runs = [{"outcome": "crash"}]
    cfg = DiagnosticsConfig(crashes_threshold=2)
    diags = compute_task_diagnostics(task, [], runs, config=cfg)
    assert all(d.rule != "repeated_crashes" for d in diags)


def test_two_crashes_at_threshold():
    task = _task()
    runs = [{"outcome": "crash"}, {"outcome": "crash"}]
    cfg = DiagnosticsConfig(crashes_threshold=2)
    diags = compute_task_diagnostics(task, [], runs, config=cfg)
    rc = [d for d in diags if d.rule == "repeated_crashes"]
    assert len(rc) == 1
    assert rc[0].severity == "error"
    assert rc[0].metadata["crash_count"] == 2


def test_mixed_outcomes_counts_only_crashes():
    task = _task()
    runs = [
        {"outcome": "success"},
        {"outcome": "crash"},
        {"outcome": "error"},
        {"outcome": "crash"},
    ]
    cfg = DiagnosticsConfig(crashes_threshold=2)
    diags = compute_task_diagnostics(task, [], runs, config=cfg)
    rc = [d for d in diags if d.rule == "repeated_crashes"]
    assert rc[0].metadata["crash_count"] == 2


# ---------------------------------------------------------------------------
# Rule 3: stuck_in_blocked
# ---------------------------------------------------------------------------


def test_non_blocked_task_no_stuck_diagnostic():
    task = _task(status="running")
    assert all(d.rule != "stuck_in_blocked" for d in compute_task_diagnostics(task, [], []))


def test_recently_blocked_no_diagnostic():
    task = _old_task(status="blocked")
    # Use a recent status_change event so age < threshold
    events = [_status_event("blocked", offset=-60)]  # 1 min ago
    cfg = DiagnosticsConfig(blocked_warning_minutes=30.0)
    diags = compute_task_diagnostics(task, events, [], config=cfg)
    assert all(d.rule != "stuck_in_blocked" for d in diags)


def test_long_blocked_triggers_warning():
    task = _old_task(status="blocked")
    # Event says it entered blocked 45 min ago
    events = [_status_event("blocked", offset=-2700)]  # 45 min
    cfg = DiagnosticsConfig(blocked_warning_minutes=30.0, blocked_error_minutes=120.0)
    diags = compute_task_diagnostics(task, events, [], config=cfg)
    sib = [d for d in diags if d.rule == "stuck_in_blocked"]
    assert sib and sib[0].severity == "warning"


def test_very_long_blocked_triggers_error():
    task = _old_task(status="blocked", created_at=time.time() - 7200)
    cfg = DiagnosticsConfig(blocked_warning_minutes=30.0, blocked_error_minutes=60.0)
    # No event — falls back to created_at (2h ago)
    diags = compute_task_diagnostics(task, [], [], config=cfg)
    sib = [d for d in diags if d.rule == "stuck_in_blocked"]
    assert sib and sib[0].severity == "error"


# ---------------------------------------------------------------------------
# Rule 4: stranded_in_ready
# ---------------------------------------------------------------------------


def test_non_ready_task_no_stranded_diagnostic():
    task = _task(status="todo")
    assert all(d.rule != "stranded_in_ready" for d in compute_task_diagnostics(task, [], []))


def test_recently_ready_no_diagnostic():
    task = _task(status="ready", created_at=time.time() - 60)
    cfg = DiagnosticsConfig(ready_warning_minutes=15.0)
    diags = compute_task_diagnostics(task, [], [], config=cfg)
    assert all(d.rule != "stranded_in_ready" for d in diags)


def test_long_ready_triggers_warning():
    task = _old_task(status="ready")
    events = [_status_event("ready", offset=-2700)]  # 45 min ago
    cfg = DiagnosticsConfig(ready_warning_minutes=15.0, ready_error_minutes=60.0)
    diags = compute_task_diagnostics(task, events, [], config=cfg)
    sir = [d for d in diags if d.rule == "stranded_in_ready"]
    assert sir and sir[0].severity == "warning"


def test_very_long_ready_triggers_error():
    task = _old_task(status="ready", created_at=time.time() - 7200)
    cfg = DiagnosticsConfig(ready_warning_minutes=15.0, ready_error_minutes=60.0)
    diags = compute_task_diagnostics(task, [], [], config=cfg)
    sir = [d for d in diags if d.rule == "stranded_in_ready"]
    assert sir and sir[0].severity == "error"


# ---------------------------------------------------------------------------
# Rule 5: triage_aux_unavailable
# ---------------------------------------------------------------------------


def test_non_triage_no_aux_diagnostic():
    task = _task(status="todo")
    assert all(d.rule != "triage_aux_unavailable" for d in
               compute_task_diagnostics(task, [], []))


def test_fresh_triage_no_diagnostic():
    task = _task(status="triage", created_at=time.time() - 60)
    cfg = DiagnosticsConfig(triage_warning_minutes=10.0)
    diags = compute_task_diagnostics(task, [], [], config=cfg)
    assert all(d.rule != "triage_aux_unavailable" for d in diags)


def test_old_triage_without_specify_event_triggers():
    task = _old_task(status="triage")
    cfg = DiagnosticsConfig(triage_warning_minutes=5.0, triage_error_minutes=20.0)
    diags = compute_task_diagnostics(task, [], [], config=cfg)
    tau = [d for d in diags if d.rule == "triage_aux_unavailable"]
    assert tau


def test_triage_with_specify_event_clears():
    task = _old_task(status="triage")
    events = [{"kind": "specify_done", "payload": "{}", "created_at": time.time()}]
    cfg = DiagnosticsConfig(triage_warning_minutes=5.0)
    diags = compute_task_diagnostics(task, events, [], config=cfg)
    assert all(d.rule != "triage_aux_unavailable" for d in diags)


def test_triage_very_old_triggers_error():
    task = _old_task(status="triage", created_at=time.time() - 7200)
    cfg = DiagnosticsConfig(triage_warning_minutes=5.0, triage_error_minutes=30.0)
    diags = compute_task_diagnostics(task, [], [], config=cfg)
    tau = [d for d in diags if d.rule == "triage_aux_unavailable"]
    assert tau and tau[0].severity == "error"


# ---------------------------------------------------------------------------
# Rule 6: prose_phantom_refs
# ---------------------------------------------------------------------------


def test_no_body_no_phantom_ref():
    task = _task(body="")
    diags = compute_task_diagnostics(task, [], [],
                                     all_task_ids=frozenset({"t_abc"}))
    assert all(d.rule != "prose_phantom_refs" for d in diags)


def test_valid_refs_no_diagnostic():
    task = _task(body="depends on t_abc123 and t_def456")
    ids = frozenset({"t_abc123", "t_def456"})
    diags = compute_task_diagnostics(task, [], [], all_task_ids=ids)
    assert all(d.rule != "prose_phantom_refs" for d in diags)


def test_phantom_ref_triggers_warning():
    task = _task(body="blocks t_ghost99 and t_missing1")
    ids = frozenset({"t_abc"})
    diags = compute_task_diagnostics(task, [], [], all_task_ids=ids)
    ppr = [d for d in diags if d.rule == "prose_phantom_refs"]
    assert ppr
    assert set(ppr[0].metadata["phantom_refs"]) == {"t_ghost99", "t_missing1"}


def test_phantom_ref_skipped_without_all_task_ids():
    task = _task(body="refs t_ghost99")
    diags = compute_task_diagnostics(task, [], [], all_task_ids=None)
    assert all(d.rule != "prose_phantom_refs" for d in diags)


# ---------------------------------------------------------------------------
# Rule 7: hallucinated_cards
# ---------------------------------------------------------------------------


def test_normal_title_no_hallucination():
    task = _task(title="Implement user login flow")
    diags = compute_task_diagnostics(task, [], [])
    assert all(d.rule != "hallucinated_cards" for d in diags)


def test_hex_hash_title_triggers():
    task = _task(title="a1b2c3d4e5f6a7b8")
    diags = compute_task_diagnostics(task, [], [])
    hc = [d for d in diags if d.rule == "hallucinated_cards"]
    assert hc
    assert hc[0].severity == "warning"


def test_uuid_title_triggers():
    task = _task(title="550e8400-e29b-41d4-a716-446655440000")
    diags = compute_task_diagnostics(task, [], [])
    hc = [d for d in diags if d.rule == "hallucinated_cards"]
    assert hc


def test_too_short_hex_not_flagged():
    # "ab12" is 4 chars, below hallucination_min_title_length=6
    task = _task(title="ab12")
    diags = compute_task_diagnostics(task, [], [])
    assert all(d.rule != "hallucinated_cards" for d in diags)


def test_long_descriptive_hex_looking_title_not_flagged():
    # Over 64 chars — too long for a hash, likely a real description
    task = _task(title="a" * 65)
    cfg = DiagnosticsConfig(hallucination_max_title_length=64)
    diags = compute_task_diagnostics(task, [], [], config=cfg)
    assert all(d.rule != "hallucinated_cards" for d in diags)


# ---------------------------------------------------------------------------
# Auto-clear: conditions resolve → no diagnostic
# ---------------------------------------------------------------------------


def test_failure_count_reset_clears_diagnostic():
    """Once consecutive_failures goes back to 0, no repeated_failures fires."""
    task = _task(consecutive_failures=0)
    diags = compute_task_diagnostics(task, [], [])
    assert all(d.rule != "repeated_failures" for d in diags)


def test_status_change_from_blocked_clears_stuck():
    """After unblocking, stuck_in_blocked should not fire."""
    task = _task(status="todo")  # no longer blocked
    diags = compute_task_diagnostics(task, [], [])
    assert all(d.rule != "stuck_in_blocked" for d in diags)


# ---------------------------------------------------------------------------
# compute_board_diagnostics
# ---------------------------------------------------------------------------


def test_board_returns_all_findings():
    tasks = [
        _task(id="t_1", title="Normal task", status="todo"),
        _task(id="t_2", title="Failing", consecutive_failures=5),
        _task(id="t_3", title="a1b2c3d4e5f6"),  # hallucinated
    ]
    all_diags = compute_board_diagnostics(tasks)
    rules = {d.rule for d in all_diags}
    assert "repeated_failures" in rules
    assert "hallucinated_cards" in rules


def test_board_sorted_by_severity():
    """Errors come before warnings in board diagnostics."""
    tasks = [
        _task(id="t_1", title="ab12cd34ef56", consecutive_failures=5),
    ]
    all_diags = compute_board_diagnostics(tasks)
    severities = [d.severity for d in all_diags]
    _order = {"critical": 0, "error": 1, "warning": 2, "info": 3}
    assert severities == sorted(severities, key=lambda s: _order.get(s, 9))


def test_board_empty_tasks_returns_empty():
    assert compute_board_diagnostics([]) == []


def test_board_passes_all_ids_to_phantom_ref_rule():
    """Phantom ref rule correctly uses the full board ID set."""
    tasks = [
        _task(id="t_real", title="Real"),
        _task(id="t_ref_holder", title="Holds ref",
              body="depends on t_real and t_ghost"),
    ]
    all_diags = compute_board_diagnostics(tasks)
    ppr = [d for d in all_diags if d.rule == "prose_phantom_refs"]
    assert ppr
    assert "t_ghost" in ppr[0].metadata["phantom_refs"]
    assert "t_real" not in ppr[0].metadata["phantom_refs"]


# ---------------------------------------------------------------------------
# Rule safety: exceptions never propagate
# ---------------------------------------------------------------------------


def test_rules_never_crash_on_bad_input():
    """Even with None fields and malformed events, compute never raises."""
    task = _task(id=None, title=None, body=None, status=None,  # type: ignore
                  consecutive_failures="not-a-number")  # type: ignore
    events = [{"kind": None, "payload": None, "created_at": "bad"}]
    runs = [{"outcome": None}]
    # Should not raise
    diags = compute_task_diagnostics(task, events, runs)
    assert isinstance(diags, list)
