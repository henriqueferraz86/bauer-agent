# Plan 042: Collapse the 7 read-only `lsp_*` tools into one multiplexed `lsp` tool

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/tools/code_intel.py bauer/tool_router.py`
> If either file changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW (scoped to the 7 read-only LSP actions only — see "Why this
  matters" for why the 2 write actions are deliberately excluded)
- **Depends on**: none
- **Category**: perf (tool-schema token cost) / tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`bauer/tools/code_intel.py` defines a single internal dispatcher,
`_lsp_call(method, file, line, char, **kwargs)` (`code_intel.py:85-161`),
that already branches on a `method` string
(`hover|definitions|references|diagnostics|workspace_symbols|completion|
code_actions|format_document|rename_symbol`). On top of that one dispatcher,
9 near-identical ~10-line wrapper methods are registered as 9 **separate**
model-facing tools in `bauer/tool_router.py:1370-1449`
(`lsp_hover`, `lsp_definitions`, `lsp_references`, `lsp_diagnostics`,
`lsp_workspace_symbols`, `lsp_completion`, `lsp_code_actions`, `lsp_format`,
`lsp_rename`) — each wrapper just extracts args, calls `_lsp_call`, and
JSON-dumps the result. 9 tool schemas cost real prompt tokens on every
turn (`bauer/commands/_runtime.py:647` measures this repo's own tool-schema
cost). This is also why the team's own curated
`_LOCAL_DEFAULT_ALLOWLIST`/worker-context allowlist
(`tool_router.py:338-354`) already excludes all `lsp_*` tools for small-
context local models — the tool *count* itself is the cost, not any single
capability. Collapsing the 7 **read-only** actions (`hover`, `definitions`,
`references`, `diagnostics`, `workspace_symbols`, `completion`,
`code_actions` — all `{"permission": "read", "risk": "low"}` in
`_TOOL_SECURITY`) into one `lsp(action=...)` tool removes 6 schema entries
with no permission-model change. `lsp_format` (`write`/`medium`) and
`lsp_rename` (`write`/`high`, requires approval) are deliberately **left
separate** in this plan — they have materially different risk/approval
properties from the read-only 7 and from each other, and folding them into
the same multiplexed tool would require moving per-action approval gating
inside the tool body (a bigger, riskier change than this plan's scope; see
Plan 043's kanban-multiplex plan for the pattern if that's wanted later).

## Current state

- `bauer/tools/code_intel.py:85-161` — `_lsp_call`, the shared dispatcher
  (unchanged by this plan; only its 7 read-only callers are collapsed).
- `bauer/tools/code_intel.py:163-225` — the 7 read-only wrapper methods to
  collapse: `_lsp_hover`, `_lsp_definitions`, `_lsp_references`,
  `_lsp_diagnostics`, `_lsp_workspace_symbols`, `_lsp_completion`,
  `_lsp_code_actions`. Example (`_lsp_hover`, `code_intel.py:163-173`):
  ```python
  def _lsp_hover(self, args: dict) -> str:
      file_rel = str(args.get("file", "")).strip()
      line = self._coerce_int(args.get("line", 0), default=0, minimum=0)
      char = self._coerce_int(args.get("character", 0), default=0, minimum=0)
      if not file_rel:
          raise ToolError("lsp_hover requer 'file'.")
      result = self._lsp_call("hover", file_rel, line, char)
      if result is None:
          server_hint = "pyright" if file_rel.endswith(".py") else "typescript-language-server"
          return json.dumps({"error": "LSP server not running", "hint": f"pip/npm install {server_hint}"})
      return json.dumps(result, indent=2, ensure_ascii=False)
  ```
  All 7 follow this exact shape: parse `args`, validate required fields,
  call `_lsp_call(method, ...)`, JSON-dump. `_lsp_workspace_symbols`
  (`:206-214`) is the one outlier — it repurposes the `file_rel` positional
  slot as a `query` string (see its comment). `_lsp_code_actions`
  (`:227-241`) takes a range (`start_line`/`start_char`/`end_line`/
  `end_char`) instead of a point.
- `bauer/tools/code_intel.py:243-273` — `_lsp_format` and `_lsp_rename` —
  **do not touch these**, they stay as separate tools.
- `bauer/tool_router.py:190-198` — `_TOOL_SECURITY` entries for all 9;
  after this plan, add one new entry for `"lsp"` (permission `read`, risk
  `low`, matching the 7 collapsed actions) and keep `lsp_format`/
  `lsp_rename` unchanged; remove the 7 collapsed entries.
- `bauer/tool_router.py:1370-1430` — the 7 registration blocks to collapse
  into one (`lsp_format`/`lsp_rename` registrations at `:1431-1449` stay
  as-is).
- `bauer/tool_router.py:338-354` — the worker-context allowlist currently
  lists all 7 read-only `lsp_*` names individually (not `lsp_format`/
  `lsp_rename`, which are absent — confirming they're already excluded from
  worker context). After this plan, replace the 7 entries with one `"lsp"`
  entry in this list.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Related tests | `uv run pytest tests/ -q -k "lsp or code_intel"` | all pass |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/tools/code_intel.py` (replace the 7 wrapper methods with 1)
