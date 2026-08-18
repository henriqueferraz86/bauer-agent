# Plan 043: Multiplex the 9 `kanban_*` tools into `kanban_read`/`kanban_write`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/tools/kanban.py bauer/tool_router.py`
> If either file changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition. **Note**: this plan depends on Plan 044
> (delete dead `_legacy_kanban_*` methods) landing first — do this plan
> after 044 so you're not multiplexing around dead code.

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: LOW-MED (see the `_CHAT_CONTEXT_DENYLIST` discovery below —
  lower risk than initially estimated, but still touches 9 tool schemas'
  worth of call sites)
- **Depends on**: plans/044-delete-legacy-kanban-methods.md (do that
  cleanup first)
- **Category**: perf (tool-schema token cost) / tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`bauer/tool_router.py:982-1058` registers 9 separate kanban tools
(`kanban_create`, `kanban_list`, `kanban_show`, `kanban_complete`,
`kanban_block`, `kanban_unblock`, `kanban_heartbeat`, `kanban_comment`,
`kanban_link`), all implemented in `bauer/tools/kanban.py`. This codebase
already has an established pattern for exactly this "CRUD + lifecycle ops
on one entity" shape: `bauer/tools/cronjob.py`'s `_cronjob(args)` multiplexes
6 operations behind one `action` arg, and `bauer/tools/agent_misc.py`'s
`_process(args)` does the same for 6 more. Kanban is the outlier — 9
separate tool schemas for one entity, each costing real prompt tokens on
every turn. Collapsing to 2 tools (`kanban_read` for `list`/`show`,
`kanban_write` for the 7 mutating operations) matches the established
multiplex pattern and removes 7 schema entries.

**Risk discovery made while planning this**: a prior audit flagged
`_CHAT_CONTEXT_DENYLIST = frozenset({"kanban_heartbeat", "kanban_complete",
"kanban_block"})` (`tool_router.py:319-323`) as a reason this consolidation
is risky — it looked like an active per-action security gate that a
multiplexed tool couldn't easily preserve. **Verify this in Step 1**: a
repo-wide grep for `_CHAT_CONTEXT_DENYLIST` outside its own definition
found **zero consumers** — nothing in the codebase actually reads this
constant. If Step 1 confirms it's genuinely dead configuration (not
consulted anywhere, e.g. via a dynamic `getattr`/reflection this plan's
author might have missed), this materially lowers the risk of this plan:
there is no live per-action chat-context gate to preserve during the
migration. This also means `_CHAT_CONTEXT_DENYLIST` itself is a separate,
tiny dead-code finding worth flagging to the maintainer (see Maintenance
notes) — but confirm before relying on this, since a stale grep is exactly
the kind of thing that should be re-verified, not assumed.

## Current state

- `bauer/tools/kanban.py` — after Plan 044's cleanup, contains only the
  live implementations: `_kanban_create`, `_kanban_list`, `_kanban_show`,
  `_kanban_complete`, `_kanban_block`, `_kanban_unblock`,
  `_kanban_heartbeat`, `_kanban_comment`, `_kanban_link`.
