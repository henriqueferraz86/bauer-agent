# Plan 034: Delete `bauer/otel.py` and the `bauer traces` command (dead OTLP tracer)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/otel.py bauer/commands/traces_cmd.py bauer/cli.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`bauer/otel.py` implements a full OTLP-style tracer (`Tracer`, `SpanContext`,
`get_tracer()`) — the span-*creation* half of a tracing system. Nothing in
the codebase ever calls it: `grep -rn "SpanContext(|get_tracer(|\.start_span("
--include="*.py" bauer | grep -v "bauer/otel.py"` returns zero results
outside its own file and its own test. The CLI command `bauer traces
list`/`bauer traces show` (`bauer/commands/traces_cmd.py`) only imports the
*read* side (`list_traces`, `load_spans`) — it displays whatever trace file
exists on disk, but since nothing ever writes to that file, the command
always returns empty results in production. This is corroborated by the
codebase's own comments: `bauer/desktop_api.py:11,148,492` explicitly note
"o serve não emite spans OTel" (the serve doesn't emit OTel spans) as the
reason a different metric had to be computed another way. Meanwhile,
distributed tracing in this codebase is actually provided by
`bauer/tracing.py` (Langfuse-backed), which *is* called from `bauer/agent.py`
— that's the live system. `otel.py` is a second, unused, unwired tracer
implementation plus a user-visible CLI command that silently does nothing
useful. Deleting both removes the dead code and an operator-facing footgun
(a command that always looks broken).

## Current state

- `bauer/otel.py` — the file to delete. Defines `Tracer`, `SpanContext`,
  `get_tracer()` (`otel.py:92-160` region, per prior audit) plus the read
  helpers `list_traces`/`load_spans` used by `traces_cmd.py`.
- `bauer/commands/traces_cmd.py` — the file to delete. Registers
  `traces_app = typer.Typer(...)` (`traces_cmd.py:9`), with commands `list`
  (`:12`, imports `from ..otel import list_traces, load_spans` at `:18`) and
  `show` (`:55`, imports `from ..otel import load_spans` at `:61`).
- `bauer/cli.py`:
  - `:85` — `from bauer.commands.traces_cmd import traces_app  # noqa: E402`
  - `:148` — `app.add_typer(traces_app, name="traces", rich_help_panel=PANEL_OBS)`
  Both lines to remove.
- `pyproject.toml:204` — `"bauer.otel",` inside the mypy `module = [...]`
  overrides list (starts around line 152); remove this line.
- `tests/test_otel.py` — the only test file that imports `bauer.otel`
  directly (`from bauer.otel import (...)` at line 13); delete it.
- `bauer/tracing.py` — **do not touch**. This is the separate, live,
  Langfuse-backed tracer actually used by `bauer/agent.py`. It happens to
  also define a function named `get_tracer()` (`tracing.py:220`) — a
  same-name coincidence with `otel.py`'s own `get_tracer()`, not a shared
  implementation. Confirm during Step 1 that you are only touching
  `otel.py`'s symbols, not `tracing.py`'s.
- `bauer/desktop_api.py` — references "OTel"/"otel" only in **comments**
  (lines 11, 148, 492 per prior audit), never imports `bauer.otel`. No code
  change needed here, but these comments corroborate the finding (they
  explain why a different mechanism had to be used because OTel spans are
  never emitted) — leave them as-is, they remain accurate after this
  deletion.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |
| CLI still boots | `uv run python -m bauer.cli --help` | exit 0, `traces` absent from command list |

## Scope

**In scope**:
- Delete `bauer/otel.py`
- Delete `bauer/commands/traces_cmd.py`
- Delete `tests/test_otel.py`
- Edit `bauer/cli.py` (remove the 2 lines above)
- Edit `pyproject.toml` (remove the 1 line above)

**Out of scope** (do NOT touch, even though they look related):
- `bauer/tracing.py` — the live Langfuse tracer; do not modify, do not
  rename its `get_tracer()` even though it shares a name with the deleted
  module's function.
- `bauer/desktop_api.py` — comments referencing OTel remain accurate; no
  code change needed or wanted here.
- `bauer/core/observability/` — an unrelated, live package
  (`AuditLog`/`RunTraceStore`); do not confuse with `bauer/observability.py`
  (a different module, addressed separately in Plan 033) or with
  `bauer/otel.py` (this plan).

## Git workflow

- Branch: `chore/034-delete-otel-traces`
- Commit message style: conventional commits matching recent history, e.g.
  `chore(cli): remove otel.py e bauer traces — tracer nunca emitia spans`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Re-confirm zero real callers of `otel.py`'s span-creation API

```bash
grep -rn "SpanContext(\|get_tracer(\|\.start_span(" --include="*.py" bauer | grep -v "bauer/otel.py"
```

Manually inspect each hit (if any) to confirm it's `bauer/tracing.py`'s
*own*, unrelated `get_tracer()` (not an import from `bauer.otel`) rather
than a real dependency on `otel.py`.

**Verify**: every hit is either inside `tests/test_otel.py` or is
`bauer/tracing.py`'s own local `get_tracer()` definition/call (not an import
of `bauer.otel`'s symbol). If a genuine cross-module dependency on
`bauer.otel`'s `Tracer`/`SpanContext`/`get_tracer` is found elsewhere, STOP.

### Step 2: Delete the module, command, and test

Delete `bauer/otel.py`, `bauer/commands/traces_cmd.py`,
`tests/test_otel.py`.

**Verify**: `git status` shows the 3 deletions.

### Step 3: Remove the CLI registration

In `bauer/cli.py`, remove:
- `from bauer.commands.traces_cmd import traces_app  # noqa: E402`
- `app.add_typer(traces_app, name="traces", rich_help_panel=PANEL_OBS)`

**Verify**: `grep -n "traces_app\|traces_cmd" bauer/cli.py` → no matches.

### Step 4: Remove the mypy override entry

In `pyproject.toml`, remove the line `"bauer.otel",` from the `module =
[...]` list (keep surrounding entries' alphabetical order intact).

**Verify**: `grep -n "bauer.otel" pyproject.toml` → no matches.

### Step 5: Confirm the CLI still boots and `traces` is gone

```bash
uv run python -m bauer.cli --help
```

**Verify**: exits 0; output does not list a `traces` command group.

## Test plan

No new tests needed — this is a pure deletion of unused code and a
never-functional CLI command. Verify the full suite still passes with the
reduced test count (fewer tests, from `test_otel.py`'s removal, is
expected).

Verification: `uv run pytest tests/ -q --tb=short` → all pass.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `bauer/otel.py`, `bauer/commands/traces_cmd.py`,
      `tests/test_otel.py` no longer exist
- [ ] `grep -n "traces_app\|traces_cmd\|bauer.otel" bauer/cli.py pyproject.toml`
      → no matches
- [ ] `uv run python -m bauer.cli --help` exits 0 and lists no `traces`
      command
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 034 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1 finds a real, non-test, non-`tracing.py` dependency on
  `bauer.otel`'s `Tracer`/`SpanContext`/`get_tracer` — the module is not
  fully dead, re-evaluate before deleting.
- `uv run python -m bauer.cli --help` fails after Step 3 — something else
  depends on `traces_app` in a way this plan's grep missed.
- You find that `bauer/tracing.py`'s `get_tracer()` actually imports from or
  delegates to `bauer/otel.py` internally (contradicting the "coincidental
  same name" premise) — re-check before deleting `otel.py`.

## Maintenance notes

- If OTLP-standard tracing (as opposed to Langfuse-backed) is wanted in the
  future, this deleted code is recoverable from git history
  (`git log --all --full-history -- bauer/otel.py`) as a starting point.
- This deletion is independent of Plan 033 (`bauer/observability.py`
  decision) — different module, different (non-)caller, can land in either
  order.
