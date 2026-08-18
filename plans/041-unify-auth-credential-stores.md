# Plan 041: Document precedence between `bauer auth` and `bauer credential`, add provenance to `auth status`

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat d903de8..HEAD -- bauer/commands/_runtime.py bauer/commands/auth_cmd.py bauer/commands/credential_cmd.py bauer/auth.py bauer/credential_pool.py`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P3
- **Effort**: M
- **Risk**: MED (touches the client-build hot path used by every LLM call —
  read-only additions should be safe, but this path is sensitive)
- **Depends on**: none
- **Category**: dx
- **Planned at**: commit `d903de8`, 2026-08-18

## Why this matters

Bauer has two independent provider-credential stores: `bauer auth`
(`bauer/auth.py`, `~/.bauer/auth.json`, Fernet/PBKDF2-encrypted, driven by
`bauer/commands/auth_cmd.py`'s `login`/`status`/`logout`/`providers`) and
`bauer credential` (`bauer/credential_pool.py`, `~/.bauer/
credential_pool.json` + OS keyring, driven by
`bauer/commands/credential_cmd.py`'s `set`/`get`/`list`/`delete`). Both are
keyed by provider name (e.g. `"openai"`, `"groq"`). A user has two different
commands to "set an API key" for the same provider, with different storage
mechanisms and different security posture (Fernet-only vs. OS-keychain-
first). The precedence between them is real but **only visible by reading
`bauer/commands/_runtime.py`'s `_build_client` function** — `AuthManager`
(`bauer auth`) is checked first, then `credential_pool` (`bauer credential`)
is used as an overlay/fallback onto whatever config value exists. Neither
command's `--help` mentions the other exists or which one wins if both are
set for the same provider. This plan makes the split visible and
documented — including a `bauer auth status --all-sources` that shows,
per-provider, which store actually supplied the key currently in use — the
lowest-risk fix that resolves the confusion without merging two live,
differently-secured storage mechanisms (a bigger, riskier change this plan
deliberately does not attempt).

## Current state

- `bauer/commands/_runtime.py:104-140+` (`_build_client`) — precedence
  logic, excerpt:
  ```python
  provider = cfg.model.provider

  # G11: credential pool overlay — keychain → encrypted file → config/env fallback
  try:
      from ..credential_pool import _cpool as _get_cpool
      _pool = _get_cpool()
  except Exception:
      _pool = None

  def _key(provider_name: str, raw: str) -> str:
      if _pool is None:
          return raw
      return _pool.get(provider_name, fallback=raw)

  # Verifica se há token autenticado via bauer auth
  try:
      from ..auth import AuthManager
      auth = AuthManager()
      token = auth.store.load(provider) or auth.store.load(f"{provider}-api")
      if token:
          ...  # uses the bauer-auth token
  ```
  Read the full function (continues past line 144) to confirm the exact
  final precedence order before writing the documentation in Step 2 — do
  not guess at the fallthrough behavior when neither `auth` nor
  `credential_pool` has a value.
- `bauer/commands/auth_cmd.py:50-55` — existing `auth status` command:
  ```python
  @auth_app.command("status")
  def auth_status():
      """Mostra providers autenticados e status dos tokens."""
      from ..auth import cmd_status
      cmd_status()
  ```
  `cmd_status()` lives in `bauer/auth.py` — read it to find its current
  output format before extending it in Step 3.
- `bauer/commands/credential_cmd.py` — `credential_app` with `set`/`get`/
  `list`/`delete`, operating on `bauer/credential_pool.py`'s
  `CredentialPool`/`_cpool()`.
- `bauer/auth.py` — `AuthManager`, `~/.bauer/auth.json` (Fernet/PBKDF2).
- `bauer/credential_pool.py` — `CredentialPool`, 3-layer resolution
  (keychain → Fernet file → config/env fallback per the comment at
  `_runtime.py:106`).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| CLI help | `uv run python -m bauer.cli auth --help` | exit 0, shows updated help text |
| Full test suite | `uv run pytest tests/ -q --tb=short` | all pass |
| Lint (blocking) | `uv run ruff check bauer/ --select E9,F63,F7,F82` | exit 0 |

## Scope

**In scope**:
- `bauer/commands/auth_cmd.py` (extend `status` with `--all-sources`; update
  help text)
- `bauer/commands/credential_cmd.py` (update help text only — cross-
  reference `bauer auth`)
- `bauer/auth.py` (extend `cmd_status` or add a new function for the
  provenance display, per Step 3)
- `AGENTS.md` or `README.md` (add a short precedence-documentation section
  — pick whichever already documents `bauer auth`/`bauer credential`
  today; check both)

**Out of scope** (do NOT touch, even though they look related):
- Do NOT merge the two storage backends, change which one wins, or migrate
  data between them — that's a larger, riskier change (see Maintenance
  notes) explicitly deferred by this plan.
- `bauer/commands/_runtime.py`'s `_build_client` precedence *logic* itself
  — this plan only adds a read-only introspection command, it does not
  change which credential actually gets used for real requests.
- `bauer/credential_pool.py`'s keyring-availability fallback chain —
  unchanged.

## Git workflow

- Branch: `docs/041-document-auth-credential-precedence`
- Commit message style: conventional commits matching recent history, e.g.
  `docs(auth): documenta precedencia auth vs credential + auth status --all-sources`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: Read `_build_client` in full to confirm the exact precedence order

Read `bauer/commands/_runtime.py` starting at line 104 through the end of
`_build_client` (do not stop at line 144 — that's a partial excerpt). Write
down the precise order of checks: does `bauer auth`'s token, if present,
always win outright? Does `credential_pool`'s `_key()` overlay apply even
when `bauer auth` also has a value, or only when `bauer auth` has none?
What's the final fallback if neither has anything (raw config/env value)?

**Verify**: you can state the precedence as an ordered list (e.g. "1. bauer
auth token if present and not expired; 2. credential_pool overlay on the
config value; 3. raw config/env value") with `file:line` citations for each
step.

### Step 2: Update both commands' `--help` text to cross-reference each other

In `bauer/commands/auth_cmd.py`, update `auth_app`'s Typer `help=` string
(and/or add a note to the `login` command's docstring) to state: *"Para
segredos genéricos (não-LLM-provider) ou para preferir o OS keychain, veja
`bauer credential`. Em caso de ambos configurados para o mesmo provider,
`bauer auth` tem precedência — veja `bauer auth status --all-sources`."*
(Adjust wording to match Step 1's actual findings if precedence differs
from this assumption.)

In `bauer/commands/credential_cmd.py`, update `credential_app`'s help
string similarly: *"Para credenciais de provider LLM (usadas em `bauer
agent`/`bauer run`), prefira `bauer auth login`. Este comando é o cofre
genérico de segredos (keychain-first) usado como fallback."*

**Verify**: `uv run python -m bauer.cli auth --help` and
`uv run python -m bauer.cli credential --help` both show the updated
cross-referencing text.

### Step 3: Add `bauer auth status --all-sources`

In `bauer/auth.py`, extend `cmd_status()` (or add a new function
`cmd_status_all_sources()`) to, for each known provider, check **both**
`AuthManager.store.load(provider)` and `credential_pool._cpool().get(provider,
fallback=None)` (read-only checks, no side effects) and print which
source(s) have a value and which one `_build_client` would actually use
per Step 1's precedence order. Wire this into `auth_cmd.py`:

```python
@auth_app.command("status")
def auth_status(
    all_sources: bool = typer.Option(False, "--all-sources", help="Mostra proveniencia (auth vs credential) por provider."),
):
    """Mostra providers autenticados e status dos tokens."""
    from ..auth import cmd_status, cmd_status_all_sources

    if all_sources:
        cmd_status_all_sources()
    else:
        cmd_status()