- `bauer/tool_router.py` (registration block, `_TOOL_SECURITY`, worker
  allowlist — the 3 locations listed in "Current state")
- Tests exercising the 7 collapsed tools

**Out of scope** (do NOT touch, even though they look related):
- `_lsp_format` / `_lsp_rename` and their tool registrations — stay
  separate tools, unchanged (see "Why this matters" for the reasoning).
- `_lsp_call` itself — the shared dispatcher's internals don't change.
- `bauer/lsp/manager.py`, `bauer/lsp/servers.py` — the actual LSP client
  machinery; untouched.
- `code_intel.py`'s other (non-LSP) tools — `code_symbols`,
  `find_definition`, `get_imports`, `find_usages` (the AST-based tools) —
  a prior audit flagged these as conceptually overlapping with the LSP
  tools but structurally different (no shared dispatcher); leave them
  alone, that's a separate, lower-confidence finding not part of this plan.

## Git workflow

- Branch: `refactor/042-collapse-lsp-tools`
- Commit message style: conventional commits matching recent history, e.g.
  `refactor(tools): unifica 7 tools lsp_* de leitura em uma tool lsp`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Add the multiplexed `_lsp` method

In `bauer/tools/code_intel.py`, replace the 7 methods
(`_lsp_hover`...`_lsp_code_actions`, lines 163-241) with one:

```python
_LSP_READ_ACTIONS = {
    "hover": "hover",
    "definitions": "definitions",
    "references": "references",
    "diagnostics": "diagnostics",
    "workspace_symbols": "workspace_symbols",
    "completion": "completion",
    "code_actions": "code_actions",
}

def _lsp(self, args: dict) -> str:
    action = str(args.get("action", "")).strip()
    if action not in self._LSP_READ_ACTIONS:
        raise ToolError(
            f"lsp: 'action' invalido {action!r}. "
            f"Use um de: {', '.join(sorted(self._LSP_READ_ACTIONS))}."
        )
    method = self._LSP_READ_ACTIONS[action]

    if method == "workspace_symbols":
        query = str(args.get("query", "")).strip()
        if not query:
            raise ToolError("lsp action=workspace_symbols requer 'query'.")
        result = self._lsp_call("workspace_symbols", query, 0, 0)
        hint = {"hint": "pip install pyright"}
    else:
        file_rel = str(args.get("file", "")).strip()
        if not file_rel:
            raise ToolError(f"lsp action={action} requer 'file'.")
        server_hint = "pyright" if file_rel.endswith(".py") else "typescript-language-server"
        hint = {"hint": f"pip/npm install {server_hint}"}
        if method == "code_actions":
            start_line = self._coerce_int(args.get("start_line", 0), default=0, minimum=0)
            start_char = self._coerce_int(args.get("start_char", 0), default=0, minimum=0)
            end_line = self._coerce_int(args.get("end_line", start_line), default=start_line, minimum=0)
            end_char = self._coerce_int(args.get("end_char", start_char), default=start_char, minimum=0)
            result = self._lsp_call(
                "code_actions", file_rel, start_line, start_char,
                end_line=end_line, end_char=end_char,
            )
        else:
            line = self._coerce_int(args.get("line", 0), default=0, minimum=0)
            char = self._coerce_int(args.get("character", 0), default=0, minimum=0)
            result = self._lsp_call(method, file_rel, line, char)

    if result is None:
        return json.dumps({"error": "LSP server not running", **hint})
    return json.dumps(result, indent=2, ensure_ascii=False)
```

Preserve every original error message's wording where practical (e.g. the
`{action} requer 'file'` pattern mirrors each original's
`f"lsp_{name} requer 'file'."`) so existing tests that assert on error text
need minimal changes.

**Verify**: `uv run python -c "import ast; ast.parse(open('bauer/tools/code_intel.py', encoding='utf-8').read())"` → exits 0.

### Step 2: Replace the 7 registrations with 1 in `tool_router.py`

Replace `self._tools["lsp_hover"] = {...}` through
`self._tools["lsp_code_actions"] = {...}` (`tool_router.py:1370-1430`) with:

