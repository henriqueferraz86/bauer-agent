"""Privacy boundaries for public chat output and persisted runtime data.

Tool messages must remain available to the model while a turn is running, but
they are not conversational output.  This module is deliberately dependency
light so it can be used by the HTTP boundary, session store, event bus and
logging setup without introducing import cycles.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"

# Match names rather than values.  ``KEY`` is intentionally included because
# provider/config payloads use both ``key`` and names such as ``privateKey``.
_SENSITIVE_NAME = re.compile(
    r"(?:^|[_\-.])(?:password|pass|passwd|pwd|secret|token|api[_\-.]?key|"
    r"key|database[_\-.]?url|redis[_\-.]?url|connection[_\-.]?string|"
    r"authorization|cookie|private[_\-.]?key)(?:$|[_\-.])",
    re.IGNORECASE,
)
_SENSITIVE_COMPACT = re.compile(
    r"^(?:password|pass|passwd|pwd|secret|token|apikey|key|databaseurl|"
    r"redisurl|connectionstring|authorization|cookie|privatekey)$",
    re.IGNORECASE,
)

_PEM = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)* PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_BEARER = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_AUTH_HEADER = re.compile(
    r"(?im)^(\s*(?:authorization|proxy-authorization|cookie)\s*:\s*)([^\r\n]+)$"
)
_URL_CREDENTIALS = re.compile(
    r"(?i)(\b(?:https?|ftp)://[^\s/@:]+:)([^\s/@]+)(@)"
)
_KNOWN_TOKEN = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{30,}|"
    r"gsk_[A-Za-z0-9]{30,}|hf_[A-Za-z0-9]{25,}|"
    r"AIza[A-Za-z0-9_-]{25,}|"
    r"ey[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b"
)
# .env, shell and JSON-ish key/value forms.  The key itself is retained so a
# diagnostic remains searchable without retaining the secret value.
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>\b(?:password|pass|passwd|pwd|secret|token|api[_-]?key|"
    r"key|database[_-]?url|redis[_-]?url|connection[_-]?string|"
    r"authorization|cookie|private[_-]?key)\b\s*(?:=|:)\s*)"
    r"(?P<quote>[\"']?)(?P<value>[^\r\n,}\]]+)",
)


def is_sensitive_name(name: object) -> bool:
    """Return whether a mapping key denotes a secret-bearing value."""
    normalized = str(name or "").strip().replace(" ", "")
    compact = re.sub(r"[^A-Za-z0-9]", "", normalized)
    return bool(_SENSITIVE_NAME.search(normalized) or _SENSITIVE_COMPACT.fullmatch(compact))


def redact_text(value: object) -> str:
    """Redact credentials and known secret formats from arbitrary text."""
    text = "" if value is None else str(value)
    text = _PEM.sub(REDACTED, text)
    text = _AUTH_HEADER.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}{REDACTED}{m.group(3)}", text)

    def _assignment(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        quote = match.group("quote")
        return f"{prefix}{quote}{REDACTED}{quote}"

    text = _SENSITIVE_ASSIGNMENT.sub(_assignment, text)
    text = _KNOWN_TOKEN.sub(REDACTED, text)
    # Reuse the broader project scanner for provider-specific credentials
    # (AWS, Telegram, Discord, etc.) and normalize its legacy marker.
    try:
        from .secrets_scanner import redact as _scan_redact

        text = _scan_redact(text)
    except Exception:
        # The legacy scanner is an optional defense-in-depth layer.
        text = text
    # Keep compatibility with the older scanner while normalizing its
    # pattern-specific marker to the public contract.
    text = re.sub(r"\[REDACTED:[^\]]+\]", REDACTED, text)
    text = text.replace(REDACTED + "]", REDACTED)
    return text


def redact_data(value: Any) -> Any:
    """Recursively sanitize data before it crosses a persistence boundary."""
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if is_sensitive_name(key) else redact_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_data(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return copy.deepcopy(value)


def is_public_message(message: Mapping[str, Any]) -> bool:
    """Whether a stored/transported message is safe for conversational UI."""
    role = str(message.get("role") or "").lower()
    kind = str(message.get("type") or message.get("kind") or "").lower()
    visibility = str(message.get("visibility") or "").lower()
    if visibility in {"internal", "private", "hidden"}:
        return False
    if role not in {"user", "assistant"}:
        return False
    if kind in {"tool", "tool_result", "system", "developer", "internal", "debug", "trace", "event", "command", "analysis"}:
        return False
    if message.get("tool_calls") or role == "tool":
        return False
    return True


def public_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a defensive, sanitized view suitable for a chat client."""
    return [redact_data(dict(message)) for message in messages if is_public_message(message)]
