"""Command approval system — three-tier defence against destructive shell calls.

Layered guards inspired by Hermes Agent's `tools/approval.py`:

1. **HARDLINE** (12 patterns) — unconditional block. Matches mean refuse to
   run the command, full stop. No user prompt, no allowlist bypass. Reserved
   for things that would brick the machine or wipe data irrecoverably:
   `rm -rf /`, `mkfs`, `dd of=/dev/sdX`, fork bombs, shutdown family,
   filesystem table corruption.

2. **DANGEROUS** (47+ patterns) — approvable. Matches mean prompt the user
   (or hit the auxiliary LLM in smart-approval mode). Once granted, the
   approval can scope to:
     - **once** — this exact command call
     - **session** — every call this session (ContextVar-scoped)
     - **always** — written to `~/.bauer/approvals.yaml` for future sessions

3. **Safe** — neither hardline nor dangerous. Run as-is.

Public surface::

    from bauer.approval import (
        detect_hardline_command,
        detect_dangerous_command,
        check_all_command_guards,
        ApprovalDecision,
        is_session_approved, approve_session, approve_permanent,
        load_permanent_allowlist, save_permanent_allowlist,
    )

    decision = check_all_command_guards(command, approval_callback=cli_prompt)
    if decision.action == "deny":
        raise CommandBlocked(decision.reason)
    if decision.action == "approved":
        # run it
        ...

The user-supplied `approval_callback(command, description) -> str` is invoked
when interactive approval is needed. It should return one of "once",
"session", "always", or "deny". CLI flows pass `prompt_dangerous_approval`;
non-interactive flows (cron, gateway) pass their own queue-backed approver.
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PatternMatch:
    """Result of a single pattern lookup. `key` is the machine-readable id;
    `description` is a human sentence we can surface in the approval prompt."""
    key: str
    description: str


@dataclass(frozen=True)
class ApprovalDecision:
    """Result of running a command through the guard pipeline.

    `action` is one of:
        - "approved": ok to run (either not dangerous, or user approved)
        - "denied": hardline match or user said no
    `reason`: human description (matches the pattern that triggered, or the
        user's response code).
    `pattern_key`: machine id of the pattern that fired (empty when safe).
    `scope`: when approved via dangerous match, one of "once", "session",
        "always", "preauth" (already in allowlist). Empty for hardline denials
        and safe commands.
    """
    action: str
    reason: str = ""
    pattern_key: str = ""
    scope: str = ""


# ---------------------------------------------------------------------------
# HARDLINE patterns — unconditional block
# ---------------------------------------------------------------------------


# Each entry: (key, regex, human description). All regexes match against the
# *normalised* command string (see `_normalise_command`). Use re.IGNORECASE
# at compile time for portability across shells.

_HARDLINE_PATTERNS: list[tuple[str, str, str]] = [
    # rm -rf / variants — recursively delete root
    # Allow flexible spacing and extra flag chars: -rf, -fr, -rfv, -rvf, etc.
    ("rm_rf_root",
     r"\brm\s+(?:-[a-z]*[rf][a-z]*[rf][a-z]*|--(?:force|recursive)(?:\s+--(?:force|recursive))*)\s+/(?:\s|$)",
     "rm -rf / (recursive delete of root filesystem)"),
    # mkfs.*: format any filesystem
    ("mkfs",
     r"\bmkfs(?:\.\w+)?\b",
     "mkfs (format filesystem)"),
    # dd if=/dev/zero of=/dev/sdX (or of any block device)
    ("dd_to_block_device",
     r"\bdd\b[^|;]*\bof=/dev/(?:sd|nvme|hd|mmcblk|xvd)",
     "dd writing to block device (will destroy partition)"),
    # Classic fork bomb
    ("fork_bomb",
     r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
     "fork bomb (`:(){:|:&};:`)"),
    # systemctl power state changes — checked BEFORE generic shutdown so the
    # more-specific key wins.
    ("systemctl_power",
     r"\bsystemctl\s+(?:poweroff|reboot|halt|emergency)\b",
     "systemctl power state change"),
    # Shutdown family
    ("shutdown",
     r"\b(?:shutdown|reboot|poweroff|halt|init\s+[06])\b",
     "system shutdown / reboot / poweroff"),
    # Wipe /etc/fstab (filesystem table)
    ("clobber_fstab",
     r"(?:>|>>)\s*/etc/fstab\b",
     "overwrite /etc/fstab (filesystem table corruption)"),
    # Chmod 000 / -R 000 — locks the user out of anything
    ("chmod_lockout",
     r"\bchmod\b(?:[^&|;]|\s)*\b-?R\b[^&|;]*\s0+\b",
     "chmod -R 000 (recursive lockout)"),
    # rm -rf ~ — wipe home
    ("rm_rf_home",
     r"\brm\b[^|;]*-[a-z]*[rf][a-z]*[rf]\b[^|;]*\s~(?:\s|/|$)",
     "rm -rf ~ (delete home directory)"),
    # rm -rf $HOME — same as above via env var
    ("rm_rf_home_env",
     r"\brm\b[^|;]*-[a-z]*[rf][a-z]*[rf]\b[^|;]*\$HOME\b",
     "rm -rf $HOME"),
    # Pipe to dd of block device
    ("pipe_to_block_dd",
     r"\|\s*dd\b[^|;]*\bof=/dev/(?:sd|nvme|hd|mmcblk|xvd)",
     "piping into dd writing to a block device"),
    # Wipe /boot (irrecoverable)
    ("wipe_boot",
     r"\brm\b[^|;]*-[a-z]*[rf][a-z]*[rf]\b[^|;]*\s/boot(?:\s|/|$)",
     "rm -rf /boot (delete kernel + bootloader)"),
]


# ---------------------------------------------------------------------------
# DANGEROUS patterns — approvable
# ---------------------------------------------------------------------------


_DANGEROUS_PATTERNS: list[tuple[str, str, str]] = [
    # --- Filesystem destruction (approvable when path is specific) ----
    ("rm_recursive",
     r"\brm\b[^|;]*-[a-z]*[rf][a-z]*[rf]\b",
     "recursive delete (rm -rf / -r)"),
    ("rm_force",
     r"\brm\b[^|;]*\s-[a-z]*f[a-z]*\b",
     "force delete (rm -f)"),
    ("rmdir_force",
     r"\brmdir\b[^|;]*-[a-z]*[rf][a-z]*",
     "rmdir with -r or -f flag"),
    ("find_delete",
     r"\bfind\b[^|;]*-delete\b",
     "find ... -delete (recursive delete via find)"),
    ("xargs_rm",
     r"\bxargs\b[^|;]*\brm\b",
     "xargs piping to rm"),
    ("truncate_zero",
     r"\btruncate\b[^|;]*-s\s*0\b",
     "truncate -s 0 (zero out file)"),
    ("shred",
     r"\bshred\b",
     "shred (overwrite file)"),

    # --- Permissions changes ----------------------------------------
    ("chmod_777",
     r"\bchmod\b[^|;]*\s7{2,3}\b",
     "chmod 777 (world-writable)"),
    ("chmod_world_writable",
     r"\bchmod\b[^|;]*\bo\+w\b",
     "chmod o+w (world-writable)"),
    ("chmod_recursive",
     r"\bchmod\b[^|;]*\s-R\b",
     "chmod -R (recursive permission change)"),
    ("chown_recursive",
     r"\bchown\b[^|;]*\s-R\b",
     "chown -R (recursive owner change)"),

    # --- Database / data loss --------------------------------------
    ("drop_table",
     r"\bDROP\s+TABLE\b",
     "DROP TABLE (SQL)"),
    ("drop_database",
     r"\bDROP\s+DATABASE\b",
     "DROP DATABASE (SQL)"),
    ("delete_no_where",
     r"\bDELETE\s+FROM\b(?:(?!\bWHERE\b)[\s\S])*?;",
     "DELETE FROM without WHERE clause"),
    ("update_no_where",
     r"\bUPDATE\s+\w+\s+SET\b(?:(?!\bWHERE\b)[\s\S])*?;",
     "UPDATE without WHERE clause"),
    ("truncate_table",
     r"\bTRUNCATE\s+TABLE\b",
     "TRUNCATE TABLE (SQL)"),

    # --- Shell injection / arbitrary download+execute ---------------
    ("curl_pipe_shell",
     r"\bcurl\b[^|;]*\|\s*(?:ba)?sh\b",
     "curl | sh (download and execute)"),
    ("wget_pipe_shell",
     r"\bwget\b[^|;]*\|\s*(?:ba)?sh\b",
     "wget | sh (download and execute)"),
    ("bash_process_subst",
     r"\b(?:ba)?sh\b\s+<\(\s*(?:curl|wget)\b",
     "bash <(curl) (download and execute via process substitution)"),
    ("eval_unfamiliar",
     r"\beval\b\s+[\"']?\$\(",
     "eval of unbounded command substitution"),

    # --- Git destructive operations -------------------------------
    ("git_reset_hard",
     r"\bgit\s+reset\b[^|;]*--hard\b",
     "git reset --hard (discard uncommitted work)"),
    ("git_clean_force",
     r"\bgit\s+clean\b[^|;]*-[fdx]+",
     "git clean -fd / -fdx (remove untracked + ignored files)"),
    ("git_push_force",
     r"\bgit\s+push\b[^|;]*(?:--force|-f)\b",
     "git push --force (rewrites remote history)"),
    ("git_checkout_dot",
     r"\bgit\s+checkout\b\s+--?\s*\.",
     "git checkout -- . (discard ALL working-tree changes)"),
    ("git_branch_delete_force",
     r"\bgit\s+branch\b[^|;]*-D\b",
     "git branch -D (force-delete unmerged branch)"),

    # --- Sudo / privilege escalation ----------------------------
    ("sudo_password_stdin",
     r"\bsudo\b\s+-S\b",
     "sudo -S (reads password from stdin — usually scripting)"),
    ("sudo_askpass",
     r"\bsudo\b\s+-A\b",
     "sudo -A (uses askpass helper)"),
    ("sudo_su",
     r"\bsudo\s+su\b",
     "sudo su (escalate to root shell)"),

    # --- Package installation ---------------------------------
    ("pip_install",
     r"\bpip\d?\b[^|;]*\binstall\b",
     "pip install (downloads + executes Python package)"),
    ("npm_install",
     r"\b(?:npm|yarn|pnpm)\b[^|;]*\b(?:install|i|add)\b",
     "npm / yarn / pnpm install (downloads + executes JS package)"),
    ("apt_install",
     r"\bapt(?:-get)?\b[^|;]*\binstall\b",
     "apt install (system package install)"),
    ("brew_install",
     r"\bbrew\b[^|;]*\binstall\b",
     "brew install (system package install)"),

    # --- System config / secrets write ----------------------
    ("write_etc",
     r"(?:>|>>)\s*/etc/(?!fstab\b)\w+",
     "overwrite a file under /etc/"),
    ("write_ssh_keys",
     r"(?:>|>>)\s*~?/\.ssh/(?:authorized_keys|known_hosts|id_)",
     "overwrite an SSH key / known_hosts file"),
    ("write_env",
     r"(?:>|>>)\s*\.?env\b",
     "overwrite .env file"),
    ("write_dotrc",
     r"(?:>|>>)\s*~?/\.(?:bashrc|zshrc|profile|bash_profile)\b",
     "overwrite a shell startup file (~/.bashrc etc.)"),

    # --- Cron / scheduling -----------------------------
    ("crontab_replace",
     r"\bcrontab\b\s+(?:-r|<)",
     "crontab -r / crontab < (replace cron table)"),
    ("at_now",
     r"\bat\b\s+(?:now|\+\d+\s+(?:min|hour))",
     "at command (schedules arbitrary execution)"),

    # --- Network ----------------------------------
    ("iptables_flush",
     r"\biptables\b[^|;]*-F\b",
     "iptables -F (flush firewall rules)"),
    ("nft_flush",
     r"\bnft\b[^|;]*\bflush\b",
     "nft flush (clear nftables ruleset)"),
    ("disable_selinux",
     r"\bsetenforce\s+0\b",
     "setenforce 0 (disable SELinux)"),

    # --- VM / container destructive --------------------
    ("docker_rm_volume",
     r"\bdocker\b[^|;]*\b(?:volume\s+rm|rm\s+-v)\b",
     "docker volume rm (delete persistent volume)"),
    ("docker_system_prune",
     r"\bdocker\b[^|;]*\bsystem\s+prune\b",
     "docker system prune (delete unused containers/images/volumes)"),
    ("kubectl_delete",
     r"\bkubectl\b[^|;]*\bdelete\b",
     "kubectl delete (remove Kubernetes resources)"),

    # --- Cloud destructive -------------------------
    ("aws_terminate",
     r"\baws\b[^|;]*\bterminate-instances\b",
     "aws terminate-instances (terminate EC2 instance)"),
    ("aws_delete_bucket",
     r"\baws\b[^|;]*\bdelete-bucket\b",
     "aws delete-bucket (delete S3 bucket)"),
    ("gcloud_delete",
     r"\bgcloud\b[^|;]*\bdelete\b",
     "gcloud delete (delete Google Cloud resource)"),

    # --- Symbol-level binary / weird ------------------
    ("rm_dotdot",
     r"\brm\b[^|;]*\.\.(?:/\.\.)+",
     "rm with ../../ path traversal"),
    ("write_dev_null_redirect_swap",
     r"(?:>|>>)\s*/dev/(?:sd|nvme|hd)",
     "redirect output to raw block device"),
]


# ---------------------------------------------------------------------------
# Compile the patterns once
# ---------------------------------------------------------------------------


def _compile(patterns: list[tuple[str, str, str]]) -> list[tuple[str, re.Pattern, str]]:
    out = []
    for key, regex, desc in patterns:
        try:
            out.append((key, re.compile(regex, re.IGNORECASE | re.DOTALL), desc))
        except re.error as exc:
            # Should never happen with the static set above, but if a new
            # pattern is added with a bug, fail loud at import time.
            raise RuntimeError(
                f"approval: pattern {key!r} failed to compile: {exc}"
            ) from exc
    return out


_HARDLINE = _compile(_HARDLINE_PATTERNS)
_DANGEROUS = _compile(_DANGEROUS_PATTERNS)


# ---------------------------------------------------------------------------
# Command normalisation
# ---------------------------------------------------------------------------


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _normalise_command(command: str) -> str:
    """Strip surface variation before pattern matching.

    - Strips ANSI escape sequences (used in copy-pasted terminal logs)
    - Replaces NUL bytes (\\x00) which would otherwise terminate regex matching
      mid-string on some engines
    - Unicode NFKC normalisation: collapses lookalikes (full-width ASCII,
      combining marks) into their canonical form so an attacker can't bypass
      the pattern with `rm -rf / ` written as `ｒｍ -ｒｆ /`
    - Collapses runs of whitespace into a single space so flexible spacing
      doesn't matter
    """
    if not command:
        return ""
    out = _ANSI_RE.sub("", command)
    out = out.replace("\x00", "")
    out = unicodedata.normalize("NFKC", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def detect_hardline_command(command: str) -> PatternMatch | None:
    """Return the first HARDLINE match (or None when safe).

    HARDLINE matches mean unconditional block — the caller MUST NOT prompt
    the user, because hardline commands should never run from an agent
    context. Used by `check_all_command_guards`.
    """
    norm = _normalise_command(command)
    if not norm:
        return None
    for key, pat, desc in _HARDLINE:
        if pat.search(norm):
            return PatternMatch(key=key, description=desc)
    return None


def detect_dangerous_command(command: str) -> PatternMatch | None:
    """Return the first DANGEROUS match (or None when safe).

    Hardline matches also count as dangerous — the caller should always
    check `detect_hardline_command` first; if that's None and this is not
    None, prompt for approval.
    """
    norm = _normalise_command(command)
    if not norm:
        return None
    for key, pat, desc in _DANGEROUS:
        if pat.search(norm):
            return PatternMatch(key=key, description=desc)
    return None


# ---------------------------------------------------------------------------
# Per-session approval state (ContextVar)
# ---------------------------------------------------------------------------


# Keys are pattern_keys (e.g. "rm_recursive"). When a key is in the set, the
# pattern was approved at session scope and won't prompt again. ContextVar so
# concurrent sessions (gateway threads) don't share each other's approvals.
_session_approved: contextvars.ContextVar[set[str]] = contextvars.ContextVar(
    "bauer_approval_session", default=frozenset(),
)


def session_approved_keys() -> set[str]:
    """Snapshot of approved pattern keys in the current session context."""
    return set(_session_approved.get() or set())


def approve_session(pattern_key: str) -> None:
    """Add a pattern key to the current session's allowlist."""
    if not pattern_key:
        return
    current = set(_session_approved.get() or set())
    current.add(pattern_key)
    _session_approved.set(frozenset(current))


def revoke_session(pattern_key: str | None = None) -> None:
    """Drop one or all session approvals. Useful between turns."""
    if pattern_key is None:
        _session_approved.set(frozenset())
        return
    current = set(_session_approved.get() or set())
    current.discard(pattern_key)
    _session_approved.set(frozenset(current))


def is_session_approved(pattern_key: str) -> bool:
    return bool(pattern_key) and pattern_key in (_session_approved.get() or set())


# ---------------------------------------------------------------------------
# Permanent allowlist (persisted to ~/.bauer/approvals.yaml)
# ---------------------------------------------------------------------------


_PERM_CACHE: set[str] | None = None


def _allowlist_path() -> Path:
    home = os.environ.get("BAUER_HOME")
    base = Path(home).expanduser().resolve() if home else Path.home() / ".bauer"
    return base / "approvals.yaml"


def _all_pattern_keys() -> set[str]:
    """Retorna todos os keys dos padrões perigosos registrados."""
    return {key for key, *_ in _DANGEROUS_PATTERNS}


def load_permanent_allowlist(*, refresh: bool = False) -> set[str]:
    """Read the permanent allowlist from disk. Cached after the first call.

    Se o arquivo não existir, cria-o com TODOS os padrões aprovados (comportamento
    padrão para uso autônomo — o usuário pode remover entradas para restringir).
    Pass `refresh=True` to force a re-read.
    """
    global _PERM_CACHE
    if _PERM_CACHE is not None and not refresh:
        return set(_PERM_CACHE)
    path = _allowlist_path()
    if not path.exists():
        # Primeira execução: cria allowlist com tudo aprovado
        all_keys = _all_pattern_keys()
        try:
            save_permanent_allowlist(all_keys)
        except Exception as exc:
            logger.info("approval: couldn't create default allowlist: %s", exc)
        _PERM_CACHE = all_keys
        return set(all_keys)
    keys: set[str] = set()
    try:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw = data.get("approved", [])
        if isinstance(raw, list):
            keys = {str(k).strip() for k in raw if str(k).strip()}
    except Exception as exc:
        logger.info("approval: couldn't parse %s: %s", path, exc)
    _PERM_CACHE = keys
    return set(keys)


def save_permanent_allowlist(keys: set[str]) -> None:
    """Persist the allowlist atomically. Creates parent dirs as needed."""
    global _PERM_CACHE
    path = _allowlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        "# bauer permanent command-approval allowlist\n"
        "# Generated automatically; safe to edit, but invalid YAML "
        "is silently ignored.\n"
        f"# Last write: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "approved:\n"
    )
    for k in sorted(keys):
        payload += f"  - {k}\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
    _PERM_CACHE = set(keys)


