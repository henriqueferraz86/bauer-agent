# Plan 044: Delete the 8 unused `_legacy_kanban_*` methods (124 dead lines)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/tools/kanban.py`
> If the file changed since this plan was written, re-run the grep check in
> Step 1 before proceeding; on a mismatch, treat it as a STOP condition.
>
> **Do this plan before Plan 043** (kanban tool multiplexing) — it removes
> dead code from the same file Plan 043 edits, so doing this first avoids
> Plan 043 having to work around/skip over unreachable code.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`bauer/tools/kanban.py:326-449` defines 8 methods prefixed
`_legacy_kanban_` (`_legacy_kanban_show`, `_legacy_kanban_update_status`,
`_legacy_kanban_complete`, `_legacy_kanban_block`, `_legacy_kanban_unblock`,
`_legacy_kanban_heartbeat`, `_legacy_kanban_comment`, `_legacy_kanban_link`)
— a complete second implementation of the same 7 operations the live,
currently-registered tools provide (`_kanban_show`, `_kanban_complete`,
`_kanban_block`, `_kanban_unblock`, `_kanban_heartbeat`, `_kanban_comment`,
`_kanban_link`, defined later in the same file starting at line 450+).
Nothing calls the `_legacy_` versions: a repo-wide grep for the prefix
finds only their own 8 definitions. This is 124 lines (18% of the file) of
pure unreachable duplicate logic that a reader has to mentally rule out
every time they work on kanban tools. Deleting it is a zero-risk cleanup.

## Current state

- `bauer/tools/kanban.py:326-449` — the dead block, starting:
  ```python
  def _legacy_kanban_show(self, args: dict) -> str:
      ...
  def _legacy_kanban_update_status(self, task_id: str, new_status: str, note: str = "") -> dict:
      ...
  def _legacy_kanban_complete(self, args: dict) -> str:
      ...
  def _legacy_kanban_block(self, args: dict) -> str:
      ...
  def _legacy_kanban_unblock(self, args: dict) -> str:
      ...
  def _legacy_kanban_heartbeat(self, args: dict) -> str:
      ...
  def _legacy_kanban_comment(self, args: dict) -> str:
      ...
  def _legacy_kanban_link(self, args: dict) -> str:
      ...
  ```
  and ending immediately before `bauer/tools/kanban.py:450`
  (`def _kanban_show(self, args: dict) -> str:` — the live version begins
  here).
- Confirmed via `grep -rn "_legacy_kanban_" --include="*.py" bauer tests`:
  every match is one of the 8 definitions above, inside
  `bauer/tools/kanban.py` itself — zero callers anywhere, including in
  `tool_router.py`'s registration block and in `tests/`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Related tests | `uv run pytest tests/ -q -k "kanban"` | all pass |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/tools/kanban.py` (delete lines 326-449 only)

**Out of scope** (do NOT touch, even though they look related):
- The live `_kanban_show`/`_kanban_complete`/etc. methods starting at line
  450+ — unchanged.
- `bauer/tool_router.py`'s kanban registrations — unchanged by this plan
  (Plan 043 handles that separately).
- Any other part of `kanban.py`.

## Git workflow

- Branch: `chore/044-delete-legacy-kanban-methods`
- Commit message style: conventional commits matching recent history, e.g.
  `chore(tools): remove _legacy_kanban_* — 124 linhas sem chamador`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Re-confirm zero callers

```bash
grep -rn "_legacy_kanban_" --include="*.py" bauer tests
```

**Verify**: every match is a `def _legacy_kanban_*` definition inside
`bauer/tools/kanban.py` at approximately lines 326, 353, 366, 374, 384,
392, 410, 429 — no matches anywhere else (no call sites, no test
references). If any other match appears, STOP.

### Step 2: Delete the dead block

Delete `bauer/tools/kanban.py:326-449` (from `def _legacy_kanban_show`
through the end of `_legacy_kanban_link`, exclusive of the blank line(s)
immediately before `def _kanban_show` at line 450 — leave normal
inter-method spacing intact, matching the file's existing style).

**Verify**: `grep -n "_legacy_kanban_" bauer/tools/kanban.py` → no matches.
`uv run python -c "import ast; ast.parse(open('bauer/tools/kanban.py', encoding='utf-8').read())"` → exits 0.

## Test plan

No new tests needed — pure deletion of unreachable code with zero test
coverage referencing it (confirmed in Step 1). Verify the kanban test suite
still passes unchanged.

Verification: `uv run pytest tests/ -q -k "kanban"` → all pass, identical
pass count to before this change (no tests were exercising the deleted
code, so none should be lost either).

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `grep -n "_legacy_kanban_" bauer/tools/kanban.py` → no matches
- [ ] `bauer/tools/kanban.py`'s live `_kanban_*` methods (starting at the
      old line 450, `_kanban_show`) are unchanged and immediately follow
      whatever now precedes them
- [ ] `uv run pytest tests/ -q -k "kanban"` exits 0, same pass count as
      before
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 044 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1's grep finds a caller of any `_legacy_kanban_*` method outside its
  own definition — the code is not actually dead, this plan's premise is
  wrong.
- The line range 326-449 doesn't match the excerpt above after re-reading
  the live file (the codebase has drifted since this plan was written) —
  re-locate the exact dead block by the method names, not by hardcoded line
  numbers, before deleting.

## Maintenance notes

- This deletion is a prerequisite for Plan 043 (kanban tool multiplexing) —
  do this one first so Plan 043's edits to the same file aren't complicated
  by dead code sitting in the middle of it.
- If any of the deleted logic differed meaningfully from its live
  counterpart (e.g. a bugfix applied to `_kanban_complete` but not
  `_legacy_kanban_complete`, or vice versa), that's now lost — but since
  the legacy versions had zero callers, this is not a regression; it's
  removing something already unreachable, not a behavior change.
