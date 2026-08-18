# Plan 029: Reset the cached embedding dimension when the vector index is emptied

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/vector_store.py`
> If `bauer/vector_store.py` changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`VectorStore` caches the embedding dimension of its index in `self._dim` so
`store()` can reject writes whose embedding dimension doesn't match the rest
of the index (this guard itself is intentional — it exists specifically to
stop the engine's silent TF-IDF fallback from poisoning a dense index, see
the comment at `bauer/vector_store.py:157-163`). The bug is that `delete()`
and `delete_prefix()` never reset `self._dim` back to "unresolved" after
removing rows. If a caller empties the index (e.g. deleting all vectors for a
session) and then writes again — possibly with a different embedding
dimension, e.g. because the embedding backend flipped between Ollama and the
TF-IDF fallback in the meantime — `store()` compares the new embedding
against the **stale** cached dimension and silently returns `0` (rejected,
no exception). `sqlite_session_store.py` calls this in a background thread
inside a blanket `except Exception: pass`, so nothing ever surfaces the
failure — the index silently stops growing. This is the exact "index looks
fine but is actually stale" failure class the dimension guard itself was
built to prevent, reintroduced for the empty-index case.

## Current state

- `bauer/vector_store.py` — SQLite-backed vector store; the file to change.
  - `bauer/vector_store.py:105-125` — `VectorStore.__init__` sets
    `self._dim: int = -1` (`-1` = not yet resolved, `0` = index confirmed
    empty).
  - `bauer/vector_store.py:131-192` (`store`) — computes `vigente =
    self._store_dim()` (the cached/resolved dimension) and rejects the write
    with `return 0` if the new embedding's dimension differs, per the excerpt
    below.
  - `bauer/vector_store.py:220-227` (`delete`) — deletes rows but does not
    touch `self._dim`:
    ```python
    def delete(self, source_id: str, source_type: str) -> int:
        """Delete the vector for (source_id, source_type).  Returns rows deleted."""
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM vectors WHERE source_id = ? AND source_type = ?",
                (source_id, source_type),
            )
        return cur.rowcount
    ```
  - `bauer/vector_store.py:229-243` (`delete_prefix`) — same gap:
    ```python
    def delete_prefix(self, source_id_prefix: str, source_type: str) -> int:
        ...
        with self._connect() as conn:
            cur = conn.execute(
                r"DELETE FROM vectors WHERE source_id LIKE ? ESCAPE '\' AND source_type = ?",
                (like, source_type),
            )
        return cur.rowcount
    ```
  - `_store_dim()` (referenced at `vector_store.py:165`, defined around
    `vector_store.py:398-414` per prior audit) memoizes `self._dim` once
    resolved — `if self._dim >= 0: return self._dim` — and never re-queries
    the DB while the cache holds a non-negative value.
- `bauer/sqlite_session_store.py:598-600` — calls `store_if_absent()` (which
  calls `store()`) from a background indexing loop wrapped in
  `except Exception: pass`; the return value of `store()` (0 = rejected) is
  discarded, so a rejected write leaves no trace anywhere.
- Repo convention: this file already has a "guard" pattern with a `log.warning`
  explaining *why* a write was rejected (see the comment block at
  `vector_store.py:157-176`) — match that documentation style if you add any
  new comment.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Test (this file) | `uv run pytest tests/test_vector_store.py -q` | all pass |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

(From `AGENTS.md` — use `uv run`, not a bare `pytest`/`python`, so the CI-locked
venv from `uv.lock` is used.)

## Scope

**In scope**:
- `bauer/vector_store.py` (the `delete` and `delete_prefix` methods)
- `tests/test_vector_store.py` (add regression test)

**Out of scope** (do NOT touch, even though they look related):
- `bauer/embeddings.py` — a different bug (Plan 030) touches this file; do
  not combine the two fixes in one commit.
- `bauer/sqlite_session_store.py` — the blanket `except Exception: pass` at
  the call site is a separate, lower-priority hardening concern; this plan
  only fixes the root cause in `VectorStore` itself.
- Do not change the dimension-mismatch **rejection** behavior itself (the
  `if vigente and dim != vigente: ... return 0` guard) — that is intentional,
  documented, by-design behavior. Only fix the stale-cache invalidation.

## Git workflow

- Branch: `fix/029-vector-store-dim-cache-invalidation`
- Commit message style: conventional commits, matching recent history, e.g.
  `fix(memory): reseta cache de dimensao do vector_store apos delete`
  (see `git log --oneline -5` for the repo's exact tone/style — Portuguese,
  present tense, no period).
- Do NOT push or open a PR unless the operator instructed it. Per
  `AGENTS.md`, fixes this small go directly to a commit on `master` once
  reviewed — but still make the commit on the branch above and let the
  operator decide whether to merge directly or via PR.

## Steps

### Step 1: Reset `self._dim` in `delete()`

In `bauer/vector_store.py`, in `delete()`, after the `DELETE` executes, reset
the cached dimension so the next `store()` call re-resolves it from the DB
instead of trusting a possibly-stale value:

```python
def delete(self, source_id: str, source_type: str) -> int:
    """Delete the vector for (source_id, source_type).  Returns rows deleted."""
    with self._connect() as conn:
        cur = conn.execute(
            "DELETE FROM vectors WHERE source_id = ? AND source_type = ?",
            (source_id, source_type),
        )
    if cur.rowcount:
        self._dim = -1
    return cur.rowcount
