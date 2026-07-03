"""URL safety checks — SSRF prevention for web_fetch and http_request tools.

Blocks requests to:
  - Cloud metadata endpoints (169.254.169.254, fd00:ec2::254, etc.)
  - Private / RFC-1918 address ranges (10/8, 172.16/12, 192.168/16)
  - Loopback (127.0.0.0/8, ::1)
  - Link-local (169.254.0.0/16, fe80::/10)
  - Unique-local IPv6 (fc00::/7)
  - Unspecified / broadcast addresses

DNS rebinding mitigation: resolved IPs are re-checked against the same
blocklist so a DNS name that resolves to a private IP is also blocked.

Usage::

    from bauer.url_safety import is_safe_url, UrlSafetyError

    try:
        is_safe_url("http://169.254.169.254/latest/meta-data/")
    except UrlSafetyError as exc:
        print(exc)  # SSRF blocked: metadata endpoint

    if not is_safe_url("https://example.com", raise_on_unsafe=False):
        ...  # blocked

Inspired by Hermes Agent's ``tools/url_safety.py``.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.parse
from dataclasses import dataclass, field
from typing import Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class UrlSafetyError(ValueError):
    """Raised when a URL is blocked by the safety checker."""


# ---------------------------------------------------------------------------
# Blocked networks (IPv4 + IPv6)
# ---------------------------------------------------------------------------

# Cloud metadata services — checked by exact IP/prefix before DNS.
_METADATA_HOSTS: frozenset[str] = frozenset({
    "169.254.169.254",     # AWS / GCP / Azure IMDS
    "fd00:ec2::254",       # AWS IPv6 IMDS
    "metadata.google.internal",
    "metadata.goog",
})

# CIDR ranges that should never be reachable from agent tools.
_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    # Loopback
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    # Link-local (includes 169.254.169.254 IMDS)
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fe80::/10"),
    # Private / RFC-1918
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # Unique-local IPv6 (fc00::/7 covers fd00::/8 too, i.e. AWS IPv6 IMDS)
    ipaddress.ip_network("fc00::/7"),
    # Unspecified
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::/128"),
    # Broadcast
    ipaddress.ip_network("255.255.255.255/32"),
    # Carrier-grade NAT (RFC 6598)
    ipaddress.ip_network("100.64.0.0/10"),
    # Documentation / TEST-NET (RFC 5737)
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    # Benchmarking (RFC 2544)
    ipaddress.ip_network("198.18.0.0/15"),
)


def _is_blocked_ip(ip_str: str) -> tuple[bool, str]:
    """Return (blocked, reason) for a raw IP string."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False, ""

    if str(addr) in _METADATA_HOSTS or ip_str in _METADATA_HOSTS:
        return True, f"metadata endpoint ({ip_str})"

    for net in _BLOCKED_NETWORKS:
        if addr in net:
            return True, f"blocked network ({ip_str} in {net})"

    return False, ""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class UrlSafetyConfig:
    """Tunable knobs for the URL checker.

    ``allowed_schemes`` — only these URL schemes are permitted.
    ``block_private_ips`` — whether to apply the RFC-1918/loopback blocklist.
    ``resolve_dns`` — whether to resolve hostnames and re-check IPs (mitigates
        DNS rebinding). Disable in unit tests for speed.
    ``extra_blocked_hosts`` — additional hostnames to block (exact match).
    ``extra_blocked_networks`` — additional CIDR strings to block.
    """
    allowed_schemes: frozenset[str] = field(
        default_factory=lambda: frozenset({"http", "https"})
    )
    block_private_ips: bool = True
    resolve_dns: bool = True
    extra_blocked_hosts: frozenset[str] = field(default_factory=frozenset)
    extra_blocked_networks: tuple[
        ipaddress.IPv4Network | ipaddress.IPv6Network, ...
    ] = field(default_factory=tuple)


_DEFAULT_CONFIG = UrlSafetyConfig()


# ---------------------------------------------------------------------------
# Core checker
# ---------------------------------------------------------------------------


