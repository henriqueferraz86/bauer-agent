"""Primitivas de observabilidade seguras para o servidor HTTP."""

from __future__ import annotations

import re
import sqlite3
import ipaddress
from pathlib import Path
from uuid import uuid4

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_RUNTIME_DATABASES = ("runtime_state.sqlite3", "budget_ledger.sqlite3")


def request_id(incoming: str | None) -> str:
    """Aceita somente IDs de correlação de formato limitado."""
    candidate = (incoming or "").strip()
    return candidate if _REQUEST_ID.fullmatch(candidate) else uuid4().hex


def apply_security_headers(response) -> None:
    """Headers seguros que não dependem de saber onde TLS foi terminado."""
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")


def runtime_ready(root: Path) -> tuple[bool, str | None]:
    """Checa stores locais sem chamar providers ou revelar dados persistidos."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".readyz-probe"
        probe.touch(exist_ok=True)
        probe.unlink(missing_ok=True)
        for name in _RUNTIME_DATABASES:
            path = root / name
            if not path.exists():
                continue
            with sqlite3.connect(str(path), timeout=2.0) as conn:
                result = conn.execute("PRAGMA quick_check").fetchone()
            if result is None or result[0] != "ok":
                return False, "runtime database integrity check failed"
    except (OSError, sqlite3.DatabaseError):
        return False, "runtime storage unavailable"
    return True, None


def parse_trusted_proxies(entries: list[str] | None) -> tuple[list, bool]:
    """Converte IPs/CIDRs de proxy em redes; `*` é opt-in explícito."""
    networks: list = []
    wildcard = False
    for raw in entries or []:
        item = str(raw).strip()
        if item == "*":
            wildcard = True
        elif item:
            try:
                networks.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                continue
    return networks, wildcard


def peer_is_trusted(peer: str, networks: list, wildcard: bool) -> bool:
    if wildcard:
        return True
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in network for network in networks)


def client_ip_from(peer: str, forwarded: str, networks: list, wildcard: bool) -> str:
    """Honra XFF somente quando a conexão vem de proxy explicitamente confiável."""
    if not peer_is_trusted(peer, networks, wildcard):
        return peer or "unknown"
    for candidate in reversed([item.strip() for item in (forwarded or "").split(",") if item.strip()]):
        if not peer_is_trusted(candidate, networks, False):
            return candidate
    return peer or "unknown"