```

Only reset when `cur.rowcount` is truthy (a row was actually deleted) — this
avoids an unnecessary re-resolve on every no-op delete call.

**Verify**: `uv run python -c "import ast; ast.parse(open('bauer/vector_store.py', encoding='utf-8').read())"` → exits 0 (syntax check).

### Step 2: Reset `self._dim` in `delete_prefix()`

Apply the identical pattern to `delete_prefix()`:

```python
def delete_prefix(self, source_id_prefix: str, source_type: str) -> int:
    """Delete all vectors whose source_id starts with the prefix.
    ...
    """
    like = source_id_prefix.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
    with self._connect() as conn:
        cur = conn.execute(
            r"DELETE FROM vectors WHERE source_id LIKE ? ESCAPE '\' AND source_type = ?",
            (like, source_type),
        )
    if cur.rowcount:
        self._dim = -1
    return cur.rowcount
```

Keep the existing docstring (including the `test_search_after_delete`
reference) unchanged — only add the two new lines.

**Verify**: `uv run python -c "import ast; ast.parse(open('bauer/vector_store.py', encoding='utf-8').read())"` → exits 0.

## Test plan

Add to `tests/test_vector_store.py` (model the fixture/setup after the
existing dimension-guard tests already in that file — look for the test that
exercises `store()`'s `return 0` rejection path to match its style):

- **New test**: `test_store_after_delete_all_accepts_new_dimension` —
  1. Create a `VectorStore` (`:memory:`).
  2. `store()` one vector with an 8-dim embedding.
  3. `delete()` that same `(source_id, source_type)` — assert the index is
     now empty (`count() == 0` or equivalent).
  4. `store()` a new vector for a *different* `source_id` with a 16-dim
     embedding.
  5. Assert the write succeeded (non-zero return, and the row is actually
     present — e.g. via `count()` or `list_source_ids()`), not silently
     rejected.
- **New test**: `test_delete_prefix_all_accepts_new_dimension` — same shape,
  using `delete_prefix()` instead of `delete()` (mirrors the existing
  `test_search_after_delete` structure for `delete_prefix`, but adds a
  dimension change after the delete).
- **Regression guard**: keep the existing dimension-mismatch-rejection test
  (whatever currently asserts `store()` returns `0` on a real mismatch)
  passing unchanged — this plan must not weaken that guard.

Verification: `uv run pytest tests/test_vector_store.py -q` → all pass,
including the 2 new tests.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `uv run pytest tests/test_vector_store.py -q` exits 0; the 2 new tests
      listed above exist and pass
- [ ] `uv run pytest tests/ -q --tb=short` exits 0 (no regressions elsewhere)
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] `grep -n "self._dim = -1" bauer/vector_store.py` shows the reset in
      both `delete` and `delete_prefix` (2 matches beyond `__init__`)
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 029 updated

## STOP conditions

Stop and report back (do not improvise) if:

- `bauer/vector_store.py:220-243` (the `delete`/`delete_prefix` bodies)
  don't match the excerpts above — the codebase has drifted since this plan
  was written; re-read the current file and adjust, or report if the
  underlying bug no longer exists.
- `_store_dim()`'s caching logic (`if self._dim >= 0: return self._dim`) has
  changed shape — confirm the fix still targets the right invalidation point
  before proceeding.
- A step's verification fails twice after a reasonable fix attempt.
- The fix appears to require touching `sqlite_session_store.py` or
  `embeddings.py` — that's out of scope for this plan (see "Out of scope").

## Maintenance notes

- If a third method is ever added to `VectorStore` that removes rows (e.g. a
  future `clear()` or `prune()`), it must also reset `self._dim` — this is
  an invariant of the class, not just a one-off fix. Consider adding a
  one-line comment near `self._dim`'s declaration in `__init__` noting that
  any row-removal path must invalidate it.
- A reviewer should scrutinize: that the reset is conditioned on
  `cur.rowcount` (avoids a needless re-resolve query on empty deletes), and
  that no other code path elsewhere reads `self._dim` directly (bypassing
  `_store_dim()`) in a way this fix wouldn't cover.
- Plan 030 (`embeddings.py` — guarding `_ollama_embed`'s success-path JSON
  parse) touches the same subsystem but a different file; the two are
  independent and can land in either order.
