# Plan 031: Fix `MUTATING_TOOLS` in `tool_dedup.py` — phantom names and missing real ones

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/tool_dedup.py bauer/tool_router.py`
> If either file changed since this plan was written, re-derive the tool
> inventory in Step 1 from the live code before proceeding; on a mismatch
> with the excerpts below, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: MED (changing what's "mutating" changes dedup-cache behavior —
  test carefully, see Test plan)
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`ToolCallDeduper` (wired into the live agent loop) replays a cached result
instead of re-executing when a tool call is byte-identical to a prior
successful call in the same session — *unless* the tool is in
`MUTATING_TOOLS`, in which case it's never cached and executing it clears
the whole cache (see the module docstring at `bauer/tool_dedup.py:1-17`).
The `MUTATING_TOOLS` frozenset is a hand-maintained literal that has drifted
from the real tool catalog: it references 5 tool names that don't exist in
the codebase (dead entries, harmless but misleading), and it's missing
~15-20 tools that genuinely mutate state — including `run_command` itself.
Concretely: two identical `run_command` calls in the same session will have
the *second* one silently replay the first one's cached stdout/stderr
instead of re-running the command — surprising for any non-idempotent
command (writes, network calls, anything with a clock or random output).
The same gap applies to `browser_type`, `browser_press`, `kanban_complete`,
`kanban_block`, and others. This is a correctness bug in a mechanism whose
entire job is "don't return stale results" — right now it silently does the
opposite for a meaningful slice of the real tool catalog.

## Current state

- `bauer/tool_dedup.py:29-35` — the buggy literal:
  ```python
  MUTATING_TOOLS = frozenset({
      "write_file", "patch", "append_file", "delete_file", "move_file",
      "copy_file", "create_dir", "shell", "cronjob", "process",
      "browser_click", "browser_fill", "browser_navigate",
      "kanban_create", "kanban_update", "kanban_claim", "kanban_comment",
      "memory", "todo", "mcp_call", "http_request", "delegate_task",
  })
  ```
- `bauer/tool_router.py:170-258` — `_TOOL_SECURITY`, a dict of
  `{tool_name: {"permission": ..., "risk": ..., "approval": ...}}` for every
  registered tool. This is the authoritative list of real tool names (84
  entries) and their permission class (`read` | `write` | `execute` |
  `network` | `shell`). **Caveat**: `permission` is not a perfect proxy for
  "safe to dedup" — e.g. `"memory": {"permission": "read", ...}` at
  `tool_router.py:176` is nonetheless already (correctly) treated as
  mutating in the current `MUTATING_TOOLS` set, because the `memory` tool is
  a multiplexed action (it can write, not just read) even though its
  `_TOOL_SECURITY` entry undersells that. Do not derive the fix mechanically
  from `permission != "read"` alone — cross-check each addition against
  the tool's actual behavior (its implementation in `bauer/tools/*.py`), not
  just its `_TOOL_SECURITY` label.
- **Phantom names to remove** (confirmed absent from `_TOOL_SECURITY` and
  from every `bauer/tools/*.py` mixin — grep `bauer/tool_router.py` and
  `bauer/tools/` for each and confirm zero hits before removing):
  `"copy_file"`, `"shell"`, `"kanban_update"`, `"kanban_claim"`,
  `"browser_fill"`. (The real names are: no `copy_file`/`shell` tool exists
  at all; `run_command`/`execute_code` are the shell-equivalent tools;
  `kanban_complete`/`kanban_block`/`kanban_unblock`/`kanban_heartbeat`/
  `kanban_link` are the real kanban mutators; `browser_type` is the real
  browser text-input tool.)
- **Missing real mutating tools** confirmed present in `_TOOL_SECURITY` with
  `permission` in `{"write", "execute", "shell"}` and absent from
  `MUTATING_TOOLS` today (cross-reference `tool_router.py:170-258`):
  `run_command`, `execute_code` (already correctly excluded? **check** —
  it is NOT in the current set, add it), `browser_type`, `browser_press`,
  `browser_dialog`, `browser_cdp`, `kanban_complete`, `kanban_block`,
  `kanban_unblock`, `kanban_heartbeat`, `kanban_link`, `skill_manage`,
  `app_factory_init`, `lsp_format`, `lsp_rename`, `verify_app`,
  `mixture_of_agents`, `image_generate`, `text_to_speech`, `video_analyze`,
  `vision_analyze`, `channel_send`, `send_message`, `social_post`,
  `video_generate`.
  Not every `permission: network` tool is mutating (e.g. `web_search`,
  `web_fetch`, `browser_snapshot`, `mcp_list_tools` are read-like) — only add
  the ones listed above, which have real external side effects (posting,
  clicking, typing, navigating, sending a message, generating media).
- Repo convention: this module already documents its own reasoning inline
  (see the docstring at the top of the file) — keep that same
  explanation-first style if you add commentary near the corrected set.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Test (this file) | `uv run pytest tests/test_tool_dedup.py -q` | all pass |
| Related tests | `uv run pytest tests/test_loop_detection.py -q` | all pass |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/tool_dedup.py` (`MUTATING_TOOLS` only)
- `tests/test_tool_dedup.py` (add regression tests)

**Out of scope** (do NOT touch, even though they look related):
- `bauer/tool_router.py`'s `_TOOL_SECURITY` — read-only reference for this
  plan, do not modify it.
- `bauer/tool_guardrails.py`'s `_DEFAULT_READONLY_TOOLS` and
  `bauer/tool_policy.py`'s worker allowlist — both noted by the audit as
  having similar hand-maintained-list drift, but they are separate lists
  with separate purposes (guardrail loop-detection vs. worker sandboxing).
  Fixing them is a separate, not-yet-planned follow-up — do not touch them
  here.
- Do not change `ToolCallDeduper`'s `check()`/`record()` logic itself — only
  the membership of `MUTATING_TOOLS`.

## Git workflow

- Branch: `fix/031-tool-dedup-mutating-tools`
- Commit message style: conventional commits matching recent history, e.g.
  `fix(agent): corrige lista de tools mutantes no dedup de tool calls`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Re-derive and verify the tool inventory

Before editing, confirm the current real tool catalog by reading
`bauer/tool_router.py`'s `_TOOL_SECURITY` dict in full (currently
`tool_router.py:170-258`, ~84 entries). Cross-check each of the 5 "phantom"
names listed above against this dict AND against `grep -rn "\"copy_file\"\|'copy_file'" bauer/`
(repeat per phantom name) to confirm zero real registrations. If any phantom
name turns out to actually exist now (catalog changed since this plan was
written), treat that as a STOP condition (re-derive the fix from scratch,
this plan's excerpts are stale).

**Verify**: for each of `copy_file`, `shell`, `kanban_update`, `kanban_claim`,
`browser_fill`: `grep -rn "\"<name>\"" bauer/tool_router.py bauer/tools/` →
zero results (confirms it's safe to remove as a phantom).

### Step 2: Replace `MUTATING_TOOLS`

In `bauer/tool_dedup.py`, replace the frozenset at lines 29-35 with the
corrected membership (remove the 5 phantoms, add the confirmed-missing real
mutating tools from "Current state" above), keeping the existing comment
above it:

```python
MUTATING_TOOLS = frozenset({
    "write_file", "patch", "append_file", "delete_file", "move_file",
    "create_dir", "cronjob", "process", "run_command",
    "browser_click", "browser_type", "browser_navigate", "browser_press",
    "browser_dialog", "browser_cdp",
    "kanban_create", "kanban_complete", "kanban_block", "kanban_unblock",
    "kanban_heartbeat", "kanban_comment", "kanban_link",
    "skill_manage", "app_factory_init", "lsp_format", "lsp_rename",
    "verify_app", "mixture_of_agents", "image_generate", "text_to_speech",
    "video_analyze", "vision_analyze", "video_generate",
    "channel_send", "send_message", "social_post",
    "memory", "todo", "mcp_call", "http_request", "delegate_task",
})
```

Keep `"memory"` and `"todo"` as-is (already correct per the caveat in
"Current state" — do not remove them even though `_TOOL_SECURITY` would
suggest otherwise for `memory`). If `"todo"` is not found in
`_TOOL_SECURITY` at all during Step 1, that's expected — it's registered
elsewhere (e.g. via `agent_misc.py`'s multiplexed action) and the audit
already treats it as correctly-included; do not remove it.

**Verify**: `uv run python -c "import ast; ast.parse(open('bauer/tool_dedup.py', encoding='utf-8').read())"` → exits 0.

### Step 3: Add a drift-prevention test

Add a test that asserts every entry in `MUTATING_TOOLS` corresponds to a
real, currently-registered tool name — so a future rename/removal can't
silently reintroduce a phantom entry. Import the real tool name set from
wherever `ToolRouter` exposes its registered actions (check
`bauer/tool_router.py` for a way to enumerate `self._tools` keys without
constructing a full router with live dependencies — e.g. if
`_TOOL_SECURITY.keys()` is accessible as a module-level constant, assert
`MUTATING_TOOLS <= set(_TOOL_SECURITY.keys()) | {"todo"}` — adjust the
right-hand side only if `"todo"` genuinely isn't in `_TOOL_SECURITY`, per
Step 1's finding).

**Verify**: `uv run pytest tests/test_tool_dedup.py -q -k drift` → new test
passes.

## Test plan

Add to `tests/test_tool_dedup.py` (model after the existing tests' fixture
setup for `ToolCallDeduper`):

- **New test**: `test_mutating_tools_are_all_real` (the drift-prevention
  test from Step 3) — asserts no phantom names.
- **New test**: `test_run_command_never_deduped` — call `deduper.record()`
  with `action="run_command"` and some args/result, then `deduper.check()`
  with the identical args; assert it returns `None` (cache miss — must
  re-execute), matching the existing test pattern for other mutating tools
  like `write_file`.
- **New test**: `test_browser_type_never_deduped` — same shape as above for
  `browser_type` (previously missing from the set).
- **Regression guard**: confirm existing tests for tools that were already
  correctly excluded (e.g. `read_file`, `web_search` — should still be
  dedupable) keep passing unchanged.

Verification: `uv run pytest tests/test_tool_dedup.py -q` → all pass,
including the new tests. Also run `uv run pytest tests/test_loop_detection.py -q`
since that test file was flagged by the audit as touching related
dedup/loop-detection behavior — confirm no regression there.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `uv run pytest tests/test_tool_dedup.py -q` exits 0; new tests exist
      and pass
- [ ] `uv run pytest tests/test_loop_detection.py -q` exits 0
- [ ] `uv run pytest tests/ -q --tb=short` exits 0 (no regressions elsewhere)
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] None of `copy_file`, `shell`, `kanban_update`, `kanban_claim`,
      `browser_fill` appear in `bauer/tool_dedup.py` (`grep -n "copy_file\|kanban_update\|kanban_claim\|browser_fill" bauer/tool_dedup.py` → no matches; `shell` needs a targeted check since it may substring-match elsewhere — confirm manually it's gone from the frozenset)
- [ ] `run_command` and `browser_type` appear in `MUTATING_TOOLS`
      (`grep -n "run_command\|browser_type" bauer/tool_dedup.py` → matches)
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 031 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1's re-derivation finds the tool catalog has changed meaningfully
  since this plan was written (new tools added/removed, permissions
  reclassified) — re-derive the corrected set from the live
  `_TOOL_SECURITY` instead of blindly applying the literal in Step 2.
- A step's verification fails twice after a reasonable fix attempt.
- You find evidence that a tool listed above as "missing, should be
  mutating" is actually read-only in practice (its implementation in
  `bauer/tools/*.py` never writes/changes external state) — report this
  instead of adding it, since the `_TOOL_SECURITY` `permission` field alone
  is not fully reliable (see the `"memory"` caveat above).
- The fix appears to require touching `tool_guardrails.py` or
  `tool_policy.py` — those are explicitly out of scope for this plan.

## Maintenance notes

- The drift-prevention test added in Step 3 only catches *phantom* names
  (entries that don't exist). It does not catch the opposite failure mode —
  a new mutating tool added to the codebase without being added to
  `MUTATING_TOOLS`. Consider filing a follow-up "direction" finding: derive
  `MUTATING_TOOLS` automatically from `_TOOL_SECURITY` (e.g.
  `permission in {"write", "execute", "shell"}` plus an explicit
  `mutating: true` flag added to the handful of `network`-permission tools
  that mutate) instead of maintaining two independent lists forever. That is
  a larger refactor and was deliberately not attempted in this S-effort fix.
- `tool_guardrails.py`'s `_DEFAULT_READONLY_TOOLS` and `tool_policy.py`'s
  worker allowlist have the same "hand-maintained list drifts from reality"
  root cause — a reviewer picking up further hardening in this area should
  read those two alongside this fix.
