# Plan 038: Nest `telegram`/`discord` under `gateway` and `skills-hub`/`skills-bundle` under `skills`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/cli.py bauer/commands/telegram_cmd.py bauer/commands/discord_cmd.py bauer/commands/gateway_cmd.py bauer/commands/skills_hub_cmd.py bauer/commands/skills_bundle_cmd.py bauer/commands/skills_cmd.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: dx
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`bauer --help` currently lists 4 top-level nouns
(`telegram`/`discord`/`skills-hub`/`skills-bundle`) that are each a thin
shortcut for a parent command group that already exists:
`telegram_cmd.py`'s own docstrings literally say *"Sobe só o bridge
Telegram/Discord. Para todos os canais use `bauer gateway start`"* — the
code itself identifies `gateway` as the intended umbrella. `skills-hub` and
`skills-bundle` are two more top-level nouns for the same "skills" concern
that `skills` already covers, registered far away in `cli.py` (lines
2322-2326, physically separated from the other command registrations
around line 91-174) instead of nested under `skills_app`. The codebase
already has a working precedent for exactly this kind of nesting —
`bauer runtime agents` / `bauer runtime teams` are subgroups of
`runtime_app`, not top-level commands. This plan applies that same pattern
to reduce `bauer --help`'s top-level noun count by up to 4, with zero
behavior change to the underlying bridge/hub/bundle logic — only how the
commands are reached.

## Current state