```python
self._tools["lsp"] = {
    "fn": self._lsp,
    "description": (
        "Consulta o servidor LSP (hover, definicoes, referencias, "
        "diagnosticos, simbolos do workspace, autocompletar, acoes de "
        "codigo) via 'action'."
    ),
    "args": {
        "action": "str — um de: hover, definitions, references, diagnostics, workspace_symbols, completion, code_actions",
        "file": "str — caminho do arquivo (nao usado para action=workspace_symbols)",
        "line": "int — linha 0-indexed (nao usado para workspace_symbols/code_actions)",
        "character": "int — coluna 0-indexed (nao usado para workspace_symbols/code_actions)",
        "query": "str — texto de busca (somente action=workspace_symbols)",
        "start_line": "int — linha inicial do intervalo (somente action=code_actions)",
        "start_char": "int — coluna inicial (somente action=code_actions)",
        "end_line": "int — linha final (somente action=code_actions)",
        "end_char": "int — coluna final (somente action=code_actions)",
    },
}
```

Leave `lsp_format`/`lsp_rename` registrations (`:1431-1449`) untouched.

**Verify**: `grep -c "self._tools\[\"lsp_" bauer/tool_router.py` → exactly
2 (only `lsp_format` and `lsp_rename` remain as individually-registered).

### Step 3: Update `_TOOL_SECURITY` and the worker allowlist

In `_TOOL_SECURITY` (`tool_router.py:190-198`), remove the 7 read-only
entries and add:
```python
"lsp": {"permission": "read", "risk": "low", "approval": False},
```
Keep `lsp_format`/`lsp_rename` entries unchanged.

In the worker-context allowlist (`tool_router.py:338-354`), replace the 7
listed `lsp_*` names with a single `"lsp"` entry in the same position.

**Verify**: `grep -n "\"lsp_hover\"\|\"lsp_definitions\"\|\"lsp_references\"\|\"lsp_diagnostics\"\|\"lsp_workspace_symbols\"\|\"lsp_completion\"\|\"lsp_code_actions\"" bauer/tool_router.py`
→ no matches anywhere in the file (all 7 fully removed, including from
`_TOOL_SECURITY` and the allowlist).

## Test plan

- For each of the 7 original tests covering `lsp_hover`/`lsp_definitions`/
  etc. (find them via `grep -rln "_lsp_hover\|_lsp_definitions\|_lsp_references\|_lsp_diagnostics\|_lsp_workspace_symbols\|_lsp_completion\|_lsp_code_actions" tests/`),
  update the test to call `router._lsp({"action": "hover", ...})` (etc.)
  instead of the old per-action method — same assertions on the returned
  JSON, just routed through the new single entry point with an `action`
  key added to the args dict.
- **New test**: `test_lsp_invalid_action_raises` — call `_lsp({"action":
  "bogus"})`, assert `ToolError` is raised listing the valid actions.
- **New test**: `test_lsp_workspace_symbols_requires_query` — call
  `_lsp({"action": "workspace_symbols"})` with no `query`, assert
  `ToolError`.
- Confirm `lsp_format`/`lsp_rename`'s existing tests are untouched and
  still pass (they weren't part of this change).

Verification: `uv run pytest tests/ -q -k "lsp or code_intel"` → all pass.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `bauer/tools/code_intel.py` has one `_lsp` method covering the 7
      read-only actions; the 7 old wrapper methods no longer exist
      (`grep -n "def _lsp_hover\|def _lsp_definitions\|def _lsp_references\|def _lsp_diagnostics\|def _lsp_workspace_symbols\|def _lsp_completion\|def _lsp_code_actions" bauer/tools/code_intel.py` → no matches)
- [ ] `bauer/tool_router.py` registers exactly 3 lsp-related tools:
      `lsp`, `lsp_format`, `lsp_rename`
      (`grep -c "self._tools\[\"lsp" bauer/tool_router.py` → 3)
- [ ] `_TOOL_SECURITY` and the worker allowlist both reflect the same 3
      names, no orphaned entries for the 7 removed ones
- [ ] `uv run pytest tests/ -q -k "lsp or code_intel"` exits 0, including
      updated + 2 new tests
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 042 updated

## STOP conditions

Stop and report back (do not improvise) if:

- `_lsp_call`'s method-string values have changed since this plan was
  written (e.g. renamed `"format_document"` or added a new method) —
  re-derive the action-to-method mapping from the live code.
- A step's verification fails twice after a reasonable fix attempt.
- You find a caller of any of the 7 old `_lsp_*` methods outside
  `tool_router.py` and their own tests (e.g. another tool or module calling
  `self._lsp_hover(...)` directly) — report before removing, since that
  would be a breaking change beyond this plan's scope.

## Maintenance notes

- If `lsp_format`/`lsp_rename` are ever folded into the same multiplexed
  `lsp` tool in a future change, that requires moving their per-action
  approval/risk gating (`format`: medium/no-approval, `rename`: high/
  approval-required) inside the tool body — a materially riskier change
  than this plan; treat it as a separate follow-up, not an extension of
  this one.
- A reviewer should confirm the new `lsp` tool's `description`/`args` text
  is clear enough for a model to pick the right `action` and supply the
  right fields per action (this is now one schema doing the job of 7, so
  its documentation quality matters more than any individual old tool's
  did).
