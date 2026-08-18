# Plan 036: Reconcile the two memory managers exposed under `bauer memory`

> **Executor instructions**: This plan has an investigation step (Step 1)
> that determines which of two paths to take. Follow the decision rule at
> the end of Step 1 before proceeding — do not attempt both paths. Run every
> verification command and confirm the expected result before moving to the
> next step. When done, update the status row for this plan in
> `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/memory_manager.py bauer/core/runtime/memory.py bauer/commands/memory_cmd.py bauer/memory_provider.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED (both paths touch live data formats with existing installs
  on disk)
- **Depends on**: none (related to, but independent of, the
  orchestrator↔`core/runtime` migration already acknowledged in
  `AGENTS.md:98` — this plan is the memory-domain instance of that same
  "two generations coexist" pattern)
- **Category**: tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

Two independent memory-store implementations are both instantiated in the
same file, `bauer/commands/memory_cmd.py:5-6`, and exposed as sibling
subcommands of the same `bauer memory` Typer group:

- `bauer/memory_manager.py`'s `MemoryManager` — a Markdown-file backend
  (`FAILED_ATTEMPTS.md`, `MODEL_EXPERIENCE.md`, etc.), backing
  `memory add-decision`/`add-note`/`search`/... (`memory_cmd.py:23-199`).
- `bauer/core/runtime/memory.py`'s `RuntimeMemoryManager` — a JSONL/
  event-sourced backend (`write`/`search`/`revise`/`expire`, scoped
  `user/company/project/agent/skill`), backing `memory runtime-add`/
  `runtime-list`/`runtime-search`/`runtime-revise`/`runtime-expire`
  (`memory_cmd.py:233-320`).

They store data in different formats (`memory/*.md` files vs.
`$BAUER_HOME/memory/runtime/*.jsonl`) with **no synchronization between
them**. A decision written via `memory add-decision` is invisible to
`memory runtime-search`, and vice versa. `AGENTS.md`'s own "Layout do
pacote" table (`AGENTS.md:53`, "Memória" row) lists only
`decision_memory.py`, `sqlite_session_store.py`, `memory_context.py`,
`embeddings.py` — it doesn't even acknowledge `memory_manager.py`,
`memory_provider.py`, or `core/runtime/memory.py` exist, so there's no
architecture doc explaining which one is canonical. This is confusing for
both human operators and any agent (including Bauer itself) trying to
decide where to write or look up a memory.

## Current state

- `bauer/commands/memory_cmd.py:1-16` — imports both managers:
  ```python
  from ..core.runtime.memory import RuntimeMemoryManager
  from ..memory_manager import MemoryManager
  ```
  `memory_app = typer.Typer(help="Operacoes com memoria Markdown")` — note
  the group's own help text only describes the Markdown half, even though
  it also hosts the `runtime-*` JSONL subcommands.
- `bauer/memory_manager.py` — `MemoryManager`, Markdown-backend, methods
  including `init_files()`, `add_decision`, `add_failure`, `add_note`,
  `search()` (per prior audit, `memory_manager.py:148-224`).
- `bauer/core/runtime/memory.py:42` — `class RuntimeMemoryManager`, with
  `write()` (`:57`), `search()` (`:108`), `revise()` (`:124`), `expire()`
  (`:164`).
- `bauer/memory_provider.py:38` — `class MemoryProvider(ABC)`, a pluggable
  backend interface already designed for exactly this kind of
  consolidation: abstract methods include `prefetch()` (`:49`),
  `sync_turn()` (`:75`), `system_prompt_block()` (`:91`), and more. The file
  contains **6 concrete implementations** of this ABC already (per prior
  audit — `LocalMemoryProvider` and others), each defining its own
  `prefetch`/`sync_turn`/`system_prompt_block`. This ABC is the natural home
  for `RuntimeMemoryManager` to plug into, if that's the direction chosen.
- `bauer/kanban_migration.py` — an existing precedent in this codebase for
  exactly this kind of "reconcile two generations of a store" migration;
  read it as a structural example if Step 1 concludes a data migration is
  needed.
- `AGENTS.md:53` — the "Memória" row of the package-layout table; does not
  mention `memory_manager.py`, `memory_provider.py`, or
  `core/runtime/memory.py` — update this table regardless of which path is
  chosen (see Step 3).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Related tests | `uv run pytest tests/ -q -k "memory"` | all pass |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/commands/memory_cmd.py`
- `bauer/memory_provider.py` (Path A only — adapter registration)
- `bauer/core/runtime/memory.py` (read-only reference, or Path A adapter
  wiring)
- `AGENTS.md` (update the "Memória" row either way)
- Tests for whichever subcommands change behavior

**Out of scope** (do NOT touch, even though they look related):
- `bauer/decision_memory.py`, `bauer/sqlite_session_store.py`,
  `bauer/embeddings.py`, `bauer/vector_store.py` — these are the *session/
  decision* memory subsystem (already documented in `AGENTS.md`), a
  different concern from the `MemoryManager`/`RuntimeMemoryManager` split
  this plan addresses. Do not merge these in.
- Do not change any of the 6 existing `MemoryProvider` implementations'
  behavior (only add a new one, if Path A is chosen).
- Do not delete either backend's on-disk data format without a migration
  path (this is why "just delete one" is not an available path here, unlike
  Plans 032/034/035 — both stores may have live user data).

## Git workflow

- Branch: `refactor/036-unify-memory-managers`
- Commit message style: conventional commits matching recent history.
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Investigate and decide

Read `bauer/memory_provider.py`'s `MemoryProvider` ABC in full (all abstract
methods, not just the ones listed above) and compare its shape against
`RuntimeMemoryManager`'s `write`/`search`/`revise`/`expire`. Answer:

1. Does `RuntimeMemoryManager`'s scoping model (`user/company/project/
   agent/skill`) map cleanly onto `MemoryProvider`'s per-turn lifecycle
   hooks (`prefetch`/`sync_turn`/`system_prompt_block`), or are they solving
   different problems (one is a CLI-driven manual store, the other is an
   automatic per-turn agent memory)?
2. Is `MemoryManager` (Markdown) actively read by the running agent loop
   (`agent.py`) at all, or is it purely a CLI/manual-entry tool that nothing
   automatic consumes? Check `grep -rn "MemoryManager(" bauer/agent.py
   bauer/memory_context.py`.

**Decision rule**:
- If `RuntimeMemoryManager` is meant to be an automatic, per-turn memory
  (like the 6 existing `MemoryProvider`s) → **Path A**: wrap it as a new
  `MemoryProvider` implementation, and treat `memory runtime-*` CLI commands
  as a debugging/inspection surface over that provider (not a second
  independent store).
- If `MemoryManager` (Markdown) and `RuntimeMemoryManager` (JSONL) are both
  genuinely CLI-only, manually-invoked tools with no automatic agent-loop
  consumer (i.e. neither is "the" per-turn memory) → **Path B**: keep both,
  but make the split explicit and documented (rename the CLI group's help
  text, split `bauer memory` into `bauer memory md ...` /
  `bauer memory runtime ...` nested subgroups so the ambiguity is visible
  rather than hidden, and add the missing `AGENTS.md` documentation).
- If investigation shows one store has essentially no real users/data in
  practice (check for existing `memory/*.md` files or
  `$BAUER_HOME/memory/runtime/*.jsonl` files in a real deployment, or ask
  the operator) → treat it like Plans 032/034/035 and propose deletion
  instead, but only after confirming with the operator (this is a
  user-facing data store, unlike the pure-dead-code deletions elsewhere in
  this plan set — do not delete unilaterally).

**Verify**: you can state, in one paragraph, which path was chosen and the
file:line evidence that justified it.

### Step 2a (Path A): Register `RuntimeMemoryManager` as a `MemoryProvider`

- Create a new class (e.g. `RuntimeMemoryProvider`) in `bauer/memory_provider.py`
  implementing the ABC's required methods, delegating to a
  `RuntimeMemoryManager` instance internally.
- Wire it into wherever `MemoryProvider` implementations are registered/
  selected (find the existing registration point — likely `plugin_hooks.py`
  or a config-driven selector, per prior audit's mention of
  `load_memory_providers()`).
- Update `memory_cmd.py`'s `runtime-*` subcommands to operate through the
  new provider abstraction where practical, or leave them as thin
  inspection tools over the same underlying `RuntimeMemoryManager` (your
  call, based on what Step 1 found) — but the goal is that the *agent loop*
  now has one coherent way to reach this data, not two independent silos.

**Verify**: `uv run pytest tests/ -q -k "memory_provider"` passes with the
new provider covered.

### Step 2b (Path B): Make the split explicit, don't merge

- In `bauer/commands/memory_cmd.py`, nest the two halves under clearly
  named subgroups (e.g. `bauer memory md <cmd>` for the Markdown backend,
  `bauer memory runtime <cmd>` for the JSONL backend — Typer supports this
  via `memory_app.add_typer(md_app, name="md")` /
  `memory_app.add_typer(runtime_app, name="runtime")`), preserving each
  underlying command's current behavior exactly.
- Update `memory_app`'s help text to describe both backends and when to use
  each, instead of the current text which only mentions "Markdown".

**Verify**: `bauer memory --help` (or `uv run python -m bauer.cli memory
--help`) shows both subgroups clearly, with no change to either backend's
actual read/write behavior (`uv run pytest tests/ -q -k "memory"` still
passes unchanged in content, only CLI invocation paths shift if command
nesting changed — update tests accordingly if so).

### Step 3: Update `AGENTS.md`

Regardless of path, add a row (or extend the existing "Memória" row) in
`AGENTS.md`'s package-layout table (`AGENTS.md:43-61`) covering
`memory_manager.py`, `memory_provider.py`, and `core/runtime/memory.py`,
explaining the relationship chosen in Step 1/2. If Path A was chosen, also
extend the existing "Custódia ≠ governança" / "ainda convive" note style
(`AGENTS.md:96-99`) — this is now a second instance of the same
old-generation/new-generation pattern the doc already tracks for
orchestrator vs. `core/runtime`.

**Verify**: `grep -n "memory_manager\|memory_provider\|core/runtime/memory" AGENTS.md`
→ shows the new/updated documentation.

## Test plan

- **Path A**: add tests for the new `MemoryProvider` implementation,
  modeled after the existing provider tests (find one of the 6 existing
  providers' test file as the structural pattern). Confirm existing
  `runtime-*` CLI command tests (if any) still pass, or are updated to
  reflect the new wiring.
- **Path B**: add/update CLI invocation tests for the new nested subgroup
  paths (`bauer memory md ...` / `bauer memory runtime ...`), confirming
  each still performs the identical underlying operation as before.
- Either way: no existing test that exercises `MemoryManager` or
  `RuntimeMemoryManager` directly (not through the CLI) should need to
  change, since neither backend's internal behavior changes in this plan.

Verification: `uv run pytest tests/ -q -k "memory"` → all pass. Then
`uv run pytest tests/ -q --tb=short` for the full suite.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] Step 1's decision is recorded (commit message or code comment) with
      file:line evidence
- [ ] `AGENTS.md` documents the relationship between `memory_manager.py`,
      `memory_provider.py`, and `core/runtime/memory.py`
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 036 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1's investigation is inconclusive after reading both files in full —
  report your partial findings rather than guessing a path.
- You find that `MemoryManager` or `RuntimeMemoryManager` has real
  production data on disk in a way that makes either path risky without an
  explicit migration step (e.g. non-trivial `memory/*.md` content, or a
  populated `memory/runtime/*.jsonl`) — report this and propose a
  `kanban_migration.py`-style migration plan instead of proceeding directly.
- Path A's wiring would require changing the `MemoryProvider` ABC's method
  signatures (affecting the other 5 existing implementations) — that's a
  larger blast radius than this plan scopes; report instead of improvising.

## Maintenance notes

- Whichever path is chosen, a reviewer should scrutinize whether this
  changes any *default* behavior for existing installs (e.g. if Path A
  makes `RuntimeMemoryManager` a provider that's now active-by-default,
  confirm that doesn't silently start writing/reading data nobody
  configured).
- This is the memory-domain sibling of two other already-tracked
  migrations: task-store (Plan 024/spike, referenced in
  `plans/023-auditoria-completa-2026-07.md` item #10) and orchestrator vs.
  `core/runtime` (`AGENTS.md:96-99`). If a future contributor is doing a
  broader "finish the core/runtime migration" pass, this plan's outcome
  should be referenced alongside those two.
