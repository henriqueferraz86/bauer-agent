# Plan 032: Delete `bauer/trigger_manager.py` (fully-built, zero-caller dead code)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/trigger_manager.py bauer/daemon.py`
> If either file changed since this plan was written, re-run the grep checks
> in Step 1 before proceeding; on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`bauer/trigger_manager.py` is a fully-built, 621-line, 4-trigger-type
(`CronTrigger`, `FilesystemTrigger`, `GitTrigger`, `WebhookTrigger`) async
event engine whose own module docstring claims: *"The daemon uses triggers
to start processing without constant polling."* That claim is false today —
`bauer/daemon.py` has zero references to `trigger_manager`, and nothing else
in the codebase imports it outside its own test file
(`tests/test_trigger_manager.py`). This is pure dead weight: a reader
encountering `daemon.py` and looking for how it avoids polling will find no
trace of this module, and a reader encountering `trigger_manager.py` will
believe it's live daemon infrastructure when it is not. Deleting it removes
621 lines of unused code and one source of confusion, with zero behavior
change (nothing currently depends on it).

## Current state

- `bauer/trigger_manager.py` — the file to delete. Docstring
  (`trigger_manager.py:1-31`) claims daemon integration:
  ```python
  """Trigger manager — event-driven task scheduling for the autonomous daemon.

  Triggers watch external conditions and fire a callback when they're met.
  The daemon uses triggers to start processing without constant polling.
  ...
  """
  ```
  Defines `BaseTrigger` (ABC, `trigger_manager.py:80`), `CronTrigger`
  (`:140`), `FilesystemTrigger` (`:203`), `GitTrigger` (`:298`),
  `WebhookTrigger` (`:395`), `TriggerManager` (`:538`).
- `tests/test_trigger_manager.py` — the only importer of this module
  anywhere in the repo (confirmed via
  `grep -rn "\btrigger_manager\b" --include="*.py" bauer tests` →
  only `bauer/trigger_manager.py` itself and this test file).
- `bauer/daemon.py` — confirmed zero references
  (`grep -n "trigger\|Trigger" bauer/daemon.py` → no matches). This is the
  file whose docstring in `trigger_manager.py` claims to use it.
- `pyproject.toml` — check whether `"bauer.trigger_manager"` appears in the
  `[[tool.mypy.overrides]]` `module = [...]` list (the repo has one such
  list starting around line 152, e.g. `"bauer.otel"` at line 204). If
  present, remove that line too when deleting the module.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |
| Confirm no importers remain | `grep -rn "trigger_manager" --include="*.py" bauer tests` | no matches |

## Scope

**In scope**:
- Delete `bauer/trigger_manager.py`
- Delete `tests/test_trigger_manager.py`
- Remove `"bauer.trigger_manager"` from `pyproject.toml`'s mypy `module =
  [...]` list, if present
- Remove any entry for `trigger_manager` in `MANIFEST.in` or packaging
  config, if present (check `grep -n "trigger_manager" MANIFEST.in
  pyproject.toml`)

**Out of scope** (do NOT touch, even though they look related):
- `bauer/automation_scheduler.py`, `bauer/automation_store.py`,
  `bauer/core/runtime/scheduler.py` — the three scheduling subsystems
  covered separately by Plan 037 (which decides which scheduler engine to
  keep). This plan only removes `trigger_manager.py`, which is unrelated to
  and unused by all three.
- `bauer/daemon.py` — confirmed to have zero dependency on
  `trigger_manager`; do not modify it as part of this deletion.

## Git workflow

- Branch: `chore/032-delete-trigger-manager`
- Commit message style: conventional commits matching recent history, e.g.
  `chore(daemon): remove trigger_manager.py — codigo morto, zero chamador`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Re-confirm zero callers

Run the exact grep from "Current state" to reconfirm nothing new imports
`trigger_manager` since this plan was written:

```bash
grep -rn "\btrigger_manager\b" --include="*.py" bauer tests
```

**Verify**: output is exactly 2 files — `bauer/trigger_manager.py` (its own
definitions) and `tests/test_trigger_manager.py` (its own tests). If any
other file appears, STOP (see STOP conditions).

### Step 2: Delete the module and its test

Delete `bauer/trigger_manager.py` and `tests/test_trigger_manager.py`.

**Verify**: `git status` shows both files as deleted; no other files touched
yet.

### Step 3: Clean up packaging references

```bash
grep -n "trigger_manager" pyproject.toml MANIFEST.in
```

If any line references `bauer.trigger_manager` or `trigger_manager.py`,
remove that line (keep the surrounding list's formatting/ordering intact —
it appears to be alphabetically sorted, so remove the line cleanly without
disturbing neighbors).

**Verify**: `grep -n "trigger_manager" pyproject.toml MANIFEST.in` → no
matches.

### Step 4: Confirm the package still imports cleanly

```bash
uv run python -c "import bauer.cli"
```

**Verify**: exits 0, no `ModuleNotFoundError` or `ImportError`.

## Test plan

No new tests needed — this is a pure deletion of unused code. The existing
`tests/test_trigger_manager.py` is deleted along with the module it tested
(since the module tested nothing anyone else depends on, removing coverage
for it is not a coverage regression in any codepath that matters).

Verification: `uv run pytest tests/ -q --tb=short` → all pass, with the test
count reduced by however many tests were in `test_trigger_manager.py` (this
is expected, not a failure).

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `bauer/trigger_manager.py` no longer exists
- [ ] `tests/test_trigger_manager.py` no longer exists
- [ ] `grep -rn "trigger_manager" --include="*.py" bauer tests` → no matches
- [ ] `grep -n "trigger_manager" pyproject.toml MANIFEST.in` → no matches
- [ ] `uv run python -c "import bauer.cli"` exits 0
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 032 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1's grep finds an importer other than `trigger_manager.py` itself and
  its test — the module is not actually dead, this plan's premise is wrong.
- `bauer/daemon.py` has grown a reference to `trigger_manager` since this
  plan was written — re-evaluate whether deletion is still correct (it may
  now be wired in, making this a "keep and use" situation instead).
- `uv run python -c "import bauer.cli"` fails after deletion — something
  transitively depended on the module in a way the grep missed.

## Maintenance notes

- If event-driven daemon triggering (filesystem watch, git commit watch,
  webhook) is wanted in the future, this deleted code is recoverable from
  git history (`git log --all --full-history -- bauer/trigger_manager.py`)
  as a starting point — it was well-structured, just never wired in.
- This deletion is independent of Plan 037 (the 3-scheduler-engine
  consolidation) — `trigger_manager.py` was a *fourth*, entirely separate,
  fully-unused mechanism, not one of the two live/semi-live ones Plan 037
  addresses.
