# Plan 030: Guard the success-path JSON parse in `_ollama_embed`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/embeddings.py`
> If `bauer/embeddings.py` changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none (independent of Plan 029; touches a different file in
  the same subsystem)
- **Category**: bug
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`EmbeddingEngine.embed()` documents an explicit contract at
`bauer/embeddings.py:245`: *"Never raises — falls back to TF-IDF on any
Ollama error."* This contract exists because the whole degrade-to-TF-IDF
design in this module is meant to be *visible* (via `_avisar_degradacao`)
rather than crash-prone — see the module's own history: a silent fallback
previously poisoned the entire index for weeks undetected. `_ollama_embed`
almost honors this: the `httpx.post()` call is wrapped in `try/except`, and
the error-branch `resp.json()` (reading `{"error": "..."}`) is wrapped too —
but the **success**-branch `resp.json()` at line 179 is not. A 200 response
whose body isn't valid JSON (truncated stream, an intercepting proxy
returning HTML, an empty body) raises `ValueError`/`JSONDecodeError` straight
out of `_ollama_embed`, and `embed()` has no try/except of its own around
that call — so the exception propagates out of `embed()`, breaking the
"never raises" contract the rest of the codebase (and the new tests added in
this same feature) relies on. Downstream, `bauer/vector_store.py:155`
(`self._engine.embed(text)`) has no guard either, and the caller in
`bauer/sqlite_session_store.py:590-600` wraps the whole indexing loop in a
blanket `except Exception: pass` — so this crash aborts the rest of that
batch's indexing silently, with nothing surfaced to the operator.

## Current state

- `bauer/embeddings.py` — the file to change.
  - `bauer/embeddings.py:149-187` (`_ollama_embed`) — current code:
    ```python
    def _ollama_embed(text: str, model: str, base_url: str) -> list[float] | None:
        try:
            import httpx
        except Exception:
            return None

        corpo = text
        for _ in range(_CORTES_MAX + 1):
            try:
                resp = httpx.post(
                    f"{base_url}/api/embeddings",
                    json={"model": model, "prompt": corpo},
                    timeout=15.0,
                )
            except Exception:
                return None  # servidor inalcançável: fallback é legítimo
            if resp.status_code == 200:
                return resp.json().get("embedding")
            try:
                erro = str(resp.json().get("error", "")).lower()
            except Exception:
                erro = ""
            if not any(m in erro for m in _ERRO_COMPRIMENTO) or len(corpo) <= 1:
                return None  # não é problema de tamanho: fallback é legítimo
            corpo = corpo[: len(corpo) // 2]
        return None
    ```
    The line `return resp.json().get("embedding")` (inside the
    `if resp.status_code == 200:` branch) is the unguarded call — contrast
    with the very next few lines, where the *error*-branch `resp.json()` call
    already has its own `try/except Exception: erro = ""`.
  - `bauer/embeddings.py:242-261` (`EmbeddingEngine.embed`) — calls
    `_ollama_embed(text, self._ollama_model, self._base_url)` at line 249
    with no surrounding try/except; the docstring at line 245 promises
    "Never raises".
  - `bauer/embeddings.py:334-354` (`_ensure_detected`) — also calls
    `_ollama_embed("test", model, self._base_url)` at line 348, during
    backend auto-detection; the same gap applies there (a malformed 200
    response during the initial probe would raise instead of falling back
    to TF-IDF detection).
- Repo convention already present in this exact function: guard a `resp.json()`
  call with `try: ... except Exception: <safe default>` — see the error-branch
  a few lines below the bug (`bauer/embeddings.py:180-183`). Match that same
  pattern for the success branch.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Test (this file) | `uv run pytest tests/test_embeddings.py -q` | all pass |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/embeddings.py` (`_ollama_embed` function only)
- `tests/test_embeddings.py` (add regression test)

**Out of scope** (do NOT touch, even though they look related):
- `bauer/vector_store.py` — Plan 029 covers a different bug there; do not
  combine the two fixes in one commit.
- `bauer/sqlite_session_store.py`'s blanket `except Exception: pass` — that's
  a separate, lower-priority hardening item; this plan fixes the root cause
  so that broad except no longer needs to catch this case at all.
- Do not change the length-retry logic (`_CORTES_MAX`, the halving loop) —
  that is unrelated, already-correct behavior.

## Git workflow

- Branch: `fix/030-ollama-embed-json-guard`
- Commit message style: conventional commits matching recent history, e.g.
  `fix(memory): guarda o parse do json de sucesso em _ollama_embed`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Wrap the success-path `resp.json()` call

In `bauer/embeddings.py`, inside `_ollama_embed`, change:

```python
            if resp.status_code == 200:
                return resp.json().get("embedding")
