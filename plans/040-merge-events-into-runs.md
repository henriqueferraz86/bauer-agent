# Plan 040: Merge `bauer events tail` into `bauer runs events`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/commands/events_cmd.py bauer/commands/runs_cmd.py bauer/cli.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`bauer events tail` (`bauer/commands/events_cmd.py`) and `bauer runs events`
(`bauer/commands/runs_cmd.py:76-87`) both display entries from the exact
same `EventBus(root=state_dir).list_events(...)` API against the same
default `memory/runtime` state directory. They're two incomplete halves of
one feature: `events tail` supports `--follow`/`--interval` (live polling)
but has no `--run-id` filter (always shows everything); `runs events`
filters by `run_id` but has no `--follow`. A whole top-level CLI noun
(`bauer events`) exists for what's 90% the same call as an existing
subcommand of `runs`. Merging them into one command with both capabilities
removes a top-level noun and gives users the union of both features instead
of having to remember which of the two supports which flag.

## Current state

- `bauer/commands/events_cmd.py` — full file (39 lines):
  ```python
  """Commands for runtime events."""

  from __future__ import annotations

  import json
  import time
  from dataclasses import asdict
  from pathlib import Path

  import typer

  from ..core.events import EventBus
  from ._common import console

  events_app = typer.Typer(help="Inspeciona eventos auditaveis do runtime.")


  @events_app.command("tail")
  def events_tail(
      state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
      limit: int = typer.Option(50, "--limit", "-n", min=1),
      follow: bool = typer.Option(False, "--follow", "-f"),
      interval: float = typer.Option(1.0, "--interval", min=0.1),
  ):
      bus = EventBus(root=state_dir)
      seen: set[str] = set()

      def _print_new() -> None:
          for event in bus.list_events(limit=limit if not seen else None):
              if event.id in seen:
                  continue
              seen.add(event.id)
              console.print(json.dumps(asdict(event), ensure_ascii=False))

      _print_new()
      while follow:
          time.sleep(interval)
          _print_new()
  ```
  Note: `_print_new()` calls `bus.list_events(limit=..., ...)` with **no
  `run_id` filter parameter** — confirm during Step 1 whether
  `EventBus.list_events()` even accepts a `run_id` kwarg (it must, since
  `runs_cmd.py` calls it with one — `list_events(run_id=run_id)`).
- `bauer/commands/runs_cmd.py:76-87` — the `events` subcommand of
  `runs_app`:
  ```python
  @runs_app.command("events")
  def runs_events(
      run_id: str = typer.Argument(...),
      state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
  ):
      events = EventBus(root=state_dir).list_events(run_id=run_id)
      if not events:
          console.print(f"[yellow]Nenhum evento para run:[/yellow] {run_id}")
          return
      for event in events:
          console.print(json.dumps(asdict(event), ensure_ascii=False))
  ```