```

(Adjust the exact function names to match whatever's idiomatic in
`bauer/auth.py` — the goal is a `--all-sources` flag on the existing `auth
status` command, not necessarily this exact function split.)

**Verify**: `uv run python -m bauer.cli auth status --all-sources` → exits
0, output shows per-provider source attribution (even with no providers
configured, it should print something like "nenhum provider configurado em
nenhuma fonte" rather than erroring).

### Step 4: Add the precedence documentation

Add a short section (5-10 lines) to `AGENTS.md` (near wherever
authentication is already documented, if anywhere) or `README.md`
explaining: two credential stores exist, why, and the precedence order from
Step 1. Cross-reference from both.

**Verify**: `grep -n "bauer auth\|bauer credential" AGENTS.md README.md` →
shows the new documentation.

## Test plan

- **New test**: `test_auth_status_all_sources_shows_provenance` — mock both
  `AuthManager.store.load` and the credential pool's `get` to return known
  values for a test provider, invoke the CLI command, assert the output
  names both sources and indicates which wins. Model after existing
  `auth_cmd.py` tests' mocking approach.
- **New test**: `test_auth_status_all_sources_no_providers_configured` —
  confirm graceful (non-erroring) output when nothing is configured
  anywhere.
- Existing `auth status` (no flag) behavior must be unchanged — confirm via
  existing tests.

Verification: `uv run pytest tests/ -q -k "auth"` → all pass, including new
tests. Then `uv run pytest tests/ -q --tb=short` for the full suite.

## Done criteria

Machine-checkable. ALL must hold:

- [ ] `uv run python -m bauer.cli auth status --all-sources` exits 0 and
      shows per-provider source attribution
- [ ] `uv run python -m bauer.cli auth --help` and
      `uv run python -m bauer.cli credential --help` cross-reference each
      other
- [ ] `AGENTS.md` or `README.md` documents the precedence order with
      `file:line` grounding matching Step 1's findings
- [ ] `uv run pytest tests/ -q --tb=short` exits 0
- [ ] `uv run ruff check bauer/ --select E9,F63,F7,F82` exits 0
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` status row for 041 updated

## STOP conditions

Stop and report back (do not improvise) if:

- Step 1's precedence order is more complex/conditional than a simple
  ordered list (e.g. varies per-provider, or depends on token expiry state
  in a way that's hard to summarize statically) — document the actual
  complexity rather than oversimplifying in the new help text/docs.
- Adding the `--all-sources` check triggers any network call or side effect
  (e.g. token refresh) — this must be a read-only introspection command;
  if `AuthManager.store.load` or the credential pool's `get` have side
  effects you can't avoid, report instead of shipping a command that
  mutates state under a "status" name.
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- This plan deliberately does not merge the two stores or change
  `_build_client`'s actual runtime behavior — it only adds visibility. A
  future, separate, higher-risk plan could consolidate them (e.g. make
  `credential_pool` the single store and have `auth login` write into it),
  but that touches the hot path every LLM call goes through and needs its
  own careful plan with dedicated testing for keyring-available and
  keyring-absent environments — do not attempt that here.
- A reviewer should confirm the new `--all-sources` output doesn't
  accidentally print any actual secret value — only provider names, which
  source(s) have *a* value, and which one wins (per Hard Rule 4 from the
  advisor session that generated this plan: never reproduce secret values).