```

to:

```python
            if resp.status_code == 200:
                try:
                    return resp.json().get("embedding")
                except Exception:
                    return None  # 200 com corpo inválido: fallback é legítimo
```

This mirrors the existing pattern immediately below it (the error-branch
`try: erro = str(resp.json().get("error", "")).lower() except Exception:
erro = ""`) — same exception-handling shape, same "fallback is the correct
behavior here" comment style used elsewhere in this function.

**Verify**: `uv run python -c "import ast; ast.parse(open('bauer/embeddings.py', encoding='utf-8').read())"` → exits 0.

### Step 2: Confirm no other unguarded `resp.json()` call remains in this function

Re-read the full `_ollama_embed` body after the edit and confirm every
`resp.json()` call (there should be exactly 2: the one just fixed, and the
pre-existing error-branch one) is inside a `try/except`.

**Verify**: `grep -n "resp.json()" bauer/embeddings.py` → both matches are
preceded by a `try:` on an earlier line within the same function (manual
read, not a mechanical grep check).

## Test plan

Add to `tests/test_embeddings.py` (model the fixture/mocking approach after
the existing connection-error and length-refusal tests already in that file
around line 281-349 — reuse whatever `httpx.post` mocking helper those use):

- **New test**: `test_ollama_embed_malformed_200_body_returns_none` — mock
  `httpx.post` to return a response object with `status_code=200` and a
  `.json()` method that raises `ValueError` (or `json.JSONDecodeError`).
  Assert `_ollama_embed(...)` returns `None` (not an exception).
- **New test**: `test_embed_falls_back_to_tfidf_on_malformed_200` — using
  `EmbeddingEngine` with the backend forced/detected to `"ollama"` (match
  however the existing connection-error fallback test sets this up), mock
  the malformed-200 response and assert `engine.embed(text)` returns a
  TF-IDF-length vector (`_VOCAB_SIZE` dimension) instead of raising —
  this is the actual contract ("never raises") being protected.

Verification: `uv run pytest tests/test_embeddings.py -q` → all pass,
including the 2 new tests.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `uv run pytest tests/test_embeddings.py -q` exits 0; the 2 new tests
      listed above exist and pass
- [ ] `uv run pytest tests/ -q --tb=short` exits 0 (no regressions elsewhere)
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] `grep -c "resp.json()" bauer/embeddings.py` → shows both call sites,
      and a manual read confirms both are guarded by `try/except`
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 030 updated

## STOP conditions

Stop and report back (do not improvise) if:

- `bauer/embeddings.py:149-187` (`_ollama_embed`) doesn't match the excerpt
  above — the codebase has drifted since this plan was written.
- A step's verification fails twice after a reasonable fix attempt.
- The fix appears to require touching `vector_store.py` or
  `sqlite_session_store.py` — out of scope for this plan.
- You discover `embed()` already has its own try/except around
  `_ollama_embed()` that you missed — re-check whether the bug still exists
  before proceeding (it would mean this plan is stale).

## Maintenance notes

- If `_ollama_embed` is ever extended to call other Ollama endpoints, the
  same "every `resp.json()` needs a try/except" rule applies — this is now
  the established pattern in this function, not a one-off exception.
- A reviewer should scrutinize: that the fix doesn't swallow the *retry* loop
  logic (a malformed-body 200 should return `None` immediately, not loop
  through `_CORTES_MAX` retries — confirm the new test asserts a single call
  to `httpx.post`, not `_CORTES_MAX + 1` calls).
- Plan 029 (`vector_store.py` stale dimension cache) is in the same
  subsystem but a different file/bug; independent, can land in either order.
