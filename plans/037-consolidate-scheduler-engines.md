# Plan 037: Consolidate `bauer schedule`/`worker` into the `cron`/`dispatch`/`runtime` stack

> **Executor instructions**: This plan has an investigation step (Step 1)
> before any deletion — do not skip it. Follow the decision rule at the end
> of Step 1. Run every verification command and confirm the expected result
> before moving to the next step. When done, update the status row for this
> plan in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/commands/schedule_cmd.py bauer/commands/worker_cmd.py bauer/commands/cron_cmd.py bauer/commands/runtime_cmd.py bauer/desktop_api.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: L
- **Risk**: MED (public CLI surface removal; `desktop_api.py` has a live
  read dependency that must be migrated, not just deleted)
- **Depends on**: none, but should land after Plan 032 (trigger_manager.py
  deletion — a related but fully-independent 4th scheduler that is pure
  dead code, safe to remove first as a warm-up with zero decision-making
  required)
- **Category**: tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

The Bauer CLI has two independent "run this on a schedule" command groups
with **no relationship to each other**:

- `bauer cron` (`bauer/commands/cron_cmd.py`) — schedules prompts as READY
  tasks via `AutomationStore` (`bauer/automation_store.py`), consumed by
  `bauer/commands/dispatch_cmd.py`'s `TaskDispatcher`, both supervised
  together by `bauer runtime start` (`bauer/commands/runtime_cmd.py:12`,
  `runtime_app = typer.Typer(help="Supervisor always-on: dispatcher, cron,
  outbox e kanban")`) — this is the actively-supervised, production path.
- `bauer schedule` / `bauer worker` (`bauer/commands/schedule_cmd.py`,
  `bauer/commands/worker_cmd.py`) — operate on `bauer/core/runtime/
  scheduler.py`'s `Scheduler` class, against `$BAUER_HOME/memory/runtime/`
  state. **Nothing in `runtime_cmd.py`, `dispatch_cmd.py`, or `cron_cmd.py`
  references `core.runtime.scheduler`** — confirmed via
  `grep -rn "core.runtime.scheduler" bauer` → only
  `bauer/desktop_api.py:889` and the two command files themselves.

A user who runs `bauer schedule add ...` gets a task that the production
`bauer runtime start` supervisor will never pick up — it only executes if
the user *also* separately runs `bauer worker start` (undocumented as a
requirement anywhere). `docs/BAUER_AGENT_RUNTIME_PLANO_COMPLETO.md:1140-1148`
documents `bauer schedule` in isolation with no mention that `bauer cron`
exists as a separate, actually-supervised alternative. `worker_cmd.py`/
`schedule_cmd.py` have **zero CLI-level test coverage**
(`grep -rln "worker_cmd\|worker_app\b" tests/*.py` → 0 files; same for
`schedule_cmd\|schedule_app\b` → 0 files) — the underlying `Scheduler` class
itself is tested (`tests/test_runtime_scheduler.py`), but the CLI layer
around it is not. This plan picks the canonical engine and removes the
other, so there is one obvious answer to "how do I schedule a recurring
task in Bauer."

## Current state

- `bauer/commands/cron_cmd.py:11` — `cron_app = typer.Typer(help=
  "Automacoes duraveis: agenda prompts como tasks READY")`. Commands:
  `create` (`:14`, uses `AutomationStore(workspace).create_job(...)`),
  plus `list`/`tick`/`run`/`pause`/`resume`/`delete`/`daemon` (per prior
  audit inventory).
- `bauer/commands/dispatch_cmd.py:12` — `TaskDispatcher`, claims READY tasks
  created by `cron`.
- `bauer/commands/runtime_cmd.py:12,312-343` — `runtime_app`; `runtime start`
  boots a `RuntimeSupervisor` with `--dispatcher`/`--cron`/`--outbox`/
  `--kanban` toggles (all default-on), i.e. this is the process that
  actually runs `cron`+`dispatch` continuously in production.
- `bauer/commands/schedule_cmd.py` — full file (142 lines), `schedule_app`
  commands `add`/`list`/`show`/`run`/`pause`/`resume`/`delete`, each
  instantiating `Scheduler(root=state_dir)` per-call with
  `state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir")`.
- `bauer/commands/worker_cmd.py` — full file (54 lines), `worker_app`
  commands `start`/`status`, calling `scheduler.start_worker(...)` /
  `WorkerRegistry(root=state_dir).list(...)`.
