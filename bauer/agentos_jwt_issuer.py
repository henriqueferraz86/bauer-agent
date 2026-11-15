"""Issue narrowly scoped AgentOS JWTs for already-authenticated Telegram users.

This primitive does not authenticate Telegram updates: the caller must first
verify the channel and allowlist, and must obtain the user ID from that trusted
envelope rather than from message content. It is not connected to a gateway.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections.abc import Iterable

import jwt

_SCOPE = re.compile(r"^(?:agents|teams):[A-Za-z0-9_.-]+:(?:run|read)$")
_MIN_LIFETIME_S = 1
_MAX_LIFETIME_S = 300


class AgentOSJWTIssuer:
    """Sign short-lived per-user HS256 tokens with explicitly granted scopes."""

    def __init__(self, *, key: str, audience: str, issuer: str,
                 allowed_scopes: Iterable[str], lifetime_s: int = 60):
        if not isinstance(key, str) or len(key.encode("utf-8")) < 32:
            raise ValueError("JWT key must contain at least 32 bytes")
        if any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in (audience, issuer)):
            raise ValueError("issuer and audience must be non-empty strings")
        if isinstance(lifetime_s, bool) or not isinstance(lifetime_s, int) or not _MIN_LIFETIME_S <= lifetime_s <= _MAX_LIFETIME_S:
            raise ValueError("JWT lifetime must be between 1 and 300 seconds")
        if isinstance(allowed_scopes, (str, bytes)):
            raise ValueError("allowed_scopes must be a collection of explicit scopes")
        scopes = tuple(allowed_scopes)
        if not scopes or any(not isinstance(scope, str) or not _SCOPE.fullmatch(scope) for scope in scopes):
            raise ValueError("scopes must name explicit agents or teams and read or run permissions")
        self._key = key
        self._audience = audience
        self._issuer = issuer
        self._allowed_scopes = frozenset(scopes)
        self._lifetime_s = lifetime_s

    def issue(self, *, authenticated_user_id: str, scopes: Iterable[str]) -> str:
        """Create a token from a trusted, allowlisted Telegram user's numeric ID."""
        if (not isinstance(authenticated_user_id, str) or not authenticated_user_id.isascii()
                or not authenticated_user_id.isdecimal() or len(authenticated_user_id) > 32
                or int(authenticated_user_id) <= 0 or str(int(authenticated_user_id)) != authenticated_user_id):
            raise ValueError("authenticated Telegram user ID must be a positive integer string")
        if isinstance(scopes, (str, bytes)):
            raise ValueError("scopes must be a collection")
        requested = tuple(scopes)
        if not requested or any(not isinstance(s, str) or s not in self._allowed_scopes for s in requested):
            raise ValueError("requested scope is not explicitly allowed")
        # Domain-separated HMAC yields a stable opaque subject without embedding
        # Telegram's raw user ID in every remote run or audit record.
        digest = hmac.new(self._key.encode("utf-8"),
                          b"bauer-agentos-telegram-subject-v1\0" + authenticated_user_id.encode("ascii"),
                          hashlib.sha256).hexdigest()
        now = int(time.time())
        return jwt.encode({"sub": f"tg-{digest}", "iss": self._issuer, "aud": self._audience,
                           "iat": now, "nbf": now, "exp": now + self._lifetime_s,
                           "scopes": sorted(set(requested))}, self._key, algorithm="HS256")
