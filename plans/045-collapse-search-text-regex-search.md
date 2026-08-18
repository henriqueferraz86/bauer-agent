# Plan 045: Make `search_text` delegate to `regex_search` instead of duplicating it

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/tools/fs.py`
> If the file changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`bauer/tools/fs.py`'s `_search_text` (`:215-249`) and `_regex_search`
(`:367-411`) are near-identical implementations: both walk
`p.rglob("*")` (or a single file), read text, iterate
`text.splitlines()`, match each line, format results as
`f"{rel}:{i}: {line.strip()}"`, cap output at `_MAX_SEARCH_RESULTS`, and
share the exact same "no results" message shape. The only functional
difference is the match test: `pattern.lower() in line.lower()` (plain
substring) vs. `compiled.search(line)` (regex) — i.e. `_search_text`'s
behavior is exactly what `_regex_search(re.escape(pattern), flags="i")`
already produces. Two ~35-line implementations doing one job means a bug
fix or feature addition (e.g. a new result-formatting tweak) has to be
applied twice, and already has to be reasoned about twice by anyone reading
this file. Collapsing `_search_text` into a thin delegation to
`_regex_search` removes the duplication with zero behavior change to
either tool's model-facing contract (both tool names, schemas, and
descriptions stay as they are — only the internal implementation of one
changes to call the other).

## Current state

- `bauer/tools/fs.py:215-249` (`_search_text`) — full current body:
  ```python
  def _search_text(self, args: dict) -> str:
      path = args.get("path", ".")
      pattern = args.get("pattern")

      if not pattern:
          raise ToolError("search_text requer 'pattern'.")

      p = self._sandbox(str(path))
      if not p.exists():
          raise ToolError(f"Nao encontrado: '{path}'")

      files = [p] if p.is_file() else sorted(p.rglob("*"))
      results: list[str] = []

      for f in files:
          if not f.is_file():
              continue
          try:
              text = f.read_text(encoding="utf-8", errors="replace")
          except OSError:
              continue
          for i, line in enumerate(text.splitlines(), 1):
              if pattern.lower() in line.lower():
                  try:
                      rel = f.relative_to(self.workspace)
                  except ValueError:
                      rel = f
                  results.append(f"{rel}:{i}: {line.strip()}")
                  if len(results) >= _MAX_SEARCH_RESULTS:
                      results.append(f"... (limite de {_MAX_SEARCH_RESULTS} resultados atingido)")
                      return "\n".join(results)

      if not results:
          return f"Nenhum resultado para '{pattern}' em '{path}'"
      return "\n".join(results)
  ```
- `bauer/tools/fs.py:367-411` (`_regex_search`) — full current body:
  ```python
  def _regex_search(self, args: dict) -> str:
      pattern = args.get("pattern")
      base = args.get("path", ".")
      flags_str = str(args.get("flags", "")).lower()
      if not pattern:
          raise ToolError("regex_search requer 'pattern'.")
      re_flags = 0
      if "i" in flags_str:
          re_flags |= re.IGNORECASE
      if "m" in flags_str:
          re_flags |= re.MULTILINE
      if "s" in flags_str:
          re_flags |= re.DOTALL
      try:
          compiled = re.compile(pattern, re_flags)
      except re.error as exc:
          raise ToolError(f"Regex inválida: {exc}") from exc

      p = self._sandbox(str(base))
      if not p.exists():
          raise ToolError(f"Nao encontrado: '{base}'")
      files = [p] if p.is_file() else sorted(p.rglob("*"))
      results: list[str] = []

      for f in files:
          if not f.is_file():
              continue
          try:
              text = f.read_text(encoding="utf-8", errors="replace")
          except OSError:
              continue
          for i, line in enumerate(text.splitlines(), 1):
              if compiled.search(line):
                  try:
                      rel = f.relative_to(self.workspace)
                  except ValueError:
                      rel = f
                  results.append(f"{rel}:{i}: {line.strip()}")
                  if len(results) >= _MAX_SEARCH_RESULTS:
                      results.append(f"... (limite de {_MAX_SEARCH_RESULTS} resultados atingido)")
                      return "\n".join(results)

      if not results:
          return f"Nenhum resultado para regex '{pattern}' em '{base}'"
      return "\n".join(results)
  ```
  Note the one message-text difference: `_search_text`'s "no results"
  message says `"Nenhum resultado para '{pattern}'"` (no mention of
  "regex"), `_regex_search`'s says `"Nenhum resultado para regex
  '{pattern}'"`. Preserve `_search_text`'s original wording in the
  delegation (see Step 1) so its output contract doesn't change for
  existing callers/tests.
