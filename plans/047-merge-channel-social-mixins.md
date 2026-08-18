# Plan 047: Merge `SocialToolsMixin` into `ChannelToolsMixin` (one messaging mixin instead of two)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/tools/channel.py bauer/tools/social.py bauer/tool_router.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P4
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`bauer/tools/channel.py` (118 lines: `_channel_send`, `_channel_list`,
`_send_message`) and `bauer/tools/social.py` (144 lines:
`_social_list_channels`, `_social_post`) are two small, single-purpose
mixin files that both implement the same underlying concern: *"send an
outbound message/post to an external service configured elsewhere."*
`channel.py` targets chat channels (Telegram/Discord via
`GatewayChannelRegistry`/`GatewayOutbox`/`live_bridges`); `social.py`
targets social platforms (Instagram/X/LinkedIn/etc. via Postiz). Both are
self-contained (no shared state beyond `self.workspace` and, for social,
`self._postiz_api_key`/`self._postiz_api_url`), and both are inherited by
`ToolRouter` at `bauer/tool_router.py:391` (`ChannelToolsMixin`) and `:404`
(`SocialToolsMixin`). This is a pure file/organization consolidation — it
reduces the mixin file count (21 → 20) and puts all "Bauer sends a message
somewhere" logic in one place, with **zero change to any tool's behavior,
name, schema, or registration**. This is the lowest-risk item in this
plan set: no tool-calling contract changes at all, only where the Python
code that implements 5 already-existing tools happens to live.

## Current state

- `bauer/tools/channel.py` — full file (119 lines): module docstring,
  `class ChannelToolsMixin:` with `_channel_send`, `_channel_list`,
  `_send_message`. Each method is self-contained, importing what it needs
  locally (`from ..gateway_channels import GatewayChannelRegistry`, etc.).
- `bauer/tools/social.py` — starts: module docstring, `class
  SocialToolsMixin:` with `_postiz_client` (private helper,
  `social.py:17-30`, reads `self._postiz_api_key`/`self._postiz_api_url`),
  `_social_list_channels` (`:32-...`), and `_social_post` (not shown in
  this excerpt — read the full file before merging, it continues past line
  40).
- `bauer/tool_router.py:69,84` — imports:
  ```python
  from .tools.channel import ChannelToolsMixin
  ...
  from .tools.social import SocialToolsMixin
  ```
- `bauer/tool_router.py:391,404` — inheritance list (part of `ToolRouter`'s
  base-class tuple):
  ```python
      ChannelToolsMixin,
      ...
      SocialToolsMixin,
  ```
  (the surrounding lines are other mixins — read the full inheritance list
  around these two lines to confirm exact position/formatting before
  editing).
