"""Kanban task diagnostics — stateless rules that surface actionable issues.

Each rule is a pure function:  rule(task, events, runs) → Diagnostic | None

Rules are stateless: they compute under demand and auto-clear the moment the
triggering condition resolves (task transitions, failure count resets, etc.).
No persistent state is written; diagnostics are always derived fresh.

Seven rules (aligned with Hermes kanban_diagnostics.py):

1. **repeated_failures** — consecutive_failures ≥ 3  (warning ≥ 3, error ≥ 5)
2. **repeated_crashes** — ≥ 2 runs with outcome = "crash" in recent history
3. **stuck_in_blocked** — status=blocked for > threshold minutes
4. **stranded_in_ready** — status=ready and no running dispatcher claimed it
   for > threshold minutes (dispatcher may be down)
5. **triage_aux_unavailable** — status=triage for > threshold and no specifier
   has ever touched it (hints that auxiliary LLM is not configured)
6. **prose_phantom_refs** — body mentions t_XXXX IDs that don't exist in DB
7. **hallucinated_cards** — title looks like a bare UUID / SHA fragment
   (LLM hallucinated a card instead of a real task)

Severity levels:
    "info"     — purely informational
    "warning"  — needs attention soon
    "error"    — blocking or actively harmful
    "critical" — immediate action required

Usage::

    from bauer.kanban_diagnostics import compute_task_diagnostics

    with kb.connect() as conn:
        task  = kb.get_task(conn, task_id)
        events = kb.list_events(conn, task_id)
        runs   = kb.list_runs(conn, task_id)
        all_ids = {t.id for t in kb.list_tasks(conn)}

    diags = compute_task_diagnostics(task, events, runs, all_task_ids=all_ids)
    for d in diags:
        print(f"[{d.severity.upper()}] {d.rule}: {d.message}")

Inspired by Hermes Agent's ``hermes_cli/kanban_diagnostics.py``.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Diagnostic:
    """A single diagnostic finding for a task.

    ``rule``     — machine-readable rule name (snake_case)
    ``severity`` — one of: "info", "warning", "error", "critical"
    ``message``  — human-readable explanation + suggested action
    ``task_id``  — the task this finding belongs to
    ``metadata`` — optional extra data (counts, timestamps, etc.)
    """
    rule: str
    severity: str
    message: str
    task_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    # Convenience predicates
    @property
    def is_warning(self) -> bool:
        return self.severity == "warning"

    @property
    def is_error(self) -> bool:
        return self.severity in {"error", "critical"}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class DiagnosticsConfig:
    """Threshold knobs for each rule.

    All durations are in **minutes** for human readability.
    """
    # repeated_failures
    failures_warning_threshold: int = 3
    failures_error_threshold: int = 5

    # repeated_crashes
    crashes_threshold: int = 2

    # stuck_in_blocked
    blocked_warning_minutes: float = 30.0
    blocked_error_minutes: float = 120.0

    # stranded_in_ready
    ready_warning_minutes: float = 15.0
    ready_error_minutes: float = 60.0

    # triage_aux_unavailable
    triage_warning_minutes: float = 10.0
    triage_error_minutes: float = 30.0

    # hallucinated_cards — title patterns
    hallucination_min_title_length: int = 6
    hallucination_max_title_length: int = 64


_DEFAULT_CONFIG = DiagnosticsConfig()

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Task ID references in body text (e.g. "t_abc123", "t-def456", "task_001")
_TASK_REF_RE = re.compile(r"\b(t[_-][a-zA-Z0-9]{3,})\b")

# Hallucinated card: title is a bare hex hash or UUID fragment
_HEX_TITLE_RE = re.compile(r"^[0-9a-fA-F]{7,}$")
_UUID_TITLE_RE = re.compile(
    r"^[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}$"
)


# ---------------------------------------------------------------------------
# Rule implementations (each returns Diagnostic | None)
# ---------------------------------------------------------------------------


def _rule_repeated_failures(
    task, events: list[dict], runs: list[dict], cfg: DiagnosticsConfig, **_
) -> Diagnostic | None:
    """Fire when consecutive_failures is high enough to warrant attention."""
    n = task.consecutive_failures
    if n >= cfg.failures_error_threshold:
        return Diagnostic(
            rule="repeated_failures",
            severity="error",
            message=(
                f"Task failed {n} times consecutively. "
                "Check last_failure_error and fix the root cause, "
                "or set status to 'blocked' to pause retries."
            ),
            task_id=task.id,
            metadata={"consecutive_failures": n},
        )
    if n >= cfg.failures_warning_threshold:
        return Diagnostic(
            rule="repeated_failures",
            severity="warning",
            message=(
                f"Task has failed {n} times consecutively. "
                f"Will be marked 'failed' after {task.max_retries} total retries."
            ),
            task_id=task.id,
            metadata={"consecutive_failures": n, "max_retries": task.max_retries},
        )
    return None


def _rule_repeated_crashes(
    task, events: list[dict], runs: list[dict], cfg: DiagnosticsConfig, **_
) -> Diagnostic | None:
    """Fire when recent runs ended with outcome='crash'."""
    crash_count = sum(1 for r in runs if (r.get("outcome") or "") == "crash")
    if crash_count >= cfg.crashes_threshold:
        return Diagnostic(
            rule="repeated_crashes",
            severity="error",
            message=(
                f"Task has crashed {crash_count} times. "
                "Likely an unhandled exception in the worker. "
                "Check run summaries and error fields."
            ),
            task_id=task.id,
            metadata={"crash_count": crash_count},
        )
    return None


def _rule_stuck_in_blocked(
    task, events: list[dict], runs: list[dict], cfg: DiagnosticsConfig, **_
) -> Diagnostic | None:
    """Fire when a task has been in 'blocked' status for too long."""
    if task.status != "blocked":
        return None

    # Find when it entered blocked status from events (kind = "status_change").
    entered_blocked_at: float | None = None
    for ev in reversed(events):
        payload = ev.get("payload") or {}
        if isinstance(payload, str):
            try:
                import json as _json
                payload = _json.loads(payload)
            except Exception:
                payload = {}
        if ev.get("kind") == "status_change" and payload.get("new_status") == "blocked":
            entered_blocked_at = float(ev.get("created_at") or 0)
            break

    # Fall back to task.created_at if no event found.
    reference_ts = entered_blocked_at or task.created_at or 0.0
    age_minutes = (time.time() - reference_ts) / 60.0

    if age_minutes >= cfg.blocked_error_minutes:
        return Diagnostic(
            rule="stuck_in_blocked",
            severity="error",
            message=(
                f"Task has been blocked for {age_minutes:.0f} minutes "
                f"(threshold: {cfg.blocked_error_minutes:.0f}m). "
                "Investigate the blocker or close as won't-fix."
            ),
            task_id=task.id,
            metadata={"blocked_minutes": round(age_minutes, 1)},
        )
    if age_minutes >= cfg.blocked_warning_minutes:
        return Diagnostic(
            rule="stuck_in_blocked",
            severity="warning",
            message=(
                f"Task has been blocked for {age_minutes:.0f} minutes. "
                "Check if the blocker is still relevant."
            ),
            task_id=task.id,
            metadata={"blocked_minutes": round(age_minutes, 1)},
        )
    return None


def _rule_stranded_in_ready(
    task, events: list[dict], runs: list[dict], cfg: DiagnosticsConfig, **_
) -> Diagnostic | None:
    """Fire when a task has been ready but unclaimed for too long (dispatcher down?)."""
    if task.status != "ready":
        return None

    # Find the most recent transition to 'ready'.
    entered_ready_at: float | None = None
    for ev in reversed(events):
        payload = ev.get("payload") or {}
        if isinstance(payload, str):
            try:
                import json as _json
                payload = _json.loads(payload)
            except Exception:
                payload = {}
        if ev.get("kind") == "status_change" and payload.get("new_status") == "ready":
            entered_ready_at = float(ev.get("created_at") or 0)
            break

    reference_ts = entered_ready_at or task.created_at or 0.0
    age_minutes = (time.time() - reference_ts) / 60.0

    if age_minutes >= cfg.ready_error_minutes:
        return Diagnostic(
            rule="stranded_in_ready",
            severity="error",
            message=(
                f"Task has been ready for {age_minutes:.0f} minutes with no dispatcher "
                f"claiming it (threshold: {cfg.ready_error_minutes:.0f}m). "
                "The dispatcher may be down or there are no matching workers."
            ),
            task_id=task.id,
            metadata={"ready_minutes": round(age_minutes, 1)},
        )
    if age_minutes >= cfg.ready_warning_minutes:
        return Diagnostic(
            rule="stranded_in_ready",
            severity="warning",
            message=(
                f"Task has been ready for {age_minutes:.0f} minutes. "
                "Check if a dispatcher is running."
            ),
            task_id=task.id,
            metadata={"ready_minutes": round(age_minutes, 1)},
        )
    return None


def _rule_triage_aux_unavailable(
    task, events: list[dict], runs: list[dict], cfg: DiagnosticsConfig, **_
) -> Diagnostic | None:
    """Fire when a triage task hasn't been specified yet (auxiliary LLM issue)."""
    if task.status != "triage":
        return None

    # If a 'specify' event exists, the auxiliary ran at least once.
    for ev in events:
        if (ev.get("kind") or "").startswith("specify"):
            return None

    age_minutes = (time.time() - (task.created_at or 0.0)) / 60.0

    if age_minutes >= cfg.triage_error_minutes:
        return Diagnostic(
            rule="triage_aux_unavailable",
            severity="error",
            message=(
                f"Task has been in triage for {age_minutes:.0f} minutes with no "
                f"specifier run recorded (threshold: {cfg.triage_error_minutes:.0f}m). "
                "Check that `auxiliary.triage_specifier` is configured in config.yaml."
            ),
            task_id=task.id,
            metadata={"triage_minutes": round(age_minutes, 1)},
        )
    if age_minutes >= cfg.triage_warning_minutes:
        return Diagnostic(
            rule="triage_aux_unavailable",
            severity="warning",
            message=(
                f"Task has been in triage for {age_minutes:.0f} minutes with no "
                "specifier activity. Run `bauer kanban-specify <id>` manually."
            ),
            task_id=task.id,
            metadata={"triage_minutes": round(age_minutes, 1)},
        )
    return None


