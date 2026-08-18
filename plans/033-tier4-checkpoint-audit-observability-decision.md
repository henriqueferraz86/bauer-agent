# Plan 033: Decide the fate of the "Tier 4" subsystem (checkpoint / audit_trail / observability) — wire in or delete

> **Executor instructions**: This plan has an investigation step followed by
> a fork (Path A: wire in, Path B: delete). Do Step 1 first, then follow the
> STOP/decision rule at the end of Step 1 to pick a path — do not attempt
> both. Run every verification command and confirm the expected result
> before moving to the next step. When done, update the status row for this
> plan in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/checkpoint.py bauer/audit_trail.py bauer/observability.py bauer/daemon.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW (Path B: delete) / MED (Path A: wire in — touches the
  daemon's crash-recovery path)
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

Three modules — `bauer/checkpoint.py` (`CheckpointManager`/`RecoveryManager`,
crash-recovery snapshots), `bauer/audit_trail.py` (`AuditTrail`, append-only
SQLite action log), and `bauer/observability.py` (`MetricsRegistry`,
Prometheus-style in-process metrics) — are fully implemented, individually
tested (`tests/test_tier4.py`), and listed in `CHANGELOG.md:132` as shipped
("Autonomia: IterationBudget, CheckpointManager, MetricsRegistry,
AuditTrail"). None of the three is ever imported by `bauer/daemon.py` — the
component whose crash-recovery, audit, and metrics story they were built to
serve (confirmed: `grep -n "CheckpointManager\|RecoveryManager\|AuditTrail\|MetricsRegistry" bauer/daemon.py`
→ zero matches). This means the CHANGELOG's claim is false in practice: if
the daemon crashes today, nothing calls `RecoveryManager.latest()` on
restart, so in-progress goals are not resumed despite the feature being
documented as delivered. Separately, the codebase already has a *different*,
actually-used audit mechanism — `bauer/audit_logger.py`'s `AuditLogger`
(used at `bauer/tool_router.py:527`) — which may make `audit_trail.py`
redundant rather than merely unwired. This plan resolves the ambiguity
(wire in vs. delete) instead of leaving ~1,200 lines of tested-but-inert
code and a false changelog claim in place indefinitely.

## Current state

- `bauer/checkpoint.py:1-36+` — `CheckpointManager.save(goals, budget,
  payload)` / `RecoveryManager.latest() -> RecoveryResult`. Docstring:
  *"This allows the daemon to resume in-progress goals after a crash or
  restart without losing context."*
- `bauer/audit_trail.py:1-30+` — `AuditTrail`, append-only `audit_events`
  SQLite table (`tool_call|llm_call|goal_start|goal_done|escalation|
  approval|config_change|error`).
- `bauer/observability.py:1-30+` — `Counter`/`Gauge`/`Histogram`/
  `MetricsRegistry`/`make_daemon_metrics`, OpenMetrics/Prometheus text
  export. **Naming collision**: there is a *different, unrelated* package
  at `bauer/core/observability/` (`AuditLog`/`RunTraceStore`) actively used
  by `bauer/server.py:505,716` and `bauer/desktop_api.py:537` — do not
  confuse the two when investigating; they share a name but not an API.
- `bauer/daemon.py` — confirmed zero references to any of the three modules
  above (see "Why this matters"). This is the file each module's docstring
  claims to integrate with.
- `bauer/audit_logger.py:60` — `class AuditLogger`, with `log_tool_call`
  (`audit_logger.py:71`), imported live at `bauer/tool_router.py:527`
  (`from .audit_logger import AuditLogger`). This is a **separate**,
  currently-used audit mechanism — investigate in Step 1 whether it already
  covers what `audit_trail.py`'s `AuditTrail` was meant to provide.
- `CHANGELOG.md:132` — lists the three as shipped, under "Autonomia".
- `tests/test_tier4.py` — the only place any of the three modules is
  exercised outside their own source files (confirmed via
  `grep -rn "checkpoint import\|CheckpointManager(\|RecoveryManager(" bauer tests | grep -v checkpoint.py`
  → only this test file; same pattern confirmed independently for
  `AuditTrail(` and `MetricsRegistry(`/`make_daemon_metrics(`).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Test (tier4) | `uv run pytest tests/test_tier4.py -q` | all pass (Path A) / file removed (Path B) |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/checkpoint.py`, `bauer/audit_trail.py`, `bauer/observability.py`
- `bauer/daemon.py` (Path A only — add the wiring)
- `tests/test_tier4.py`
- `CHANGELOG.md` (correct or confirm the existing claim)

**Out of scope** (do NOT touch, even though they look related):
- `bauer/core/observability/` (the *different* `AuditLog`/`RunTraceStore`
  package) — do not merge, rename, or touch this package; it is a distinct,
  live system that merely shares a name with `bauer/observability.py`.
- `bauer/audit_logger.py` — read-only reference for the Step 1 investigation
  (to determine if `audit_trail.py` is redundant); do not modify it.
- `bauer/core/runtime/run_manager.py` / `bauer/core/runtime/state_store.py`
  — read-only reference for the Step 1 investigation; do not modify them.

## Git workflow

- Branch: `chore/033-tier4-decision` (Path B) or `feat/033-wire-tier4`
  (Path A — this is a `feat` since it activates previously-inert behavior)
- Commit message style: conventional commits matching recent history.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Investigate — does the newer `core/runtime`/`core/observability` generation already cover this ground?

Answer three questions by reading the code (not guessing):

1. **Crash recovery**: does `bauer/core/runtime/run_manager.py` (combined
   with `bauer/core/runtime/state_store.py`'s `JsonlStateStore`) already
   persist enough run/goal state that a kernel-governed run can resume after
   a daemon restart, without `checkpoint.py`? Read `run_manager.py` in full
   and check whether it has anything equivalent to `RecoveryManager.latest()`.
2. **Audit trail**: does `bauer/audit_logger.py`'s `AuditLogger` (used live
   at `tool_router.py:527`) already record enough of what
   `audit_trail.py`'s `AuditTrail` schema wants (`tool_call|llm_call|
   goal_start|goal_done|escalation|approval|config_change|error`)? Read
   both files' schemas side by side.
3. **Metrics**: does `bauer/core/observability/` or anything else already
   expose Prometheus/OpenMetrics-style counters for daemon activity, making
   `observability.py`'s `MetricsRegistry` redundant?

**Decision rule** (apply per-module independently — you may end up choosing
Path A for one module and Path B for another):
- If the newer generation **already covers** a module's purpose →
  **Path B** (delete) for that module.
- If the newer generation does **not** cover it, and crash-recovery /
  audit / metrics for the daemon is still a wanted capability → **Path A**
  (wire in) for that module.
- If genuinely unsure after reading the code, default to **Path B**
  (delete) and record the investigation findings in the commit message —
  unused-but-plausible-someday code is a worse default than a clean
  deletion recoverable from git history.

**Verify**: you can state, for each of the 3 modules, one sentence citing
`file:line` evidence for why it was kept (Path A) or removed (Path B).

### Step 2a (Path A — wire in): Daemon integration

Only for modules where Step 1 concluded "wire in":

- `checkpoint.py`: in `bauer/daemon.py`, call `CheckpointManager.save(...)`
  periodically (find the daemon's existing periodic-tick location) and
  `RecoveryManager.latest()` once at startup, before the main loop begins;
  if `result.interrupted`, resume the listed goals via whatever mechanism
  `daemon.py` already uses to start a goal.
- `audit_trail.py`: add `AuditTrail` writes alongside the existing
  `AuditLogger.log_tool_call` calls at `tool_router.py:527`, using the
  richer event-type taxonomy (`goal_start`/`goal_done`/`escalation`/etc.)
  that `AuditLogger` doesn't cover, if Step 1 found a genuine gap.
- `observability.py`: instantiate `MetricsRegistry` in `daemon.py`, wire the
  counters/gauges/histograms the module already defines at the natural
  daemon lifecycle points (goal start/done, tool call, budget spend).

**Verify**: `uv run pytest tests/test_tier4.py -q` still passes; add a new
integration test asserting `daemon.py`'s startup path calls
`RecoveryManager.latest()` (mock/patch and assert called).

### Step 2b (Path B — delete): Remove unused modules

Only for modules where Step 1 concluded "delete":

- Delete the module file(s).
- Delete or trim `tests/test_tier4.py` to only cover modules that survive
  (delete the whole file if all 3 are removed).
- Remove any now-orphaned entries in `pyproject.toml`'s mypy `module = [...]`
  list (check `grep -n "bauer.checkpoint\|bauer.audit_trail\|bauer.observability" pyproject.toml` —
  careful: `bauer.observability` differs from `bauer.core.observability`,
  do not remove the latter if present).
- Update `CHANGELOG.md:132` to remove the claim for whichever modules were
  deleted (keep the line if any module was wired in instead via Path A).

**Verify**: `grep -rn "checkpoint import\|CheckpointManager(\|AuditTrail(\|MetricsRegistry(" bauer tests`
→ no matches for deleted modules.

## Test plan

- **Path A**: add a `daemon.py` startup-recovery integration test (see Step
  2a). Keep all existing `test_tier4.py` unit tests passing.
- **Path B**: no new tests needed — deletion of unused code. Confirm the
  full suite's pass count drops by exactly the number of tests removed with
  `test_tier4.py` (or the surviving subset), not more.

Verification: `uv run pytest tests/ -q --tb=short` → all pass either way.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] Step 1's investigation is recorded (in the commit message or a code
      comment) with `file:line` evidence per module
- [ ] For each of the 3 modules, exactly one path (A or B) was applied —
      not left half-wired
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] `CHANGELOG.md:132` accurately reflects the post-decision state (no
      claim of a capability that was deleted)
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 033 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1's investigation is inconclusive after a genuine read of
  `run_manager.py`/`state_store.py`/`audit_logger.py`/`core/observability/`
  — report your partial findings rather than guessing a path.
- `bauer/daemon.py` has grown references to any of the 3 modules since this
  plan was written (contradicts the "zero references" premise) — re-read
  the current state before proceeding.
- Wiring in (Path A) for `checkpoint.py` would require restructuring
  `daemon.py`'s main loop beyond adding two call sites (save + restore) —
  that's a larger change than this plan scopes; report instead of
  improvising a bigger refactor.

## Maintenance notes

- Whichever modules survive as "wired in" become load-bearing daemon
  infrastructure — a reviewer should confirm the daemon's test coverage
  (crash-mid-run scenarios) actually exercises the new call sites, not just
  that they're present.
- This decision should inform Plan 037 (scheduler engine consolidation) if
  any surviving module turns out to be relevant to which scheduler
  generation is kept — flag that connection if it surfaces in Step 1.