def check_url(
    url: str,
    *,
    config: UrlSafetyConfig | None = None,
) -> None:
    """Raise :exc:`UrlSafetyError` if *url* is unsafe.

    Checks (in order):
    1. Parse error / empty URL → blocked
    2. Scheme not in allowed_schemes → blocked
    3. Hostname is a known metadata endpoint → blocked
    4. Hostname is a raw IP in a blocked network → blocked
    5. If resolve_dns: resolve hostname, re-check each returned IP → blocked
    6. Extra blocked hosts and networks → blocked
    """
    cfg = config or _DEFAULT_CONFIG

    # --- 1. Parse ---------------------------------------------------------
    url = url.strip()
    if not url:
        raise UrlSafetyError("Empty URL")

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception as exc:
        raise UrlSafetyError(f"Malformed URL: {exc}") from exc

    # --- 2. Scheme --------------------------------------------------------
    scheme = (parsed.scheme or "").lower()
    if scheme not in cfg.allowed_schemes:
        raise UrlSafetyError(
            f"URL scheme {scheme!r} not allowed "
            f"(allowed: {sorted(cfg.allowed_schemes)})"
        )

    hostname = (parsed.hostname or "").lower().strip("[]")
    if not hostname:
        raise UrlSafetyError("URL has no hostname")

    # --- 3. Known metadata hosts ------------------------------------------
    if hostname in _METADATA_HOSTS or hostname in cfg.extra_blocked_hosts:
        raise UrlSafetyError(f"SSRF blocked: metadata/blocked host ({hostname!r})")

    # --- 4. Raw-IP check (before DNS) -------------------------------------
    if cfg.block_private_ips:
        blocked, reason = _is_blocked_ip(hostname)
        if blocked:
            raise UrlSafetyError(f"SSRF blocked: {reason}")

    # --- 5. DNS resolution + re-check -------------------------------------
    if cfg.resolve_dns and cfg.block_private_ips:
        _check_resolved_ips(hostname, cfg)

    # --- 6. Extra networks ------------------------------------------------
    if cfg.extra_blocked_networks:
        _check_extra_networks(hostname, cfg.extra_blocked_networks)


def _check_resolved_ips(
    hostname: str,
    cfg: UrlSafetyConfig,
) -> None:
    """Resolve *hostname* and raise if any returned IP is in a blocked range."""
    try:
        results = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, OSError) as exc:
        # DNS failure is treated as *safe* — we didn't confirm it's private.
        # Callers that want strict fail-closed behaviour should set
        # resolve_dns=False and handle DNS errors themselves.
        logger.debug("DNS resolution failed for %r: %s", hostname, exc)
        return

    for _family, _type, _proto, _canonname, sockaddr in results:
        ip_str = sockaddr[0]
        blocked, reason = _is_blocked_ip(ip_str)
        if blocked:
            raise UrlSafetyError(
                f"SSRF blocked: DNS resolved {hostname!r} → {reason}"
            )

        # Extra networks check for resolved IPs too.
        if cfg.extra_blocked_networks:
            _check_extra_networks(ip_str, cfg.extra_blocked_networks)


def _check_extra_networks(
    ip_or_host: str,
    networks: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> None:
    """Block *ip_or_host* if it falls in any of the extra networks."""
    try:
        addr = ipaddress.ip_address(ip_or_host)
    except ValueError:
        return  # not a raw IP; hostname already covered by extra_blocked_hosts
    for net in networks:
        if addr in net:
            raise UrlSafetyError(
                f"SSRF blocked: {ip_or_host} in extra-blocked network {net}"
            )


# ---------------------------------------------------------------------------
# Convenience wrapper
# ---------------------------------------------------------------------------


def is_safe_url(
    url: str,
    *,
    config: UrlSafetyConfig | None = None,
    raise_on_unsafe: bool = True,
) -> bool:
    """Return True if *url* passes all safety checks.

    If *raise_on_unsafe* is True (default), raises :exc:`UrlSafetyError` on
    failure so callers get a descriptive message. Set to False for boolean
    gate patterns.

    >>> is_safe_url("https://example.com")
    True
    >>> is_safe_url("http://169.254.169.254/", raise_on_unsafe=False)
    False
    """
    try:
        check_url(url, config=config)
        return True
    except UrlSafetyError:
        if raise_on_unsafe:
            raise
        return False


# ---------------------------------------------------------------------------
# Redirect-chain re-validator
# ---------------------------------------------------------------------------


def validate_redirect_chain(
    urls: Sequence[str],
    *,
    config: UrlSafetyConfig | None = None,
) -> None:
    """Validate every URL in a redirect chain.

    HTTP libraries that follow redirects may end up at a private IP even
    if the original URL was safe. Pass the full redirect chain here to
    catch DNS-rebinding via 301/302.

    Raises :exc:`UrlSafetyError` on the first unsafe URL in the chain.
    """
    for url in urls:
        check_url(url, config=config)
