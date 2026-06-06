"""Secret redaction and worker environment policy."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Mapping


DEFAULT_WORKER_ENV_ALLOWLIST = {
    "PATH",
    "PYTHONPATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "BAUER_KANBAN_TASK",
    "BAUER_KANBAN_PUBLIC_TASK",
    "BAUER_KANBAN_CLAIM_ID",
    "BAUER_KANBAN_RUN_ID",
    "BAUER_KANBAN_WORKSPACE",
    "BAUER_TOOL_CONTEXT",
}

DEFAULT_SECRET_NAME_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"api[_-]?key",
        r"access[_-]?token",
        r"refresh[_-]?token",
        r"secret",
        r"password",
        r"passwd",
        r"bearer",
        r"private[_-]?key",
    )
]

DEFAULT_SECRET_VALUE_PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"sk-[A-Za-z0-9_\-]{16,}",
        r"sk-proj-[A-Za-z0-9_\-]{16,}",
        r"xox[baprs]-[A-Za-z0-9\-]{16,}",
        r"gh[pousr]_[A-Za-z0-9_]{20,}",
        r"AIza[0-9A-Za-z_\-]{20,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    )
]


@dataclass
class SecretPolicy:
    """Central redaction policy used by workers, gateway and logs."""

    worker_env_allowlist: set[str] = field(default_factory=lambda: set(DEFAULT_WORKER_ENV_ALLOWLIST))

    def is_secret_name(self, name: str) -> bool:
        return any(pattern.search(name or "") for pattern in DEFAULT_SECRET_NAME_PATTERNS)

    def sanitize_text(self, value: str) -> str:
        text = value or ""
        for pattern in DEFAULT_SECRET_VALUE_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text

    def sanitize_mapping(self, mapping: Mapping[str, object]) -> dict[str, str]:
        sanitized: dict[str, str] = {}
        for key, value in mapping.items():
            key_str = str(key)
            value_str = "" if value is None else str(value)
            sanitized[key_str] = "[REDACTED]" if self.is_secret_name(key_str) else self.sanitize_text(value_str)
        return sanitized

    def safe_worker_env(
        self,
        base: Mapping[str, str] | None = None,
        *,
        extra: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        source = dict(base or os.environ)
        env = {
            key: value
            for key, value in source.items()
            if key.upper() in {allowed.upper() for allowed in self.worker_env_allowlist}
            and not self.is_secret_name(key)
        }
        for key, value in (extra or {}).items():
            if not self.is_secret_name(key):
                env[str(key)] = str(value)
        return env


def sanitize_text(value: str) -> str:
    return SecretPolicy().sanitize_text(value)


def sanitize_mapping(mapping: Mapping[str, object]) -> dict[str, str]:
    return SecretPolicy().sanitize_mapping(mapping)


def safe_worker_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    return SecretPolicy().safe_worker_env(extra=extra)