def _rule_prose_phantom_refs(
    task,
    events: list[dict],
    runs: list[dict],
    cfg: DiagnosticsConfig,
    all_task_ids: frozenset[str] | None = None,
    **_,
) -> Diagnostic | None:
    """Fire when the task body references task IDs that don't exist in the DB."""
    if all_task_ids is None:
        return None  # can't check without the full ID set

    body = task.body or ""
    refs = _TASK_REF_RE.findall(body)
    phantom = [r for r in refs if r not in all_task_ids]
    if phantom:
        return Diagnostic(
            rule="prose_phantom_refs",
            severity="warning",
            message=(
                f"Task body references {len(phantom)} non-existent task IDs: "
                f"{', '.join(phantom[:5])}{'…' if len(phantom) > 5 else ''}. "
                "These may be hallucinated or from a deleted board."
            ),
            task_id=task.id,
            metadata={"phantom_refs": phantom},
        )
    return None


def _rule_hallucinated_cards(
    task, events: list[dict], runs: list[dict], cfg: DiagnosticsConfig, **_
) -> Diagnostic | None:
    """Fire when the task title looks like a raw hash or UUID (LLM hallucination)."""
    title = (task.title or "").strip()
    if not title:
        return None

    min_len = cfg.hallucination_min_title_length
    max_len = cfg.hallucination_max_title_length

    if min_len <= len(title) <= max_len and (
        _HEX_TITLE_RE.match(title) or _UUID_TITLE_RE.match(title)
    ):
        return Diagnostic(
            rule="hallucinated_cards",
            severity="warning",
            message=(
                f"Task title {title!r} looks like a bare hash/UUID. "
                "This may be a hallucinated card created by an LLM. "
                "Verify it corresponds to a real task and update the title."
            ),
            task_id=task.id,
            metadata={"suspicious_title": title},
        )
    return None


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

