# Plan 039: Extract the triplicated `_parse_last` time-window parser into a shared helper

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/commands/audit_cmd.py bauer/commands/perf_cmd.py bauer/commands/skills_cmd.py bauer/commands/_common.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW (helper extraction) — the command-group-merge idea from the
  original audit is explicitly NOT part of this plan, see Scope
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

The exact same `_parse_last(value) -> datetime | None` time-window parser
(`"24h" / "7d" / "30m" / "2w"` → cutoff datetime) is copy-pasted **three**
times: `bauer/commands/audit_cmd.py:29-42`,
`bauer/commands/perf_cmd.py:26-37`, and `bauer/commands/skills_cmd.py:67-78`.
They have already drifted: `audit_cmd.py` and `perf_cmd.py` both correctly
use `datetime.now(timezone.utc)` (with an explicit comment explaining *why*
— run timestamps are UTC, using naive local time would miscompute the
window by the timezone offset), but `skills_cmd.py`'s copy uses
`datetime.now()` (naive, local time) — the exact bug the other two copies'
own comments warn against. This is the drift that inevitably happens when
one function is maintained in three places: a bugfix applied to two copies
silently didn't reach the third. Consolidating into one shared helper fixes
this real timezone bug in `skills_cmd.py` as a side effect, and prevents
future fixes from needing to be applied three times.

## Current state

- `bauer/commands/audit_cmd.py:29-42` (correct, UTC-aware):
  ```python
  def _parse_last(last: str) -> "datetime | None":
      """'24h' / '7d' / '30m' / '2w' → datetime de corte UTC-aware. Vazio → None.

      UTC (não naive local): os timestamps das runs são UTC; usar now() local
      erraria o corte da janela pelo offset do fuso."""
      if not last:
          return None
      m = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", last.lower())
      if not m:
          raise typer.BadParameter("Use formatos como 24h, 7d, 30m, 2w.")
      n, unit = int(m.group(1)), m.group(2)
      delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n),
               "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
      return datetime.now(timezone.utc) - delta
  ```
- `bauer/commands/perf_cmd.py:26-37` — byte-for-byte equivalent logic
  (comment reworded, same UTC-aware behavior).
- `bauer/commands/skills_cmd.py:67-78` — **divergent, buggy copy**:
  ```python
  def _parse_last(value: str) -> datetime:
      match = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", value.lower())
      if not match:
          raise typer.BadParameter("Use formatos como 24h, 7d, 2w.")
      amount, unit = int(match.group(1)), match.group(2)
      delta = {
          "m": timedelta(minutes=amount),
          "h": timedelta(hours=amount),
          "d": timedelta(days=amount),
          "w": timedelta(weeks=amount),
      }[unit]
      return datetime.now() - delta   # BUG: naive local time, not UTC
  ```
  Note also: this version doesn't handle the empty-string → `None` case the
  other two do, and its return type annotation is `datetime` (not
  `datetime | None`) — callers in `skills_cmd.py` may rely on this
  never-None behavior; check call sites before changing the signature (see
  Step 2).
- `bauer/commands/_common.py` — the existing shared-helpers module for
  `bauer/commands/*.py` (imported by `audit_cmd.py`, `perf_cmd.py`, and
  others for `console` and constants) — the natural home for the
  consolidated helper.
- `bauer/commands/audit_cmd.py:45-51` (`_emit`) and
  `bauer/commands/perf_cmd.py:40-46` (`_emit_json`) — a second, smaller
  duplication (JSON-to-file-or-stdout output) noted by the audit; included
  in this plan's scope since it's adjacent and trivial once you're already
  editing these two files (see Step 3).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Related tests | `uv run pytest tests/ -q -k "audit_cmd or perf_cmd or skills_cmd or benchmark_cmd"` | all pass |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/commands/_common.py` (add the shared helper(s))
- `bauer/commands/audit_cmd.py`, `bauer/commands/perf_cmd.py`,
  `bauer/commands/skills_cmd.py` (replace local `_parse_last` with the
  shared import)
- `bauer/commands/benchmark_cmd.py` — check whether it has its own
  time-window parsing needs and would benefit from the same helper (per
  prior audit, `benchmark_cmd.py` follows the same run+report shape; verify
  during Step 1 whether it actually duplicates `_parse_last` too, or has a
  different need)
- `tests/test_commands_*.py` or wherever `_parse_last` is currently tested
  (add regression test for the `skills_cmd.py` timezone fix)

**Out of scope** (do NOT touch, even though they look related):
- **Do NOT merge the `audit`/`perf`/`benchmark` command groups themselves**
  into one CLI noun. The original audit finding flagged this as a
  lower-confidence, separate follow-up decision (the three measure
  genuinely different things — quality score, latency, scenario pass/fail —
  and merging the *commands* is a UX call for the maintainer, not implied by
  this refactor). This plan only deduplicates the shared parsing/output
  helpers underneath them.
- Do not change `_parse_last`'s accepted format strings (`m`/`h`/`d`/`w`) or
  error message wording beyond what's needed to unify the three copies.

## Git workflow

- Branch: `refactor/039-dedupe-report-helpers`
- Commit message style: conventional commits matching recent history, e.g.
  `refactor(commands): unifica _parse_last (corrige bug de timezone no skills_cmd)`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Check `benchmark_cmd.py` for the same duplication

```bash
grep -n "_parse_last\|datetime.now" bauer/commands/benchmark_cmd.py
```

**Verify**: note whether `benchmark_cmd.py` has its own copy (include it in
Step 2 if so) or doesn't need this helper at all (per prior audit,
`benchmark_cmd.py`'s `run`/`report` shape is similar but may not use a
time-window filter — confirm rather than assume).

### Step 2: Add the shared helper to `_common.py`

In `bauer/commands/_common.py`, add:

```python
def parse_last(last: str) -> "datetime | None":
    """'24h' / '7d' / '30m' / '2w' → datetime de corte UTC-aware. Vazio → None.

    UTC (não naive local): os timestamps das runs são UTC; usar now() local
    erraria o corte da janela pelo offset do fuso.
    """
    if not last:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", last.lower())
    if not m:
        raise typer.BadParameter("Use formatos como 24h, 7d, 30m, 2w.")
    n, unit = int(m.group(1)), m.group(2)
    delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n),
             "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
    return datetime.now(timezone.utc) - delta