def approve_permanent(pattern_key: str) -> None:
    """Add a key to the permanent allowlist (writes to disk)."""
    if not pattern_key:
        return
    keys = load_permanent_allowlist(refresh=True)
    keys.add(pattern_key)
    save_permanent_allowlist(keys)


def is_permanent_approved(pattern_key: str) -> bool:
    return bool(pattern_key) and pattern_key in load_permanent_allowlist()


# ---------------------------------------------------------------------------
# Combined guard pipeline
# ---------------------------------------------------------------------------


ApprovalCallback = Callable[[str, str], str]
"""Callback signature: (command, description) -> decision str.

Decision must be one of: 'once', 'session', 'always', 'deny'.
'once' approves this specific call without persisting; 'session' adds to the
session allowlist; 'always' writes to the permanent allowlist; 'deny' refuses.
"""


_VALID_DECISIONS: frozenset[str] = frozenset({"once", "session", "always", "deny"})


def check_all_command_guards(
    command: str,
    *,
    approval_callback: ApprovalCallback | None = None,
    yolo: bool = False,
) -> ApprovalDecision:
    """Run a command through hardline → permanent → session → prompt pipeline.

    Args:
        command: The full shell command line about to be executed.
        approval_callback: Optional interactive prompt. If None and the command
            hits a dangerous pattern, the call is denied (safer default for
            non-interactive contexts like cron and CI).
        yolo: When True, ALL dangerous matches auto-approve. Hardline matches
            still block — yolo never overrides those.

    Returns:
        `ApprovalDecision`. `.action` is "approved" or "denied". The caller
        only runs the command when `.action == "approved"`.
    """
    if not command or not command.strip():
        return ApprovalDecision(action="approved", scope="empty")

    # 1. Hardline — incontestable.
    hard = detect_hardline_command(command)
    if hard is not None:
        return ApprovalDecision(
            action="denied",
            reason=f"hardline: {hard.description}",
            pattern_key=hard.key,
            scope="hardline",
        )

    # 2. Dangerous detection.
    danger = detect_dangerous_command(command)
    if danger is None:
        return ApprovalDecision(action="approved", scope="safe")

    # 3. Yolo override.
    if yolo:
        return ApprovalDecision(
            action="approved",
            reason=f"yolo: {danger.description}",
            pattern_key=danger.key,
            scope="yolo",
        )

    # 4. Pre-existing allowlists (permanent first since it survives restarts).
    if is_permanent_approved(danger.key):
        return ApprovalDecision(
            action="approved",
            reason=f"permanent: {danger.description}",
            pattern_key=danger.key,
            scope="preauth",
        )
    if is_session_approved(danger.key):
        return ApprovalDecision(
            action="approved",
            reason=f"session: {danger.description}",
            pattern_key=danger.key,
            scope="preauth",
        )

    # 5. Interactive prompt — only if we have one.
    if approval_callback is None:
        return ApprovalDecision(
            action="denied",
            reason=f"dangerous (no approver attached): {danger.description}",
            pattern_key=danger.key,
            scope="no-prompt",
        )

    try:
        raw = approval_callback(command, danger.description)
    except Exception as exc:
        logger.warning("approval: callback raised: %s", exc)
        return ApprovalDecision(
            action="denied",
            reason=f"callback failure: {exc}",
            pattern_key=danger.key,
            scope="callback-error",
        )

    decision = (raw or "deny").strip().lower()
    if decision not in _VALID_DECISIONS:
        decision = "deny"

    if decision == "deny":
        return ApprovalDecision(
            action="denied",
            reason=f"user-denied: {danger.description}",
            pattern_key=danger.key,
            scope="deny",
        )
    if decision == "session":
        approve_session(danger.key)
    elif decision == "always":
        approve_permanent(danger.key)

    return ApprovalDecision(
        action="approved",
        reason=f"user-{decision}: {danger.description}",
        pattern_key=danger.key,
        scope=decision,
    )