- `bauer/cli.py` — registers `events_app` at `name="events"` (find the exact
  line via `grep -n "events_app" bauer/cli.py`); `runs_app` is registered
  separately at `name="runs"`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| CLI help | `uv run python -m bauer.cli runs events --help` | exit 0, shows `--run-id` (now optional) and `--follow` |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/commands/runs_cmd.py` (extend `runs_events`)
- `bauer/commands/events_cmd.py` (delete)
- `bauer/cli.py` (remove `events_app` registration/import)
- Tests covering either command's current behavior

**Out of scope** (do NOT touch, even though they look related):
- `bauer/core/events.py`'s `EventBus` class itself — do not change its
  `list_events()` signature beyond what's strictly needed (it may already
  support both `run_id` and `limit` together; verify in Step 1 before
  assuming a change is needed there).
- `bauer/commands/runs_cmd.py`'s other commands (`list`/`show`/`cancel`) —
  unchanged.

## Git workflow

- Branch: `chore/040-merge-events-into-runs`
- Commit message style: conventional commits matching recent history, e.g.
  `chore(cli): funde bauer events em bauer runs events`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Confirm `EventBus.list_events()`'s full signature

```bash
grep -n "def list_events" bauer/core/events.py
```

Read the full signature and confirm it accepts `run_id: str | None = None`
and `limit: int | None = None` together (both `events_cmd.py` and
`runs_cmd.py` already call it with one or the other — confirm both can be
passed simultaneously, or that `run_id=None` means "all runs").

**Verify**: you can state the full parameter list of `list_events()`.

### Step 2: Extend `runs_events` with `--follow`/`--interval`/`--limit`, and make `run_id` optional

In `bauer/commands/runs_cmd.py`, replace `runs_events` with a version that
merges both commands' behavior:

```python
@runs_app.command("events")
def runs_events(
    run_id: str = typer.Argument(None, help="Filtra por run especifica; omitido = todas as runs."),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
    limit: int = typer.Option(50, "--limit", "-n", min=1, help="Maximo de eventos (ignorado com --follow apos a 1a leitura)."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Continua exibindo novos eventos."),
    interval: float = typer.Option(1.0, "--interval", min=0.1, help="Intervalo de poll em segundos com --follow."),
):
    bus = EventBus(root=state_dir)
    seen: set[str] = set()

    def _print_new() -> None:
        events = bus.list_events(run_id=run_id, limit=limit if not seen else None)
        if not events and not seen and not follow:
            console.print(
                f"[yellow]Nenhum evento{f' para run: {run_id}' if run_id else ''}.[/yellow]"
            )
            return
        for event in events:
            if event.id in seen:
                continue
            seen.add(event.id)
            console.print(json.dumps(asdict(event), ensure_ascii=False))

    _print_new()
    while follow:
        time.sleep(interval)
        _print_new()
```

Add the needed imports (`time`) to `runs_cmd.py` if not already present.
Keep the `run_id` argument's existing required-positional *call signature*
compatible for scripts that already pass one — only the "omitted" case
becomes newly valid, not a breaking change to the existing usage.

**Verify**: `uv run python -m bauer.cli runs events --help` → shows
`run_id` as optional, plus `--limit`/`--follow`/`--interval`.
`uv run python -m bauer.cli runs events <some-run-id>` still works exactly
as before (filtered, no follow).

### Step 3: Delete `events_cmd.py` and its registration

Delete `bauer/commands/events_cmd.py`. In `bauer/cli.py`, remove the
`events_app` import and `add_typer(events_app, name="events", ...)` call.

**Verify**: `uv run python -m bauer.cli --help` → no `events` top-level
command; `grep -n "events_app\|events_cmd" bauer/cli.py` → no matches.

## Test plan

- **New test**: `test_runs_events_without_run_id_shows_all` — invoke
  `runs events` (via whatever CLI-test mechanism this repo uses — check
  `tests/test_cli_*.py` or similar for the pattern) with no `run_id`
  argument, against a state dir with events from 2+ different runs; assert
  all events are shown (this is the new capability, previously only
  `events tail` could do this).
- **New test**: `test_runs_events_with_run_id_filters` — confirm existing
  filtered behavior (equivalent to the old `runs_events` test, if one
  exists — reuse/adapt it).
- **New test**: `test_runs_events_follow_polls` — model after any existing
  test for `events_cmd.py`'s `--follow` behavior (find it before deleting
  `events_cmd.py` — port its test logic to `runs_cmd.py`'s test file rather
  than losing coverage).
- Delete `tests/test_events_cmd.py` (or equivalent) only after confirming
  its test cases are represented in the new/updated `runs_cmd.py` tests —
  do not delete coverage, migrate it.

Verification: `uv run pytest tests/ -q --tb=short` → all pass.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `bauer/commands/events_cmd.py` no longer exists
- [ ] `bauer/commands/runs_cmd.py`'s `events` subcommand supports optional
      `run_id`, `--limit`, `--follow`, `--interval`
- [ ] `uv run python -m bauer.cli --help` shows no top-level `events`
      command
- [ ] `uv run pytest tests/ -q --tb=short` exits 0, with `--follow` test
      coverage preserved (not lost from the deleted file)
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 040 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1 finds `EventBus.list_events()` cannot accept `run_id` and `limit`
  together (e.g. mutually exclusive params) — the merge needs a different
  shape than proposed; report instead of forcing an incompatible signature.
- A test exists for `events_cmd.py` that exercises behavior not
  representable in the merged command (e.g. some `events`-specific flag not
  mentioned in "Current state") — report before dropping that coverage.
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- If `README.md` or other docs reference `bauer events tail` directly,
  update those references to `bauer runs events` as a follow-up (not in
  this plan's file scope, but worth a quick grep check).
- A reviewer should confirm the merged command's help text clearly
  documents that omitting `run_id` means "all runs" — this is new behavior
  that didn't exist in either original command alone.