- `bauer/desktop_api.py:886-901` — the one live, production dependency on
  `core.runtime.scheduler`:
  ```python
  # Tarefas agendadas com falha na ultima execucao.
  failed_scheduled: List[Dict[str, Any]] = []
  try:
      from .core.runtime.scheduler import Scheduler

      for task in Scheduler(root=_runtime_root).list_tasks():
          if task.last_error:
              failed_scheduled.append({
                  "id": task.id,
                  "name": getattr(task, "name", task.id),
                  "last_error": task.last_error,
                  "last_run_id": task.last_run_id,
                  "next_run_at": task.next_run_at,
              })
  except Exception as exc:  # noqa: BLE001
      logger.debug("os home scheduler load failed: %s", exc)
  ```
  This is a **read-only**, best-effort (wrapped in try/except, logs at
  DEBUG on failure) call that populates a "failed scheduled tasks" widget on
  the desktop home dashboard. This is the only thing that would break if
  `Scheduler`/`core/runtime/memory` data stops being populated.
- `docs/BAUER_AGENT_RUNTIME_PLANO_COMPLETO.md:1140-1148` — documents
  `bauer schedule` without mentioning `bauer cron` exists.
- `README.md:33-37` — per prior audit, lists `cron`/`dispatch`/`runtime` and
  `schedule`/`worker` as if they were unrelated separate features, with no
  cross-reference. Update this section too (see Step 3).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Related tests | `uv run pytest tests/ -q -k "scheduler or cron or dispatch or runtime_supervisor"` | all pass |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |
| CLI still boots | `uv run python -m bauer.cli --help` | exit 0 |

## Scope

**In scope**:
- `bauer/commands/schedule_cmd.py`, `bauer/commands/worker_cmd.py`
  (deletion candidates)
- `bauer/cli.py` (registration removal)
- `bauer/desktop_api.py:886-901` (migrate the read to the surviving engine)
- `README.md:33-37`, `docs/BAUER_AGENT_RUNTIME_PLANO_COMPLETO.md:1140-1148`
  (documentation)
- `pyproject.toml` mypy `module = [...]` list, if `bauer.commands.schedule_cmd`
  / `bauer.commands.worker_cmd` appear there

**Out of scope** (do NOT touch, even though they look related):
- `bauer/core/runtime/scheduler.py` itself — do NOT delete the `Scheduler`
  class; other code (`tests/test_runtime_scheduler.py`, potentially other
  kernel-path consumers) may still use it directly even after the CLI
  wrapper is removed. Only remove the CLI command layer
  (`schedule_cmd.py`/`worker_cmd.py`), not the underlying engine, unless
  Step 1 finds zero non-CLI consumers too (see decision rule).
- `bauer/automation_store.py`, `bauer/automation_scheduler.py`,
  `bauer/commands/cron_cmd.py`, `bauer/commands/dispatch_cmd.py`,
  `bauer/commands/runtime_cmd.py` — the surviving/canonical stack; no
  behavior changes here, only documentation additions.
- Plan 032's deletion of `bauer/trigger_manager.py` is a separate, unrelated
  4th mechanism — do not conflate the two; that one has zero callers and
  needs no decision, this one has a live caller that needs migration.

## Git workflow

- Branch: `refactor/037-consolidate-scheduler-engines`
- Commit message style: conventional commits matching recent history.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Confirm the decision and check for other non-CLI consumers

Re-run the grep from "Why this matters" to confirm `core.runtime.scheduler`
still has no consumer inside `runtime_cmd.py`/`dispatch_cmd.py`/`cron_cmd.py`:

```bash
grep -rn "core.runtime.scheduler\|from .core.runtime import scheduler" bauer tests
```

**Decision rule**:
- If the only hits are `bauer/commands/schedule_cmd.py`,
  `bauer/commands/worker_cmd.py`, `bauer/desktop_api.py`, and
  `tests/test_runtime_scheduler.py` → proceed with **removing the CLI
  layer** (`schedule_cmd.py`/`worker_cmd.py`) and keeping `Scheduler` itself
  intact (desktop_api.py still needs it, per the excerpt above).
- If you find evidence that `cron`/`dispatch`/`runtime` is actually the
  *less* mature path (e.g. missing a feature `schedule`/`worker` has, like
  per-worker heartbeat/stale detection via `WorkerRegistry`) — STOP and
  report instead of proceeding; this plan's premise (cron/dispatch/runtime
  is canonical) would need re-evaluation by the operator.

**Verify**: state which hits were found and confirm they match the expected
4 locations above.

### Step 2: Remove the CLI command layer

- Delete `bauer/commands/schedule_cmd.py` and `bauer/commands/worker_cmd.py`.
- In `bauer/cli.py`, remove their `add_typer(...)` registrations and their
  imports (find via `grep -n "schedule_app\|worker_app\|schedule_cmd\|worker_cmd" bauer/cli.py`).
- Remove any `pyproject.toml` mypy override entries for these two modules
  (`grep -n "commands.schedule_cmd\|commands.worker_cmd" pyproject.toml`).

**Verify**: `uv run python -m bauer.cli --help` exits 0 and lists neither
`schedule` nor `worker` as top-level commands.