- `bauer/tool_router.py:982-1058` — the 9 registrations, e.g.:
  ```python
  self._tools["kanban_create"] = {
      "fn": self._kanban_create,
      "description": "Cria nova tarefa no board Kanban. Retorna o ID da tarefa.",
      "args": {
          "title": "str — titulo da tarefa (obrigatorio)",
          "description": "str — detalhes da tarefa (opcional)",
          "assignee": "str — agente/usuario responsavel (opcional)",
          "priority": "str — low | medium | high | critical (default: medium)",
          "status": "str — todo | ready | in_progress | blocked | failed | done (default: todo)",
          "parent_id": "str — ID da tarefa pai para sub-tarefas (opcional)",
      },
  }
  self._tools["kanban_list"] = {
      "fn": self._kanban_list,
      "description": "Lista tarefas do board com filtros por status, assignee ou prioridade.",
      "args": {
          "status": "str — todo | ready | in_progress | blocked | failed | done | all (default: all)",
          "assignee": "str — filtrar por responsavel (opcional)",
          "priority": "str — low | medium | high | critical (opcional)",
      },
  }
  self._tools["kanban_show"] = {
      "fn": self._kanban_show,
      "description": "Exibe detalhes completos de uma tarefa: descricao, historico, comentarios.",
      "args": {"task_id": "str — ID da tarefa (obrigatorio)"},
  }
  self._tools["kanban_complete"] = {
      "fn": self._kanban_complete,
      "args": {"task_id": "str — ID da tarefa (obrigatorio)", "result": "str — resumo do resultado/handoff (opcional)"},
  }
  self._tools["kanban_block"] = {
      "fn": self._kanban_block,
      "args": {"task_id": "str — ID da tarefa (obrigatorio)", "reason": "str — motivo do bloqueio (obrigatorio)"},
  }
  self._tools["kanban_unblock"] = {
      "fn": self._kanban_unblock,
      "args": {"task_id": "str — ID da tarefa (obrigatorio)", "note": "str — nota (opcional)"},
  }
  self._tools["kanban_heartbeat"] = {
      "fn": self._kanban_heartbeat,
      "args": {"task_id": "str — ID da tarefa (obrigatorio)", "progress": "str — descricao do progresso atual (obrigatorio)"},
  }
  self._tools["kanban_comment"] = {
      "fn": self._kanban_comment,
      "args": {"task_id": "str — ID da tarefa (obrigatorio)", "comment": "str — texto do comentario (obrigatorio)", "author": "str — autor (default: agent)"},
  }
  self._tools["kanban_link"] = {
      "fn": self._kanban_link,
      "args": {"parent_id": "str — ID da tarefa pai (obrigatorio)", "child_id": "str — ID da tarefa filha (obrigatorio)"},
  }
  ```
  (full descriptions omitted above for brevity — read the live file for
  exact wording, they're one-liners per tool.)
- `bauer/tool_router.py:190-215` — `_TOOL_SECURITY` entries for all 9 (see
  Plan 031's excerpt for the exact permission/risk/approval values — all
  `permission: "write"`, `risk: "low"` except `kanban_show`/`kanban_list`
  which are `permission: "read"`).
- `bauer/tool_router.py:319-323` — `_CHAT_CONTEXT_DENYLIST` (see "Why this
  matters" — verify dead/unused in Step 1 before relying on that).
- `bauer/tools/cronjob.py:61-67` (`_cronjob`) and
  `bauer/tools/agent_misc.py:168-174` (`_process`) — the established
  multiplex pattern to follow for shape/style (an `action` dispatch
  dict/if-elif inside one method).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Related tests | `uv run pytest tests/ -q -k "kanban"` | all pass |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/tools/kanban.py` (add `_kanban_read`/`_kanban_write` dispatchers)
- `bauer/tool_router.py` (registration block, `_TOOL_SECURITY`, and — only
  if Step 1 finds it's actually live — `_CHAT_CONTEXT_DENYLIST` migration)
- Tests exercising all 9 original kanban tools

**Out of scope** (do NOT touch, even though they look related):
- `bauer/kanban_store.py`, `bauer/kanban_db.py`, `bauer/kanban_decompose.py`,
  `bauer/kanban_diagnostics.py`, `bauer/kanban_migration.py`,
  `bauer/kanban_server.py`, `bauer/kanban_specify.py`, `bauer/kanban_swarm.py`
  — the underlying kanban data layer; unchanged. This plan only touches the
  *tool-calling surface* (`bauer/tools/kanban.py` and its registration).
- `bauer/commands/boards_cmd.py`, `bauer/commands/task_cmd.py` — the CLI
  commands for kanban; unrelated to the agent tool-calling surface,
  unchanged.
- Do not change any of the 9 underlying `_kanban_*` implementation methods'
  logic — only add a dispatcher in front of them.

## Git workflow

- Branch: `refactor/043-multiplex-kanban-tools`
- Commit message style: conventional commits matching recent history, e.g.
  `refactor(tools): unifica 9 tools kanban_* em kanban_read/kanban_write`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Verify `_CHAT_CONTEXT_DENYLIST` is genuinely unused

```bash
grep -rn "_CHAT_CONTEXT_DENYLIST" --include="*.py" bauer tests
```

**Decision rule**:
- If the only hit is the definition itself (`tool_router.py:319-323`) →
  it's dead configuration; proceed with the multiplex in Steps 2-3 with no
  special per-action gating logic needed (note this finding in the commit
  message, and see Maintenance notes for reporting it separately).
- If you find a real consumer (e.g. `execute()` checking `if tool_name in
  _CHAT_CONTEXT_DENYLIST and context == "chat": raise ...`) that this
  plan's excerpt missed → the risk is real as originally assessed; you must
  replicate that gating logic *inside* `_kanban_write`'s dispatch (deny
  `action in {"heartbeat", "complete", "block"}` when
  `self._context == "chat"` or however the router tracks current context —
  read the real consumer code to match its exact condition before writing
  the replacement).

**Verify**: state which case applies, with the grep output as evidence.

### Step 2: Add `_kanban_read` and `_kanban_write` dispatchers

In `bauer/tools/kanban.py`, add two new methods (place near the existing
live `_kanban_*` methods):

```python
def _kanban_read(self, args: dict) -> str:
    mode = str(args.get("mode", "")).strip()
    if mode == "list":
        return self._kanban_list(args)
    if mode == "show":
        return self._kanban_show(args)
    raise ToolError(f"kanban_read: 'mode' invalido {mode!r}. Use 'list' ou 'show'.")

def _kanban_write(self, args: dict) -> str:
    action = str(args.get("action", "")).strip()
    handlers = {
        "create": self._kanban_create,
        "complete": self._kanban_complete,
        "block": self._kanban_block,
        "unblock": self._kanban_unblock,
        "heartbeat": self._kanban_heartbeat,
        "comment": self._kanban_comment,
        "link": self._kanban_link,
    }
    handler = handlers.get(action)
    if handler is None:
        raise ToolError(
            f"kanban_write: 'action' invalido {action!r}. "
            f"Use um de: {', '.join(sorted(handlers))}."
        )
    # Only apply if Step 1 found _CHAT_CONTEXT_DENYLIST is actually live —
    # otherwise omit this block entirely.
    # if action in {"heartbeat", "complete", "block"} and <live context check>:
    #     raise ToolError("kanban_write: acao nao permitida neste contexto.")
    return handler(args)
```

Each existing `_kanban_*` method already takes `args: dict` and reads its
own required keys (`task_id`, `title`, etc.) — no signature change needed,
`_kanban_write` just forwards the same `args` dict through.

**Verify**: `uv run python -c "import ast; ast.parse(open('bauer/tools/kanban.py', encoding='utf-8').read())"` → exits 0.

### Step 3: Replace the 9 registrations with 2 in `tool_router.py`

Replace `self._tools["kanban_create"] = {...}` through
`self._tools["kanban_link"] = {...}` (`tool_router.py:982-1058`) with:

```python
self._tools["kanban_read"] = {
    "fn": self._kanban_read,
    "description": "Le tarefas do board Kanban — listar ou exibir detalhes de uma tarefa.",
    "args": {
        "mode": "str — 'list' ou 'show' (obrigatorio)",
        "task_id": "str — ID da tarefa (obrigatorio para mode=show)",
        "status": "str — filtro de status para mode=list (opcional)",
        "assignee": "str — filtro de responsavel para mode=list (opcional)",
        "priority": "str — filtro de prioridade para mode=list (opcional)",
    },
}
self._tools["kanban_write"] = {
    "fn": self._kanban_write,
    "description": (
        "Modifica o board Kanban — criar, completar, bloquear, desbloquear, "
        "enviar heartbeat, comentar ou linkar tarefas via 'action'."
    ),
    "args": {
        "action": "str — create | complete | block | unblock | heartbeat | comment | link (obrigatorio)",
        "task_id": "str — ID da tarefa (obrigatorio, exceto action=create/link)",
        "title": "str — titulo (action=create)",
        "description": "str — detalhes (action=create, opcional)",
        "assignee": "str — responsavel (action=create, opcional)",
        "priority": "str — low|medium|high|critical (action=create, opcional)",
        "parent_id": "str — tarefa pai (action=create para sub-tarefa, ou action=link)",
        "child_id": "str — tarefa filha (action=link, obrigatorio)",
        "result": "str — resumo do resultado (action=complete, opcional)",
        "reason": "str — motivo do bloqueio (action=block, obrigatorio)",
        "note": "str — nota de resolucao (action=unblock, opcional)",
        "progress": "str — descricao do progresso (action=heartbeat, obrigatorio)",
        "comment": "str — texto do comentario (action=comment, obrigatorio)",
        "author": "str — autor do comentario (action=comment, opcional)",
    },
}
```

**Verify**: `grep -c "self._tools\[\"kanban_" bauer/tool_router.py` →
exactly 2.

### Step 4: Update `_TOOL_SECURITY`

Replace the 9 `kanban_*` entries in `_TOOL_SECURITY`
(`tool_router.py:190-215`) with:
```python
"kanban_read": {"permission": "read", "risk": "low", "approval": False},
"kanban_write": {"permission": "write", "risk": "low", "approval": False},
```
(matching the least-restrictive-common-denominator of the originals — all
9 were `risk: "low"`, `approval: False`; confirm this during Step 4 by
re-reading the full original 9 entries, not just the excerpt above, in case
any had `approval: True` that this plan's excerpt missed.)

**Verify**: `grep -n "kanban_create\|kanban_list\|kanban_show\|kanban_complete\|kanban_block\|kanban_unblock\|kanban_heartbeat\|kanban_comment\|kanban_link" bauer/tool_router.py`
→ no matches remain in `_TOOL_SECURITY` (only in comments/docs if any).

## Test plan

- For each of the 9 original tests covering `kanban_create`/`kanban_list`/
  etc. (find via `grep -rln "kanban_create\|kanban_complete\|kanban_block\|kanban_unblock\|kanban_heartbeat\|kanban_comment\|kanban_link" tests/`),
  update to call `router._kanban_write({"action": "create", ...})` (etc.)
  or `router._kanban_read({"mode": "list", ...})` — same assertions, routed
  through the new dispatchers.
- **New test**: `test_kanban_write_invalid_action_raises`.
- **New test**: `test_kanban_read_invalid_mode_raises`.
- If Step 1 found a live `_CHAT_CONTEXT_DENYLIST` consumer: add a test
  confirming `kanban_write` with `action="heartbeat"`/`"complete"`/
  `"block"` is still denied in chat context, matching the original
  per-tool-name denial's test (if one exists).

Verification: `uv run pytest tests/ -q -k "kanban"` → all pass.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `bauer/tools/kanban.py` has `_kanban_read`/`_kanban_write` dispatchers
- [ ] `bauer/tool_router.py` registers exactly `kanban_read`/`kanban_write`
      (`grep -c "self._tools\[\"kanban_" bauer/tool_router.py` → 2)
- [ ] `_TOOL_SECURITY` has exactly 2 kanban entries
- [ ] If Step 1 found `_CHAT_CONTEXT_DENYLIST` live: the equivalent gating
      is preserved inside `_kanban_write` and covered by a test; if dead:
      it's flagged in the commit message as a separate tiny finding
- [ ] `uv run pytest tests/ -q -k "kanban"` exits 0, including new tests
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 043 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1 finds `_CHAT_CONTEXT_DENYLIST` (or equivalent per-action gating)
  IS actually consumed somewhere this plan's grep missed — implement the
  replicated gating carefully per the real logic, and if the real logic is
  more complex than a simple set-membership check, report instead of
  approximating it.
- Any of the 9 original `_TOOL_SECURITY` entries has `approval: True` (this
  plan's excerpt assumed all `False`) — re-derive the merged entries'
  `approval` value as the OR of all 9 originals' values, and report if that
  makes a single merged risk/approval value inappropriate (i.e. if
  `kanban_block`'s risk profile genuinely differs enough from
  `kanban_create`'s that merging them under one `kanban_write` security
  entry is wrong — in that case, STOP and propose per-action security
  checking inside `_kanban_write` instead of a single flat entry).
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- If Step 1 confirms `_CHAT_CONTEXT_DENYLIST` is dead code, file it as a
  tiny separate follow-up (delete the unused constant at
  `tool_router.py:319-323`, or wire it into `execute()` if the intent was
  real and just never got connected — that's a judgment call for whoever
  picks it up, not this plan).
- A reviewer should confirm the new `kanban_write`'s single `args` schema
  (which lists every field for every action, most marked optional/
  action-specific) is still clear enough for a model to construct correctly
  — this is the main readability cost of multiplexing 7 actions into one
  tool; if it proves confusing in practice, consider splitting the args
  documentation more explicitly by action in the description text.