- Both `search_text` and `regex_search` remain **separate, model-facing
  tool names** in `bauer/tool_router.py` — this plan does not remove either
  tool from the catalog or change their schemas, only deduplicates the
  underlying implementation.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Related tests | `uv run pytest tests/ -q -k "search_text or regex_search"` | all pass |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/tools/fs.py` (`_search_text` body only)
- Tests exercising `search_text`

**Out of scope** (do NOT touch, even though they look related):
- `_regex_search` — unchanged; `_search_text` will call it, not the other
  way around.
- `bauer/tool_router.py` — no registration changes; both tools keep their
  current names, descriptions, and args schemas.
- Do not remove `search_text` as a model-facing tool — the original audit
  flagged this as an *option* but this plan implements the lower-risk
  "collapse the implementation, keep both tool names" path, since
  `search_text`'s plain-substring semantics (no regex special characters to
  worry about) may be genuinely easier for some models to use correctly
  than always requiring `regex_search` with `re.escape`.

## Git workflow

- Branch: `refactor/045-collapse-search-text-regex-search`
- Commit message style: conventional commits matching recent history, e.g.
  `refactor(tools): search_text delega para regex_search (implementacao duplicada)`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Replace `_search_text`'s body with a delegation

In `bauer/tools/fs.py`, replace `_search_text` (lines 215-249) with:

```python
def _search_text(self, args: dict) -> str:
    pattern = args.get("pattern")
    if not pattern:
        raise ToolError("search_text requer 'pattern'.")
    result = self._regex_search({
        "pattern": re.escape(str(pattern)),
        "path": args.get("path", "."),
        "flags": "i",
    })
    # _regex_search's "no results" message says "regex '{pattern}'" —
    # search_text's original contract says just "'{pattern}'" (no "regex"
    # wording, and using the raw pattern, not the escaped one).
    no_result_regex = f"Nenhum resultado para regex '{re.escape(str(pattern))}'"
    if result.startswith(no_result_regex):
        path = args.get("path", ".")
        return f"Nenhum resultado para '{pattern}' em '{path}'"
    return result
```

This preserves `_search_text`'s exact original error-raising behavior
(missing `pattern` → `ToolError("search_text requer 'pattern'.")`) and its
exact original "no results" message wording, while eliminating the
duplicated file-walking/matching loop by delegating to `_regex_search`.

**Alternative, simpler approach** (prefer this if the message-matching
string comparison above feels fragile): instead of string-matching
`_regex_search`'s output to detect the no-results case, factor the shared
file-walking/matching loop out of `_regex_search` into a private helper
(e.g. `_search_lines(self, base, matcher: Callable[[str], bool]) ->
list[str]`) that both `_search_text` and `_regex_search` call, each
supplying their own matcher function and their own message wording. This
avoids string-sniffing entirely. Use whichever approach you find cleaner —
the string-matching version above is given as the minimal-diff option, but
the shared-helper refactor is arguably more correct. If you choose the
shared-helper path, apply the equivalent extraction to `_regex_search` too
(both functions should end up calling the same loop).

**Verify**: `uv run python -c "import ast; ast.parse(open('bauer/tools/fs.py', encoding='utf-8').read())"` → exits 0.

### Step 2: Confirm both tools' output is unchanged for existing behavior

Manually trace through: `_search_text({"pattern": "foo.bar", "path": "x"})`
— the old implementation matched `"foo.bar"` as a literal substring
(`.` is not special in plain substring matching); the new delegation calls
`_regex_search` with `re.escape("foo.bar")` = `"foo\\.bar"`, which as a
regex also matches only the literal substring `"foo.bar"` — same result.
Confirm this equivalence holds for a pattern containing regex metacharacters
(`.`, `*`, `[`, `(`, etc.) since that's exactly the case `re.escape` exists
to handle correctly.

**Verify**: no command — this is a manual correctness check to do before
running the automated tests in Step 3, since the shape of a subtle bug here
would be "works for simple patterns, breaks for patterns with special
characters."

## Test plan

- Existing `search_text` tests must all still pass unchanged — they are the
  regression guard that this refactor preserves`_search_text`'s external
  contract.
- **New test**: `test_search_text_pattern_with_regex_metacharacters` —
  search for a pattern containing characters like `.`, `(`, `)`, `[`, `]`
  (e.g. `"config.yaml"` or `"foo(bar)"`) in a file that contains that exact
  literal substring; assert it's found (this is the case that would break
  if `re.escape` were missing or wrong).
- **New test**: `test_search_text_no_results_message_unchanged` — assert
  the "no results" message text still matches `_search_text`'s original
  wording (not `_regex_search`'s "regex" wording) — this is the specific
  contract-preservation detail Step 1 handles.
- Confirm `_regex_search`'s own existing tests still pass unchanged (it
  wasn't modified, but worth confirming no accidental edit).

Verification: `uv run pytest tests/ -q -k "search_text or regex_search"` →
all pass, including 2 new tests.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `_search_text` no longer contains its own file-walking/matching loop
      (delegates to `_regex_search` or a shared helper)
- [ ] `uv run pytest tests/ -q -k "search_text or regex_search"` exits 0,
      including the 2 new tests
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 045 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 2's manual trace finds a pattern where `re.escape` + `_regex_search`
  doesn't produce the same match result as the old plain substring check
  (would indicate a subtlety in `_MAX_SEARCH_RESULTS` truncation ordering
  or line-splitting behavior between the two original implementations that
  this plan's excerpt didn't capture).
- A step's verification fails twice after a reasonable fix attempt.
- `_regex_search`'s `flags` parameter doesn't accept `"i"` the way assumed
  (re-read its flag-parsing logic, `fs.py:373-379`, to confirm before
  relying on it).

## Maintenance notes

- If a future contributor wants to go further and remove `search_text` as
  a separate model-facing tool entirely (folding it into `regex_search`'s
  schema, e.g. via a `regex: bool` flag defaulting to `false`), that's a
  larger, tool-schema-changing follow-up — this plan deliberately keeps
  both tool names intact to minimize risk and blast radius.
- A reviewer should scrutinize the exact string-matching logic in Step 1's
  primary approach (or, if the shared-helper alternative was chosen,
  confirm both functions' message wording is preserved via that path
  instead) — this is the one place where a subtle behavior mismatch could
  hide.
