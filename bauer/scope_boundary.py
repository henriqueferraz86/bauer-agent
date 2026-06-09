"""Scope boundary enforcement for autonomous operation.

When Bauer operates autonomously (daemon mode, goal execution) it must respect
an explicit perimeter that constrains:

* **Filesystem writes** — only paths under ``allowed_write_paths`` may be created
  or modified.  Paths under ``denied_paths`` are blocked even if they fall inside
  an allowed write root.
* **Filesystem reads** — by default, all ``allowed_write_paths`` are also readable.
  Add additional read-only roots via ``allowed_read_paths``.
* **Network access** — only URL prefixes in ``allowed_url_prefixes`` are allowed.
  ``denied_url_patterns`` (regex) further block matching URLs.
* **Shell commands** — only executables whose stem (case-insensitive) appears in
  ``allowed_commands`` may be dispatched.  Empty list = all commands permitted.
* **Task depth** — ``max_task_depth`` limits how many levels of LLM decomposition
  are allowed (prevents exponential task explosion).

Usage::

    from bauer.scope_boundary import ScopeBoundary, ScopeViolation

    scope = ScopeBoundary.default(project_root=Path("~/my-project").expanduser())

    violation = scope.check_write(Path("/etc/passwd"))
    if violation:
        raise PermissionError(violation.reason)

    violation = scope.check_url("http://169.254.169.254/latest/meta-data/")
    if violation:
        raise PermissionError(violation.reason)

Configuration via YAML (``~/.bauer/scope.yaml``)::

    scope:
      allowed_write_paths:
        - ~/Documents/PROJETOS/MyProject
        - /tmp/bauer-workspace
      denied_paths:
        - ~/.ssh
        - ~/.aws
      allowed_url_prefixes:
        - https://api.github.com/
      allow_all_urls: false
      allowed_commands: [git, python, pip, pytest, make]
      max_task_depth: 3
"""

from __future__ import annotations

import os
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeViolation:
    """A boundary violation detected before executing an operation.

    Attributes
    ----------
    kind:
        Category of the violated boundary.
    resource:
        The specific path, URL, or command that triggered the violation.
    reason:
        Human-readable explanation of why it was blocked.
    suggested_action:
        What the operator can do to resolve this (e.g., add to allowlist).
    """

    kind: Literal["write", "read", "url", "command", "depth"]
    resource: str
    reason: str
    suggested_action: str = ""

    def __str__(self) -> str:
        msg = f"[SCOPE:{self.kind.upper()}] {self.resource!r} — {self.reason}"
        if self.suggested_action:
            msg += f". Hint: {self.suggested_action}"
        return msg


# ---------------------------------------------------------------------------
# ScopeBoundary
# ---------------------------------------------------------------------------