- **Naming decision**: rename the merged class. Both `channel.py`'s
  docstring ("Canais do Bauer Gateway") and `social.py`'s ("Postiz")
  describe messaging destinations — a name like `MessagingToolsMixin` (in a
  renamed `bauer/tools/messaging.py`) covers both without favoring either
  original name. Confirm this fits `bauer/tools/`'s existing naming
  convention (check a few other mixin filenames — `fs.py`, `web.py`,
  `execution.py` — single lowercase nouns for the domain) before finalizing
  the name in Step 1.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Related tests | `uv run pytest tests/ -q -k "channel or social"` | all pass |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/tools/channel.py` (becomes the merged file, or is replaced by a
  new `bauer/tools/messaging.py` — pick one approach in Step 1 and follow
  it consistently)
- `bauer/tools/social.py` (deleted, content moved)
- `bauer/tool_router.py` (import + inheritance list update)
- Any test file importing `ChannelToolsMixin`/`SocialToolsMixin` directly

**Out of scope** (do NOT touch, even though they look related):
- `bauer/gateway_channels.py`, `bauer/gateway_outbox.py`,
  `bauer/live_bridges.py`, `bauer/postiz_client.py` — the underlying
  implementations the mixin methods call into; unchanged.
- The 5 tools' registrations, names, descriptions, or `args` schemas in
  `bauer/tool_router.py`'s `self._tools[...]` block and `_TOOL_SECURITY` —
  unchanged. Only the Python module/class the methods live in changes.
- Do not attempt any behavioral unification between `channel_send`/
  `send_message` and `social_post` (e.g. a shared "send to any platform"
  abstraction) — that would be a real feature change, not a file
  consolidation; explicitly out of scope.

## Git workflow

- Branch: `chore/047-merge-channel-social-mixins`
- Commit message style: conventional commits matching recent history, e.g.
  `chore(tools): funde ChannelToolsMixin e SocialToolsMixin em um so mixin`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Decide the merged file/class name and read both files in full

Read `bauer/tools/channel.py` and `bauer/tools/social.py` completely
(social.py continues past line 40 shown in "Current state" — read to the
end, including `_social_post`). Decide: rename to `bauer/tools/
messaging.py` / `MessagingToolsMixin` (recommended, per "Current state"'s
naming rationale), or keep `channel.py`/`ChannelToolsMixin` as the
surviving name and fold `social.py`'s content into it (simpler diff, less
"correct" naming since "channel" doesn't obviously cover social-media
posting). Either is acceptable — pick one and apply it consistently through
the rest of this plan's steps.

**Verify**: state which naming choice you made and why.

### Step 2: Merge the file contents

Create the merged file (new `messaging.py`, or the surviving `channel.py`)
containing:
- A combined module docstring describing both concerns (chat channels via
  Bauer Gateway, and social platforms via Postiz).
- One class (`MessagingToolsMixin` or `ChannelToolsMixin`, per Step 1's
  decision) with all 5 methods: `_channel_send`, `_channel_list`,
  `_send_message`, `_postiz_client`, `_social_list_channels`,
  `_social_post` (6 methods total, including the private `_postiz_client`
  helper) — copied verbatim, no logic changes.
- Keep each method's existing docstring and implementation exactly as-is.

Delete the old `bauer/tools/social.py` (and, if renaming, the old
`bauer/tools/channel.py` too, replaced by the new file).

**Verify**: `uv run python -c "import ast; ast.parse(open('bauer/tools/<merged-file>.py', encoding='utf-8').read())"`
→ exits 0. `grep -c "def _" bauer/tools/<merged-file>.py` → 6 (all methods
present).

### Step 3: Update `tool_router.py`'s import and inheritance list

Replace the two imports (`tool_router.py:69,84`) with one import of the
merged class. Replace the two entries in the inheritance tuple
(`tool_router.py:391,404`) with one.

**Verify**: `grep -n "ChannelToolsMixin\|SocialToolsMixin\|MessagingToolsMixin" bauer/tool_router.py`
→ exactly 2 matches (one import, one inheritance-list entry) for whichever
single class name Step 1 chose.

### Step 4: Confirm the router still constructs and exposes all 5 tools

```bash
uv run python -c "
from bauer.tool_router import ToolRouter
import inspect
r = ToolRouter.__new__(ToolRouter)
print([m for m in ('_channel_send', '_channel_list', '_send_message', '_postiz_client', '_social_list_channels', '_social_post') if hasattr(r, m)])
"
```

**Verify**: prints all 6 method names (adjust the exact instantiation
approach if `ToolRouter.__new__` doesn't work cleanly for this repo's
constructor — the goal is just confirming the merged class's methods are
present on `ToolRouter` instances via MRO, not fully constructing a live
router with all its dependencies).

## Test plan

- Any existing test importing `from bauer.tools.channel import
  ChannelToolsMixin` or `from bauer.tools.social import SocialToolsMixin`
  directly needs its import updated to the new module/class name — find
  via `grep -rln "tools.channel import\|tools.social import" tests/`.
- No new test logic needed — all 5 tools' behavior is unchanged; existing
  tests for `channel_send`/`channel_list`/`send_message`/
  `social_list_channels`/`social_post` should pass unmodified except for
  import-path updates.

Verification: `uv run pytest tests/ -q -k "channel or social"` → all pass.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `bauer/tools/social.py` no longer exists
- [ ] One merged mixin file exists with all 6 methods (5 tools +
      `_postiz_client` helper)
- [ ] `bauer/tool_router.py` has exactly one import and one inheritance
      entry for the merged class
- [ ] Step 4's method-presence check passes
- [ ] `uv run pytest tests/ -q -k "channel or social"` exits 0
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 047 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Either mixin has grown a method or dependency this plan's excerpt didn't
  capture (re-read both files fully in Step 1 before merging — do not
  assume the excerpts above are the complete files).
- The two classes turn out to share a method name (a real name collision
  merging them) — check for this explicitly before merging; if found,
  report rather than silently letting one shadow the other.
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- This is a pure file-organization change — a reviewer should be able to
  confirm correctness almost entirely from the diff being "the same code,
  moved" with no logic lines changed, plus the import/inheritance-list
  update in `tool_router.py`.
- If a future contributor wants an actual behavioral unification (e.g. one
  `send(platform, ...)` tool covering both chat channels and social posts),
  that's a real feature-design decision explicitly deferred by this plan —
  see "Out of scope".