```

(Named `parse_last`, not `_parse_last`, since it's now a public shared
helper rather than a private module-level function — match whatever naming
convention `_common.py`'s other exports already use; if everything there is
prefixed `_` regardless of shared use, keep the `_` prefix for consistency
instead.) Add the necessary imports (`re`, `datetime`, `timedelta`,
`timezone`, `typer`) to `_common.py` if not already present.

**Verify**: `uv run python -c "from bauer.commands._common import parse_last"`
(adjust name if you kept the `_` prefix) → exits 0.

### Step 3: Replace the three (or four) local copies

In each of `audit_cmd.py`, `perf_cmd.py`, `skills_cmd.py` (and
`benchmark_cmd.py` if Step 1 found a copy there):
- Delete the local `_parse_last` function body.
- Add `from ._common import parse_last as _parse_last` (aliasing preserves
  every existing call site's spelling — `_parse_last(...)` — with zero
  other line changes needed in the file).
- For `skills_cmd.py` specifically: since the shared helper returns
  `datetime | None` (unlike its old `datetime`-only signature), check every
  call site of `_parse_last` in this file
  (`grep -n "_parse_last(" bauer/commands/skills_cmd.py`) and confirm each
  one already handles a `None` result sensibly, or add a guard where
  needed. This is the one place behavior could visibly change (empty-string
  input now returns `None` instead of raising/erroring differently) — read
  each call site before assuming it's a no-op.

Also fold `audit_cmd.py`'s `_emit` and `perf_cmd.py`'s `_emit_json` into one
shared `emit_json(payload, fmt_or_output, ...)` helper in `_common.py` if
their signatures are compatible after inspection — if they differ in a
load-bearing way (e.g. `audit_cmd.py`'s takes a `fmt` parameter the other
doesn't), leave them separate rather than forcing an awkward unification;
this part of the plan is lower priority than the `_parse_last` fix.

**Verify**: `grep -rn "^def _parse_last" bauer/commands/` → no matches
(all three/four local definitions removed).

## Test plan

- **New regression test**: `test_parse_last_is_utc_aware` in whichever test
  file covers `skills_cmd.py`'s command behavior — assert the result of
  `parse_last("1h")` has `tzinfo` set (was previously naive for
  `skills_cmd.py`'s copy specifically). Model after any existing test for
  `audit_cmd.py`'s or `perf_cmd.py`'s time-window filtering, if one exists.
- **Existing tests**: confirm all pre-existing tests exercising `audit`,
  `perf`, `skills`, `benchmark` report/filter commands still pass — these
  are the regression guard that the refactor didn't change filtering
  behavior for the two already-correct copies.

Verification: `uv run pytest tests/ -q -k "audit_cmd or perf_cmd or skills_cmd or benchmark_cmd"`
→ all pass, including the new test.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `grep -rn "^def _parse_last" bauer/commands/` → no matches (single
      shared definition in `_common.py`)
- [ ] `skills_cmd.py`'s time-window filtering now uses UTC-aware datetimes
      (verified by the new regression test)
- [ ] `uv run pytest tests/ -q -k "audit_cmd or perf_cmd or skills_cmd or benchmark_cmd"` exits 0
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 039 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 3's call-site check in `skills_cmd.py` finds a place that would
  break on receiving `None` where it previously never could — fix that
  call site as part of this plan (it's in scope, it's the direct
  consequence of the consolidation), but if the fix isn't obvious/safe,
  report instead of guessing.
- `benchmark_cmd.py` (Step 1) turns out to have a materially different
  time-window need that doesn't fit the shared helper — leave it
  unconsolidated and note why, rather than forcing a bad fit.
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- The command-group-merge question (`audit`/`perf`/`benchmark` → one CLI
  noun) is explicitly deferred — see "Out of scope". If a future plan
  attempts that merge, this plan's shared `_common.py` helper is a
  prerequisite that makes it easier (one parsing implementation to build
  the merged UI around, not three).
- A reviewer should scrutinize the `skills_cmd.py` behavior change
  specifically — it's the one place this refactor is not purely mechanical
  (it fixes a real bug), so confirm the fix is correct and doesn't
  regress any skills-metrics filtering that was accidentally relying on the
  old naive-local-time behavior.