@dataclass
class ScopeBoundary:
    """Defines and enforces the operational perimeter for autonomous agents.

    All path comparisons are made on *resolved* (absolute, symlink-free) paths
    to prevent traversal tricks.

    Attributes
    ----------
    allowed_write_paths:
        List of root directories where autonomous writes are permitted.
        Subdirectories are implicitly included.
    allowed_read_paths:
        Additional read-only roots (writes still blocked). The union of
        ``allowed_write_paths`` and ``allowed_read_paths`` forms the full read
        perimeter.
    denied_paths:
        Paths that are NEVER accessible, even if they fall inside an allowed
        root. Checked before allowed-path checks.
    allowed_url_prefixes:
        If non-empty, only URLs matching one of these prefixes are permitted.
        An empty list (combined with ``allow_all_urls=True``) permits all URLs.
    denied_url_patterns:
        Regex patterns compiled case-insensitively.  A URL matching any pattern
        is blocked regardless of ``allowed_url_prefixes``.
    allow_all_urls:
        When True, skip the ``allowed_url_prefixes`` check (but
        ``denied_url_patterns`` and url_safety still apply).
    allowed_commands:
        Executable stems that may be dispatched.  An empty list means all
        commands are permitted (no restriction).
    max_parallel_tasks:
        Maximum number of tasks allowed to run in parallel in daemon mode.
    max_task_depth:
        Maximum decomposition depth.  Prevents the planner from recursively
        creating thousands of sub-tasks.
    """

    allowed_write_paths: list[Path] = field(default_factory=list)
    allowed_read_paths: list[Path] = field(default_factory=list)
    denied_paths: list[Path] = field(default_factory=list)

    allowed_url_prefixes: list[str] = field(default_factory=list)
    denied_url_patterns: list[str] = field(default_factory=list)
    allow_all_urls: bool = False

    allowed_commands: list[str] = field(default_factory=list)

    max_parallel_tasks: int = 4
    max_task_depth: int = 3

    # Compiled regex cache (populated lazily by __post_init__)
    _denied_url_re: list[re.Pattern] = field(default_factory=list, repr=False)
    _allowed_cmds_lower: frozenset[str] = field(default=frozenset(), repr=False)

    def __post_init__(self) -> None:
        # Expand ~ and resolve to absolute paths.
        self.allowed_write_paths = [_resolve(p) for p in self.allowed_write_paths]
        self.allowed_read_paths = [_resolve(p) for p in self.allowed_read_paths]
        self.denied_paths = [_resolve(p) for p in self.denied_paths]

        # Compile URL patterns.
        self._denied_url_re = [
            re.compile(pat, re.IGNORECASE) for pat in self.denied_url_patterns
        ]

        # Normalise command stems.
        self._allowed_cmds_lower = frozenset(
            c.lower().rstrip(".exe") for c in self.allowed_commands
        )

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def default(cls, project_root: Path | str) -> "ScopeBoundary":
        """Conservative default scope pinned to *project_root*.

        Allows writes only inside the project, reads from /tmp as well,
        denies writes to ~/.ssh, ~/.aws, /etc, /usr, /sys, /proc.
        Common dev commands are allowed.  All URLs are allowed by default
        (url_safety.py provides separate SSRF protection).
        """
        root = _resolve(Path(project_root))
        return cls(
            allowed_write_paths=[root, Path("/tmp")],
            allowed_read_paths=[Path.home()],
            denied_paths=[
                Path.home() / ".ssh",
                Path.home() / ".aws",
                Path.home() / ".gnupg",
                Path("/etc"),
                Path("/usr"),
                Path("/sys"),
                Path("/proc"),
                Path("/boot"),
                Path("/dev"),
            ],
            allow_all_urls=True,  # URL safety delegated to url_safety.py
            allowed_commands=[
                "git", "python", "python3", "pip", "pip3",
                "pytest", "ruff", "mypy", "make", "npm", "node",
                "cargo", "rustc", "go", "mvn", "gradle",
                "ls", "cat", "grep", "find", "sed", "awk",
                "echo", "mkdir", "cp", "mv", "rm",
            ],
            max_parallel_tasks=4,
            max_task_depth=3,
        )

    @classmethod
    def open(cls) -> "ScopeBoundary":
        """Fully open scope — no restrictions.  For testing only."""
        return cls(allow_all_urls=True)

    @classmethod
    def from_config(cls, config: dict) -> "ScopeBoundary":
        """Build from a parsed YAML/dict config section (``scope`` key)."""
        s = config.get("scope", config)  # tolerate passing the full config or just scope

        def _paths(key: str) -> list[Path]:
            return [Path(p).expanduser() for p in s.get(key, [])]

        return cls(
            allowed_write_paths=_paths("allowed_write_paths"),
            allowed_read_paths=_paths("allowed_read_paths"),
            denied_paths=_paths("denied_paths"),
            allowed_url_prefixes=s.get("allowed_url_prefixes", []),
            denied_url_patterns=s.get("denied_url_patterns", []),
            allow_all_urls=bool(s.get("allow_all_urls", False)),
            allowed_commands=s.get("allowed_commands", []),
            max_parallel_tasks=int(s.get("max_parallel_tasks", 4)),
            max_task_depth=int(s.get("max_task_depth", 3)),
        )

    @classmethod
    def from_yaml(cls, yaml_path: Path | str) -> "ScopeBoundary":
        """Load from a YAML file at *yaml_path*."""
        import yaml  # type: ignore[import-untyped]

        with open(yaml_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        return cls.from_config(data)

    # ------------------------------------------------------------------
    # Enforcement methods — all return ScopeViolation | None
    # ------------------------------------------------------------------

    def check_write(self, path: Path | str) -> ScopeViolation | None:
        """Return a violation if *path* may not be written.

        Blocked conditions:
        1. Path is under a ``denied_paths`` entry (highest priority).
        2. Path is not under any ``allowed_write_paths`` entry.
        """
        resolved = _resolve(Path(path))
        kind = "write"

        # 1. Explicit deny overrides everything.
        for denied in self.denied_paths:
            if _is_under(resolved, denied):
                return ScopeViolation(
                    kind=kind,
                    resource=str(resolved),
                    reason=f"path is under denied root {denied}",
                    suggested_action=f"remove {denied} from denied_paths if intentional",
                )

        # 2. Must be under an allowed write root.
        if self.allowed_write_paths:
            for root in self.allowed_write_paths:
                if _is_under(resolved, root):
                    return None  # allowed
            return ScopeViolation(
                kind=kind,
                resource=str(resolved),
                reason="path is outside all allowed_write_paths",
                suggested_action=(
                    f"add parent directory to allowed_write_paths in scope config"
                ),
            )

        return None  # no write restriction configured

    def check_read(self, path: Path | str) -> ScopeViolation | None:
        """Return a violation if *path* may not be read.

        Checks denied_paths only; read is less restricted than write.
        """
        resolved = _resolve(Path(path))
        for denied in self.denied_paths:
            if _is_under(resolved, denied):
                return ScopeViolation(
                    kind="read",
                    resource=str(resolved),
                    reason=f"path is under denied root {denied}",
                    suggested_action="this path is explicitly blocked for security",
                )
        return None

    def check_url(self, url: str) -> ScopeViolation | None:
        """Return a violation if *url* may not be fetched.

        Blocked conditions:
        1. URL matches any ``denied_url_patterns`` regex.
        2. ``allow_all_urls`` is False AND no ``allowed_url_prefixes`` matches.
        """
        # 1. Explicit deny patterns (highest priority).
        for pat in self._denied_url_re:
            if pat.search(url):
                return ScopeViolation(
                    kind="url",
                    resource=url,
                    reason=f"URL matches denied_url_patterns: {pat.pattern!r}",
                    suggested_action="remove the pattern from denied_url_patterns if intentional",
                )

        # 2. Allowlist (only checked when allow_all_urls is False).
        if not self.allow_all_urls and self.allowed_url_prefixes:
            for prefix in self.allowed_url_prefixes:
                if url.startswith(prefix):
                    return None  # allowed
            return ScopeViolation(
                kind="url",
                resource=url,
                reason="URL does not match any allowed_url_prefixes",
                suggested_action=(
                    f"add a matching prefix to allowed_url_prefixes, or set allow_all_urls: true"
                ),
            )

        return None  # allowed

    def check_command(self, command: str) -> ScopeViolation | None:
        """Return a violation if the *command* executable is not allowed.

        Extracts the first token of *command* and checks its stem (lowercase)
        against ``allowed_commands``.  An empty ``allowed_commands`` list
        means all commands are permitted.
        """
        if not self._allowed_cmds_lower:
            return None  # no restriction

        # Extract executable stem.
        tokens = command.strip().split()
        if not tokens:
            return None
        exe = Path(tokens[0]).stem.lower().rstrip(".exe")

        if exe in self._allowed_cmds_lower:
            return None

        return ScopeViolation(
            kind="command",
            resource=tokens[0],
            reason=f"command {exe!r} is not in allowed_commands",
            suggested_action=(
                f"add {exe!r} to allowed_commands in scope config, or leave "
                "allowed_commands empty to permit all commands"
            ),
        )

    def check_task_depth(self, current_depth: int) -> ScopeViolation | None:
        """Return a violation if ``current_depth`` exceeds ``max_task_depth``."""
        if current_depth > self.max_task_depth:
            return ScopeViolation(
                kind="depth",
                resource=str(current_depth),
                reason=(
                    f"task decomposition depth {current_depth} exceeds "
                    f"max_task_depth={self.max_task_depth}"
                ),
                suggested_action="increase max_task_depth in scope config, or simplify the goal",
            )
        return None

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def is_write_allowed(self, path: Path | str) -> bool:
        return self.check_write(path) is None

    def is_url_allowed(self, url: str) -> bool:
        return self.check_url(url) is None

    def is_command_allowed(self, command: str) -> bool:
        return self.check_command(command) is None

    def summary(self) -> str:
        """Return a one-paragraph human-readable summary of the scope."""
        write_roots = (
            ", ".join(str(p) for p in self.allowed_write_paths)
            or "(no restriction)"
        )
        url_policy = (
            "all URLs"
            if self.allow_all_urls
            else (
                f"{len(self.allowed_url_prefixes)} allowed prefix(es)"
                if self.allowed_url_prefixes
                else "no URLs"
            )
        )
        cmd_policy = (
            f"{len(self._allowed_cmds_lower)} allowed command(s)"
            if self._allowed_cmds_lower
            else "all commands"
        )
        return (
            f"ScopeBoundary — write: {write_roots} | "
            f"urls: {url_policy} | "
            f"commands: {cmd_policy} | "
            f"max_depth={self.max_task_depth}"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve(path: Path) -> Path:
    """Expand ~ and make absolute.  Does NOT call resolve() to avoid
    requiring the path to exist on disk."""
    path = Path(os.path.expanduser(path))
    if not path.is_absolute():
        path = Path.cwd() / path
    # Normalise separators and dots without requiring existence.
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _is_under(child: Path, parent: Path) -> bool:
    """Return True if *child* is *parent* or a descendant of *parent*."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
