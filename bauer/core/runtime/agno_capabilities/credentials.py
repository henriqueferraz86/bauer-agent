"""Credential lookup for capabilities — by *name*, never stored in settings.

Order: process environment, then ``bauer credential`` (``CredentialPool``
rooted at ``$BAUER_HOME``). Status checks only answer "present/missing";
the value is resolved exclusively at call time by the capability factory and
never returned to catalog, API, events or logs.
"""

from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger("bauer.agno_capabilities")

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.\-]{1,80}$")


def _pool():  # pragma: no cover - thin wrapper, exercised via monkeypatch in tests
    from ....credential_pool import CredentialPool
    from ....paths import get_bauer_home

    return CredentialPool(base_dir=get_bauer_home())


def valid_credential_name(name: str) -> bool:
    return bool(_NAME.match(name or ""))


def credential_available(name: str) -> bool:
    if not valid_credential_name(name):
        return False
    if os.environ.get(name):
        return True
    try:
        return name in set(_pool().list_providers())
    except Exception as exc:  # noqa: BLE001 - status is best-effort
        logger.debug("credential status lookup failed: %s", type(exc).__name__)
        return False


def resolve_credential(name: str) -> str | None:
    """Return the secret for ``name`` or ``None``. Callers must not log it."""
    if not valid_credential_name(name):
        return None
    value = os.environ.get(name)
    if value:
        return value
    try:
        return _pool().get(name) or None
    except Exception as exc:  # noqa: BLE001
        logger.debug("credential lookup failed: %s", type(exc).__name__)
        return None