### Step 3: Migrate `desktop_api.py`'s read path

The `Scheduler(root=_runtime_root).list_tasks()` call at
`desktop_api.py:889` is display-only (a "failed scheduled tasks" widget).
Since `Scheduler` itself is **not** deleted (only its CLI wrapper), the
simplest correct migration is: **leave this call as-is**, pointed directly
at `core.runtime.scheduler.Scheduler` — it doesn't need the CLI layer that
was removed. Confirm this still works:

```bash
uv run python -c "from bauer.core.runtime.scheduler import Scheduler; print(Scheduler)"
```

**Verify**: exits 0, no `ImportError`. If the operator's actual intent
(discovered in Step 1) was to *also* delete the underlying `Scheduler`
engine entirely and route the desktop widget through `AutomationStore`/
`cron`-equivalent data instead, that is a larger follow-up — do not attempt
it in this plan; report it as a suggested next step instead (see
Maintenance notes).

### Step 4: Update documentation

- `README.md:33-37` — add a one-line cross-reference clarifying `bauer cron`
  + `bauer runtime start` is the supervised, production scheduling path;
  remove or clearly demote any parallel mention of `bauer schedule`/
  `bauer worker` (since the commands no longer exist after Step 2).
- `docs/BAUER_AGENT_RUNTIME_PLANO_COMPLETO.md:1140-1148` — update or remove
  the section documenting `bauer schedule` in isolation; if `Scheduler` is
  kept only as an internal desktop-widget data source (not a
  user-facing CLI feature), say so explicitly.

**Verify**: `grep -n "bauer schedule\|bauer worker" README.md docs/BAUER_AGENT_RUNTIME_PLANO_COMPLETO.md`
→ no remaining references to the removed commands (cross-references to the
internal `Scheduler` class, if any remain, are fine).

## Test plan

- Delete any CLI-invocation tests that existed for `schedule`/`worker`
  commands (there should be none per the audit's confirmed 0-test-coverage
  finding — if any are found, that contradicts this plan's premise, STOP
  and re-evaluate).
- Add one test for the `desktop_api.py` migration: confirm the "failed
  scheduled tasks" endpoint/handler still returns correctly with
  `Scheduler` imported directly (no behavior change, just confirming the
  import path survives the CLI-layer deletion — model after existing
  `test_desktop_api.py` tests).
- Keep `tests/test_runtime_scheduler.py` passing unchanged — it tests
  `Scheduler` itself, which is not deleted.

Verification: `uv run pytest tests/ -q --tb=short` → all pass.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `bauer/commands/schedule_cmd.py` and `bauer/commands/worker_cmd.py`
      no longer exist
- [ ] `uv run python -m bauer.cli --help` exits 0, no `schedule`/`worker`
      top-level commands
- [ ] `uv run python -c "from bauer.core.runtime.scheduler import Scheduler; print(Scheduler)"`
      exits 0
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] `README.md` and `docs/BAUER_AGENT_RUNTIME_PLANO_COMPLETO.md` no longer
      reference `bauer schedule`/`bauer worker` as user-facing commands
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 037 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1 finds `core.runtime.scheduler` has consumers beyond the 4 expected
  locations — re-evaluate which engine is actually canonical before
  deleting anything.
- Step 1 finds evidence `schedule`/`worker` has a real feature
  `cron`/`dispatch`/`runtime` lacks (e.g. `WorkerRegistry`'s stale-worker
  detection) that would be lost — report this instead of deleting; the
  operator may want that feature ported to the surviving stack first.
- `uv run python -m bauer.cli --help` fails after Step 2 — something else
  depends on `schedule_app`/`worker_app` in a way this plan's grep missed.

## Maintenance notes

- This plan intentionally does **not** delete `bauer/core/runtime/
  scheduler.py` itself, only its CLI exposure — `desktop_api.py` still
  needs the class. A follow-up could go further (migrate the desktop
  widget's data source to `AutomationStore` and delete `Scheduler`/
  `WorkerRegistry` entirely), but that's a larger, separate piece of work
  with its own risk profile; flag it as a suggested next plan rather than
  attempting it here.
- If a future contributor wants full unification (one engine, one CLI
  group, one state format), that would mean also migrating whatever data
  `core/runtime/memory/` JSONL currently holds into the `AutomationStore`/
  kanban-task-backed format `cron`/`dispatch` uses — a data migration in the
  same spirit as `kanban_migration.py`. Not attempted here; this plan only
  removes the redundant, untested, undocumented CLI surface.
- A reviewer should scrutinize: that no external automation script anyone
  currently has invokes `bauer schedule` or `bauer worker` directly (this
  plan assumes zero real-world usage based on zero test coverage and no
  supervisor wiring — but that's an inference, not a certainty; if the
  operator knows of external usage, this plan should be revised to add a
  deprecation warning period instead of an immediate removal).