# All rules in priority order (more severe first within each group).
_RULES: list[Callable] = [
    _rule_repeated_crashes,         # error-level first
    _rule_repeated_failures,
    _rule_stuck_in_blocked,
    _rule_stranded_in_ready,
    _rule_triage_aux_unavailable,
    _rule_prose_phantom_refs,
    _rule_hallucinated_cards,
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_task_diagnostics(
    task,
    events: list[dict],
    runs: list[dict],
    *,
    config: DiagnosticsConfig | None = None,
    all_task_ids: frozenset[str] | None = None,
) -> list[Diagnostic]:
    """Run all diagnostic rules against *task* and return findings.

    Parameters
    ----------
    task:
        A ``bauer.kanban_db.Task`` dataclass instance.
    events:
        List of event dicts from ``kanban_db.list_events(conn, task.id)``.
    runs:
        List of run dicts from ``kanban_db.list_runs(conn, task.id)``.
    config:
        Optional :class:`DiagnosticsConfig` for threshold overrides.
    all_task_ids:
        Optional set of all task IDs in the board. Required by
        ``prose_phantom_refs``; pass ``None`` to skip that rule.

    Returns a list of :class:`Diagnostic` instances, one per firing rule.
    Empty list = no issues.
    """
    cfg = config or _DEFAULT_CONFIG
    findings: list[Diagnostic] = []
    for rule_fn in _RULES:
        try:
            diag = rule_fn(
                task, events, runs,
                cfg=cfg,
                all_task_ids=all_task_ids,
            )
            if diag is not None:
                findings.append(diag)
        except Exception:
            # Rules must never crash the caller.
            pass
    return findings


def compute_board_diagnostics(
    tasks: Sequence,
    *,
    events_by_task: dict[str, list[dict]] | None = None,
    runs_by_task: dict[str, list[dict]] | None = None,
    config: DiagnosticsConfig | None = None,
) -> list[Diagnostic]:
    """Run diagnostics across an entire board.

    Parameters
    ----------
    tasks:
        Iterable of ``Task`` objects (all non-archived tasks on the board).
    events_by_task:
        Pre-fetched events keyed by task ID. If None, each task gets [].
    runs_by_task:
        Pre-fetched runs keyed by task ID. If None, each task gets [].
    config:
        Optional threshold overrides.

    Returns all findings across all tasks, sorted by severity (critical first).
    """
    all_ids = frozenset(t.id for t in tasks)
    evts = events_by_task or {}
    rns = runs_by_task or {}
    all_findings: list[Diagnostic] = []
    for task in tasks:
        task_diags = compute_task_diagnostics(
            task,
            evts.get(task.id, []),
            rns.get(task.id, []),
            config=config,
            all_task_ids=all_ids,
        )
        all_findings.extend(task_diags)

    # Sort: critical → error → warning → info
    _order = {"critical": 0, "error": 1, "warning": 2, "info": 3}
    all_findings.sort(key=lambda d: _order.get(d.severity, 9))
    return all_findings