- `bauer/commands/telegram_cmd.py:11` — `telegram_app = typer.Typer(help=
  "Telegram Bridge — agente Bauer via Telegram")`, commands `start` (`:14`,
  docstring: *"Sobe só o bridge Telegram. Para todos os canais use `bauer
  gateway start`."*), `stop` (`:29`), `test` (`:38`).
- `bauer/commands/discord_cmd.py:10` — `discord_app = typer.Typer(help=
  "Discord Bridge — agente Bauer via Discord")`, one command: `start`
  (`:13`, docstring: *"Sobe só o bridge Discord. Para todos os canais use
  `bauer gateway start`."*).
- `bauer/commands/gateway_cmd.py:14` — `gateway_app = typer.Typer(help=
  "Bauer Gateway — todos os canais de chat + entrega do outbox")`. Already
  nests a subgroup: `gateway_app.add_typer(gateway_service_app,
  name="service")` at `:20` — this is the exact pattern to replicate for
  `telegram`/`discord`.
- `bauer/commands/skills_hub_cmd.py` — `skills_hub_app`, curated built-in
  skill catalog browse/install (`hub install` per prior audit).
- `bauer/commands/skills_bundle_cmd.py` — `skills_bundle_app`, skill bundle
  grouping.
- `bauer/commands/skills_cmd.py` — `skills_app` (imported at `cli.py:91`),
  the primary skills command group.
- `bauer/cli.py` — current registrations:
  ```python
  # ~line 91
  from bauer.commands.skills_cmd import skills_app  # noqa: E402
  # ~line 99-100
  from bauer.commands.telegram_cmd import telegram_app  # noqa: E402
  from bauer.commands.discord_cmd import discord_app  # noqa: E402
  # ~line 159
  app.add_typer(skills_app, name="skills", rich_help_panel=PANEL_MEM)
  # ~line 163-164
  app.add_typer(telegram_app, name="telegram", rich_help_panel=PANEL_CONN)
  app.add_typer(discord_app, name="discord", rich_help_panel=PANEL_CONN)
  ...
  # ~line 2322-2326 (far away from the block above)
  from bauer.commands.skills_hub_cmd import skills_hub_app  # noqa: E402
  app.add_typer(skills_hub_app, name="skills-hub", rich_help_panel=PANEL_MEM)

  from bauer.commands.skills_bundle_cmd import skills_bundle_app  # noqa: E402
  app.add_typer(skills_bundle_app, name="skills-bundle", rich_help_panel=PANEL_MEM)
  ```
- Existing nesting precedent: `bauer/commands/runtime_cmd.py` registers
  `runtime_app.add_typer(agents_app, name="agents")` and similarly for
  `teams` (per prior audit) — follow this exact style.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| CLI help | `uv run python -m bauer.cli --help` | exit 0 |
| Nested help | `uv run python -m bauer.cli gateway telegram --help` | exit 0, shows start/stop/test |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/cli.py` (registration changes only)
- `bauer/commands/gateway_cmd.py` (add the two `add_typer` nesting calls)
- `bauer/commands/skills_cmd.py` (add the two `add_typer` nesting calls)
- Any tests that invoke `telegram`/`discord`/`skills-hub`/`skills-bundle`
  as top-level CLI paths (update invocation paths only)

**Out of scope** (do NOT touch, even though they look related):
- `bauer/commands/telegram_cmd.py`, `bauer/commands/discord_cmd.py`,
  `bauer/commands/skills_hub_cmd.py`, `bauer/commands/skills_bundle_cmd.py`
  — the command **bodies** (the `telegram_app`/`discord_app`/
  `skills_hub_app`/`skills_bundle_app` Typer instances and their commands)
  do not change at all; only where they get registered.
- `bauer/telegram_bridge.py`, `bauer/discord_bridge.py` — the actual bridge
  implementations; untouched.
- Do not remove top-level aliases unless Step 3 confirms nothing else
  depends on the old paths — see the decision point in Step 3.

## Git workflow

- Branch: `chore/038-nest-thin-command-groups`
- Commit message style: conventional commits matching recent history, e.g.
  `chore(cli): aninha telegram/discord sob gateway, skills-hub/bundle sob skills`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Nest `telegram`/`discord` under `gateway`

In `bauer/commands/gateway_cmd.py`, import the two Typer apps and register
them as nested subgroups, following the exact pattern already used for
`gateway_service_app` at `gateway_cmd.py:20`:

```python
from .telegram_cmd import telegram_app
from .discord_cmd import discord_app
...
gateway_app.add_typer(telegram_app, name="telegram")
gateway_app.add_typer(discord_app, name="discord")
```

Place these lines near the existing `gateway_app.add_typer(gateway_service_app,
name="service")` call at `gateway_cmd.py:20`.

**Verify**: `uv run python -m bauer.cli gateway telegram --help` → exits 0,
lists `start`/`stop`/`test`. Same for `uv run python -m bauer.cli gateway
discord --help` → lists `start`.

### Step 2: Nest `skills-hub`/`skills-bundle` under `skills`

In `bauer/commands/skills_cmd.py`, import and register the two apps as
nested subgroups:

```python
from .skills_hub_cmd import skills_hub_app
from .skills_bundle_cmd import skills_bundle_app
...
skills_app.add_typer(skills_hub_app, name="hub")
skills_app.add_typer(skills_bundle_app, name="bundle")
```

**Verify**: `uv run python -m bauer.cli skills hub --help` → exits 0. Same
for `uv run python -m bauer.cli skills bundle --help`.

### Step 3: Remove the old top-level registrations from `cli.py`

Remove the 4 `add_typer` calls and their imports for `telegram_app`,
`discord_app`, `skills_hub_app`, `skills_bundle_app` from `bauer/cli.py`
(the lines listed in "Current state").

**Decision point**: before removing, run
`grep -rn "\"telegram\"\|\"discord\"\|\"skills-hub\"\|\"skills-bundle\"" tests/`
to check whether any test invokes these as top-level CLI paths (e.g. via a
Typer `CliRunner` with `["telegram", "start", ...]`). If found, update those
tests to the new nested paths (`["gateway", "telegram", "start", ...]`)
rather than keeping a backward-compatible top-level alias — no evidence was
found during the audit of any external user/script depending on the
top-level form (bridges are typically started via `bauer gateway start`
already, and CLI-invocation test coverage was confirmed at 0 hits for
`telegram`/`discord` specifically).

**Verify**: `uv run python -m bauer.cli --help` → exits 0; output no longer
lists `telegram`, `discord`, `skills-hub`, or `skills-bundle` as top-level
commands.

## Test plan

- Update any test that invokes the old top-level CLI paths (per Step 3's
  grep) to use the new nested paths instead — same assertions, different
  invocation args.
- No new test logic needed beyond invocation-path updates, since command
  bodies are unchanged.
- Add one smoke test per nested group if none exists: invoke
  `["gateway", "telegram", "--help"]`, `["gateway", "discord", "--help"]`,
  `["skills", "hub", "--help"]`, `["skills", "bundle", "--help"]` via the
  Typer `CliRunner` (or however existing CLI tests in this repo invoke
  commands — check `tests/test_cli_*.py` or similar for the pattern) and
  assert exit code 0.

Verification: `uv run pytest tests/ -q --tb=short` → all pass.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `uv run python -m bauer.cli --help` no longer lists `telegram`,
      `discord`, `skills-hub`, `skills-bundle` as top-level commands
- [ ] `uv run python -m bauer.cli gateway telegram --help`,
      `gateway discord --help`, `skills hub --help`, `skills bundle --help`
      all exit 0
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 038 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 3's grep finds evidence of real external usage of the top-level
  command paths (beyond tests, e.g. referenced in `README.md`,
  `install.sh`/`install.ps1`, or `CHANGELOG.md` as a documented stable
  interface) — consider keeping a deprecated top-level alias instead of a
  clean removal, and report this tradeoff instead of deciding unilaterally.
- A step's verification fails twice after a reasonable fix attempt.
- Typer's `add_typer` nesting behaves unexpectedly (e.g. help text
  formatting breaks, or command discovery fails) — report the exact error
  rather than working around it with a different mechanism.

## Maintenance notes

- If `README.md` or other docs reference `bauer telegram start` /
  `bauer discord start` / `bauer skills-hub ...` / `bauer skills-bundle ...`
  directly, update those references to the new nested form as a follow-up
  (grep `README.md`, `docs/`, `CHANGELOG.md` for these strings — not done
  as part of this plan's in-scope files, but worth a quick check).
- A reviewer should confirm the `rich_help_panel` grouping (`PANEL_CONN` for
  telegram/discord, `PANEL_MEM` for skills-hub/bundle) is no longer needed
  at the top-level `add_typer` call sites (removed along with the
  registrations) and that `gateway`/`skills`'s own panel assignment already
  covers the nested commands appropriately in `--help` output.
