# Plan 035: Delete `bauer/feedback_store.py` (dead wrapper, zero real callers)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/feedback_store.py bauer/learning_engine.py`
> If either file changed since this plan was written, re-run the grep check
> in Step 1 before proceeding; on a mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`bauer/feedback_store.py` defines `FeedbackStore`, a thin (~60-line)
convenience wrapper around `MemoryManager` for recording model
failures/successes/preferences. Its own docstring says it exists to
"persist data for the `LearningEngine`" — but `grep -n "FeedbackStore("
bauer` (excluding the file itself) finds zero real callers: it's
instantiated and exercised only by `tests/test_feedback_store.py` (6 tests),
never by `bauer/learning_engine.py` or `bauer/self_tuner.py` — the modules
that would plausibly be its consumers. This is a small dead-code item, but
it adds to a real problem the broader audit flagged: this codebase already
has ambiguity about which of several memory-writing entry points to use
(`MemoryManager` directly, `RuntimeMemoryManager`, `MemoryProvider`,
`decision_memory.py`) — see Plan 036. An unused wrapper that *sounds* like
the right place to record learning feedback, but isn't actually wired to
anything, is exactly the kind of dead end that wastes a future contributor's
time. Delete it.

## Current state

- `bauer/feedback_store.py` — the file to delete. Full public surface:
  `record_model_failure`, `record_model_success`, `record_preference`
  (wrapping `self.mm = MemoryManager(memory_dir)`). Module docstring:
  ```python
  """FeedbackStore — registra eventos de aprendizado (Fase 7).

  Wrapper de conveniencia sobre MemoryManager para registrar falhas,
  sucessos e preferencias de forma consistente.

  Nao analisa nem recomenda — apenas persiste dados para o LearningEngine.
  """
  ```
- `tests/test_feedback_store.py` — the only place `FeedbackStore` is ever
  instantiated (confirmed: `grep -n "FeedbackStore(" bauer` outside
  `feedback_store.py` → zero hits; `grep -rn "FeedbackStore" bauer tests` →
  only `bauer/feedback_store.py` and `tests/test_feedback_store.py`).
- `bauer/learning_engine.py`, `bauer/self_tuner.py` — confirmed to **not**
  import or call `FeedbackStore` (these are the modules whose job, per the
  docstring, would plausibly involve consuming what `FeedbackStore` writes).
- `pyproject.toml` — confirmed `"bauer.feedback_store"` is **not** present
  in the mypy `module = [...]` overrides list — no cleanup needed there.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- Delete `bauer/feedback_store.py`
- Delete `tests/test_feedback_store.py`

**Out of scope** (do NOT touch, even though they look related):
- `bauer/memory_manager.py` — the module `FeedbackStore` wrapped; it has
  other real callers and must not be touched by this plan (Plan 036
  addresses its broader consolidation with `RuntimeMemoryManager`
  separately).
- `bauer/learning_engine.py`, `bauer/self_tuner.py` — do not add new calls
  to `MemoryManager` here to "replace" `FeedbackStore`'s intended purpose;
  that would be a feature addition, not the dead-code removal this plan
  scopes. If the maintainer wants failure/success recording wired into
  `LearningEngine`, that's a separate, explicit follow-up.

## Git workflow

- Branch: `chore/035-delete-feedback-store`
- Commit message style: conventional commits matching recent history, e.g.
  `chore(memory): remove feedback_store.py — wrapper sem chamador real`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Re-confirm zero real callers

```bash
grep -rn "FeedbackStore" --include="*.py" bauer tests
```

**Verify**: output is exactly 2 files — `bauer/feedback_store.py` (its own
class definition) and `tests/test_feedback_store.py` (its own tests). If any
other file appears, STOP.

### Step 2: Delete the module and its test

Delete `bauer/feedback_store.py` and `tests/test_feedback_store.py`.

**Verify**: `git status` shows both files deleted.

### Step 3: Confirm the package still imports cleanly

```bash
uv run python -c "import bauer.cli; import bauer.learning_engine; import bauer.self_tuner"
```

**Verify**: exits 0, no `ImportError`.

## Test plan

No new tests needed — pure deletion of unused code with no other
dependents. Verify the full suite still passes with the reduced test count.

Verification: `uv run pytest tests/ -q --tb=short` → all pass.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `bauer/feedback_store.py` and `tests/test_feedback_store.py` no
      longer exist
- [ ] `grep -rn "FeedbackStore" --include="*.py" bauer tests` → no matches
- [ ] `uv run python -c "import bauer.cli; import bauer.learning_engine; import bauer.self_tuner"` exits 0
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 035 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1's grep finds an importer other than `feedback_store.py` itself and
  its test — the module is not actually dead, this plan's premise is wrong.
- `uv run python -c "..."` in Step 3 fails — something transitively
  depended on the module in a way the grep missed.

## Maintenance notes

- If failure/success/preference recording for the learning loop is wanted
  in the future, this deleted code is recoverable from git history
  (`git log --all --full-history -- bauer/feedback_store.py`) as a starting
  point — but at that point, wire it directly into `learning_engine.py`
  rather than resurrecting an unused intermediate wrapper.
- Independent of Plan 036 (memory-manager consolidation) — can land in
  either order, but if Plan 036 changes `MemoryManager`'s constructor
  signature, do this deletion first to avoid an unnecessary merge conflict
  in a file about to be deleted anyway.
