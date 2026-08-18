# Plan 046: Delete the stale `tools.yaml` catalog (documents 7 of 84 tools, zero runtime effect)

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- tools.yaml bauer/tool_router.py`
> If either file changed since this plan was written, compare the "Current
> state" excerpts against the live code before proceeding; on a mismatch,
> treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: docs / tech-debt
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

`tools.yaml` (repo root) presents itself as *"Catálogo de tools disponíveis
no Bauer Tool Bridge"* but documents exactly 7 of the 84 real tools the
codebase registers (`list_dir`, `read_file`, `write_file`, `search_text`,
`run_command`, `web_search`, `web_fetch`) — 8% coverage. Its only functional
consumer is `bauer/tool_router.py`'s `_load_tool_timeouts_from_yaml()`
(`tool_router.py:290-303`), which optionally overrides `_TOOL_TIMEOUTS` from
a `timeout_seconds` key per tool — and **none of the 7 entries in
`tools.yaml` actually set `timeout_seconds`**, so the file currently has
**zero effect on runtime behavior**. There is also no existing per-tool
timeout override surface anywhere else (`config.yaml`'s `agent.tool_timeout_s`
at `config.yaml.example:147` is a single *global* timeout, not per-tool).
Anyone — human or agent — reading `tools.yaml` to understand "what tools
does Bauer have" gets a picture that's both incomplete and describes fields
(`disabled_by_default`, `blocked_patterns`) that don't reflect where the
real gating logic actually lives today (`_TOOL_SECURITY`, `tool_policy.py`,
`tool_guardrails.py`, the allowlist mechanism in `commands/_runtime.py`).
A stale doc that looks authoritative is worse than no doc — this plan
removes it.

## Current state

- `tools.yaml:1-4` — header:
  ```yaml
  # tools.yaml — Catálogo de tools disponíveis no Bauer Tool Bridge.
  # Referência legível. O ToolRouter usa este arquivo para documentar as tools,
  # mas a lógica de execução fica em tool_router.py.

  tools:
  ```
  followed by 7 entries (`list_dir`, `read_file`, `write_file`,
  `search_text`, and 3 more per prior audit — confirm the exact 7 by
  reading the full file before deleting, in case it's grown since this plan
  was written).
- `bauer/tool_router.py:290-308`:
  ```python
  def _load_tool_timeouts_from_yaml() -> None:
      """Override _TOOL_TIMEOUTS from tools.yaml timeout_seconds keys (if present)."""
      try:
          from pathlib import Path as _Path
          import yaml as _yaml
          _p = _Path(__file__).parent.parent / "tools.yaml"
          if not _p.exists():
              return
          data = _yaml.safe_load(_p.read_text())
          for tool_name, cfg in (data.get("tools") or {}).items():
              if isinstance(cfg, dict) and "timeout_seconds" in cfg:
                  _TOOL_TIMEOUTS[tool_name] = int(cfg["timeout_seconds"])
      except Exception:
          pass  # Never crash on config load

  try:
      _load_tool_timeouts_from_yaml()
  except Exception:
      pass
  ```
  Note this function **already guards on `if not _p.exists(): return`** —
  deleting `tools.yaml` makes this function a clean, silent no-op, exactly
  as it already effectively is today (since no entry sets
  `timeout_seconds`). **Do not delete this function** — it's a legitimate,
  harmless, currently-inert override mechanism; a future contributor could
  recreate `tools.yaml` with real `timeout_seconds` entries and it would
  work correctly. This plan only removes the misleading, incomplete catalog
  file, not the (currently unused but harmless) loading mechanism.
- `config.yaml.example:143-147` — the only actual per-tool-adjacent timeout
  config that exists, but it's global, not per-tool:
  ```yaml
  # Timeout por execução de tool (segundos). 0 = sem limite.
  # Range: 0.0 – 600.0 | Padrão: 30.0
  tool_timeout_s: 30.0
  ```
- `BauerAgent.md:93,833` — two mentions of `tools.yaml` in a directory-tree
  listing (no functional dependency, just documentation of repo layout);
  update these two lines to remove the now-nonexistent file from the tree.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |
| Confirm loader still safe | `uv run python -c "import bauer.tool_router"` | exit 0, no error |

## Scope

**In scope**:
- Delete `tools.yaml` (repo root)
- `BauerAgent.md` (remove the 2 directory-tree references)
- Any test that specifically asserts `tools.yaml`'s contents (check via
  Step 1)

**Out of scope** (do NOT touch, even though they look related):
- `bauer/tool_router.py`'s `_load_tool_timeouts_from_yaml()` function and
  its call site — leave this mechanism in place; it's not dead code, it's
  an inert-but-functional override path (see "Current state").
- `_TOOL_TIMEOUTS`, `_TOOL_SECURITY` — the real, live source of tool
  metadata; unaffected by this deletion.
- `config.yaml.example`'s `tool_timeout_s` — unrelated global setting;
  unchanged.

## Git workflow

- Branch: `chore/046-delete-stale-tools-yaml`
- Commit message style: conventional commits matching recent history, e.g.
  `chore(tools): remove tools.yaml — catalogo desatualizado, 8% de cobertura`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Check for test dependencies on `tools.yaml`'s existence/content

```bash
grep -rln "tools.yaml\|tools_yaml" tests/
```

**Verify**: note any hits. If a test specifically loads or asserts against
`tools.yaml`'s content (as opposed to testing
`_load_tool_timeouts_from_yaml()`'s *behavior* generically with a temp
file), that test needs updating in Step 3 — do not just delete a test that
would then fail; adapt it to construct its own temporary `tools.yaml`-like
fixture if it needs to test the loader function's mechanics.

### Step 2: Delete `tools.yaml`

Delete `tools.yaml` from the repo root.

**Verify**: `git status` shows the deletion. `uv run python -c "import bauer.tool_router"`
→ exits 0 (confirms `_load_tool_timeouts_from_yaml()`'s `if not
_p.exists(): return` guard handles the missing file cleanly, matching the
excerpt in "Current state").

### Step 3: Update `BauerAgent.md`'s directory tree

Remove the two `tools.yaml` lines from `BauerAgent.md` (around lines 93 and
833 per the current file — re-locate by searching for the string, line
numbers may have shifted).

Update any test found in Step 1 to no longer depend on `tools.yaml`
existing at the repo root (if it tests the loader function's mechanics
generically, point it at a `tmp_path`-created fixture file instead, per
this repo's stated convention in `AGENTS.md`: "Use `tmp_path`/`monkeypatch`,
nunca escreva na raiz do repo").

**Verify**: `grep -n "tools.yaml" BauerAgent.md` → no matches.

## Test plan

- No new tests needed — this is a doc/config-file deletion.
- If Step 1 found a test depending on `tools.yaml`'s existence, update it
  to use a `tmp_path` fixture instead (per repo convention), so
  `_load_tool_timeouts_from_yaml()`'s actual override behavior is still
  covered by a test — just not one that depends on a real file existing at
  a hardcoded repo-root path.

Verification: `uv run pytest tests/ -q --tb=short` → all pass.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `tools.yaml` no longer exists at the repo root
- [ ] `uv run python -c "import bauer.tool_router"` exits 0
- [ ] `grep -n "tools.yaml" BauerAgent.md` → no matches
- [ ] Any test that previously depended on `tools.yaml`'s existence now
      uses a `tmp_path` fixture instead (or no such test existed)
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 046 updated

## STOP conditions

Stop and report back (do not improvise) if:

- `_load_tool_timeouts_from_yaml()`'s guard has changed since this plan was
  written (e.g. it no longer checks `_p.exists()` before reading) — verify
  the function fails gracefully on a missing file before deleting the file
  it reads.
- Step 1 finds a test with real assertions against `tools.yaml`'s specific
  content (e.g. asserting exactly 7 tools are documented) that can't be
  cleanly adapted to a fixture — report instead of deleting test coverage.
- `uv run python -c "import bauer.tool_router"` fails after deletion —
  something unexpected depends on the file's presence.

## Maintenance notes

- If a real per-tool timeout override system is wanted in the future, the
  cleanest path is a small `tools:` section in `config.yaml` (parallel to
  the existing `ollama:`/`agent:` sections) rather than resurrecting a
  separate `tools.yaml` file — that would put per-tool timeout config in
  the same place as every other Bauer setting, and `_TOOL_SECURITY`'s
  existing 84-entry dict in `tool_router.py` could serve as the generated
  reference doc instead of a hand-maintained YAML catalog (a `bauer tools
  list` CLI command, if the repo has one, might already serve this
  documentation need better than a static file ever did — check before
  reinventing it).
- This deletion is independent of Plans 042/043 (lsp/kanban tool
  multiplexing) — none of those plans touch `tools.yaml`.
