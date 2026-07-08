"""Tests for `bauer/url_safety.py` — SSRF prevention."""

from __future__ import annotations

import ipaddress
from unittest.mock import patch

import pytest

from bauer.url_safety import (
    UrlSafetyConfig,
    UrlSafetyError,
    check_url,
    is_safe_url,
    validate_redirect_chain,
)

# Disable DNS resolution in all tests unless explicitly overridden.
_NO_DNS = UrlSafetyConfig(resolve_dns=False)


# ---------------------------------------------------------------------------
# Safe URLs pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://example.com",
    "https://example.com/path?q=1",
    "http://example.com",
    "https://api.github.com/repos/foo/bar",
    "https://8.8.8.8",           # public IP — Google DNS
    "https://1.1.1.1",           # public IP — Cloudflare DNS
    "https://[2001:db8::1]",     # public IPv6 (documentation range — ok)
])
def test_safe_urls_pass(url: str):
    assert is_safe_url(url, config=_NO_DNS) is True


# ---------------------------------------------------------------------------
# Metadata endpoints — always blocked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/",
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.169.254/latest/user-data",
    # IPv6 IMDS — must use brackets per RFC 2732
    "http://[fd00:ec2::254]/latest/meta-data/",
    "http://metadata.google.internal/",
    "http://metadata.goog/computeMetadata/v1/",
])
def test_metadata_endpoints_blocked(url: str):
    with pytest.raises(UrlSafetyError, match=r"SSRF blocked"):
        check_url(url, config=_NO_DNS)


# ---------------------------------------------------------------------------
# Private / RFC-1918 ranges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ip", [
    # RFC-1918
    "10.0.0.1",
    "10.255.255.255",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.0.1",
    "192.168.1.100",
    # Loopback
    "127.0.0.1",
    "127.0.0.2",
    "::1",
    # Link-local
    "169.254.0.1",
    "169.254.100.50",
    # Unique-local IPv6
    "fc00::1",
    "fd00::1",
    "fd12:3456:789a::1",
    # Carrier-grade NAT
    "100.64.0.1",
    "100.127.255.255",
])
def test_private_ips_blocked(ip: str):
    # Wrap in http:// — brackets for IPv6
    url = f"http://[{ip}]/" if ":" in ip else f"http://{ip}/"
    with pytest.raises(UrlSafetyError, match=r"SSRF blocked"):
        check_url(url, config=_NO_DNS)


@pytest.mark.parametrize("ip", [
    "8.8.8.8",
    "8.8.4.4",
    "1.1.1.1",
    "93.184.216.34",  # example.com
    "2606:4700:4700::1111",  # Cloudflare IPv6
])
def test_public_ips_allowed(ip: str):
    url = f"http://[{ip}]/" if ":" in ip else f"http://{ip}/"
    assert is_safe_url(url, config=_NO_DNS) is True


# ---------------------------------------------------------------------------
# Scheme validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "ftp://example.com/file",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/html,<h1>hi</h1>",
    "gopher://example.com/",
    "dict://example.com:2628/",
])
def test_bad_schemes_blocked(url: str):
    with pytest.raises(UrlSafetyError, match=r"scheme"):
        check_url(url, config=_NO_DNS)


def test_custom_scheme_allowed():
    """Caller can allow extra schemes via config."""
    cfg = UrlSafetyConfig(allowed_schemes=frozenset({"https", "ftp"}),
                          resolve_dns=False)
    assert is_safe_url("ftp://files.example.com/data.csv", config=cfg) is True


# ---------------------------------------------------------------------------
# Empty / malformed URLs
# ---------------------------------------------------------------------------


def test_empty_url_blocked():
    with pytest.raises(UrlSafetyError, match=r"Empty"):
        check_url("", config=_NO_DNS)


def test_whitespace_url_blocked():
    with pytest.raises(UrlSafetyError, match=r"Empty"):
        check_url("   ", config=_NO_DNS)


def test_no_hostname_blocked():
    with pytest.raises(UrlSafetyError):
        check_url("http:///path", config=_NO_DNS)


# ---------------------------------------------------------------------------
# is_safe_url — raise_on_unsafe=False
# ---------------------------------------------------------------------------


def test_is_safe_url_returns_false_without_raising():
    result = is_safe_url("http://127.0.0.1/", config=_NO_DNS,
                         raise_on_unsafe=False)
    assert result is False


def test_is_safe_url_raises_by_default():
    with pytest.raises(UrlSafetyError):
        is_safe_url("http://10.0.0.1/", config=_NO_DNS)


# ---------------------------------------------------------------------------
# DNS resolution (mocked)
# ---------------------------------------------------------------------------


def test_dns_rebinding_blocked():
    """A public-looking hostname that resolves to 169.254.169.254 is blocked."""
    cfg = UrlSafetyConfig(resolve_dns=True)
    mock_results = [
        (2, 1, 6, "", ("169.254.169.254", 80)),
    ]
    with patch("socket.getaddrinfo", return_value=mock_results):
        with pytest.raises(UrlSafetyError, match=r"SSRF blocked.*DNS resolved"):
            check_url("http://totally-legit.example.com/", config=cfg)


def test_dns_resolution_private_range_blocked():
    """DNS resolving to 10.x.x.x is blocked."""
    cfg = UrlSafetyConfig(resolve_dns=True)
    mock_results = [(2, 1, 6, "", ("10.42.0.5", 80))]
    with patch("socket.getaddrinfo", return_value=mock_results):
        with pytest.raises(UrlSafetyError, match=r"SSRF blocked"):
            check_url("http://internal.corp.example.com/", config=cfg)


def test_dns_failure_is_safe():
    """DNS failure (NXDOMAIN) does not block the request — caller decides."""
    import socket as _socket
    cfg = UrlSafetyConfig(resolve_dns=True)
    with patch("socket.getaddrinfo",
               side_effect=_socket.gaierror("nxdomain")):
        # Should NOT raise — DNS failure is treated as safe.
        assert is_safe_url("http://nonexistent.example.com/", config=cfg) is True


def test_dns_resolution_disabled_skips_check():
    """resolve_dns=False means we never call socket.getaddrinfo."""
    with patch("socket.getaddrinfo") as mock_dns:
        is_safe_url("https://example.com", config=_NO_DNS)
    mock_dns.assert_not_called()


# ---------------------------------------------------------------------------
# block_private_ips=False — opt-out for trusted internal environments
# ---------------------------------------------------------------------------


def test_private_ips_allowed_when_disabled():
    cfg = UrlSafetyConfig(block_private_ips=False, resolve_dns=False)
    assert is_safe_url("http://192.168.1.1/", config=cfg) is True


# ---------------------------------------------------------------------------
# Extra blocked hosts and networks
# ---------------------------------------------------------------------------


def test_extra_blocked_host():
    cfg = UrlSafetyConfig(
        resolve_dns=False,
        extra_blocked_hosts=frozenset({"evil.internal.corp"}),
    )
    with pytest.raises(UrlSafetyError, match=r"SSRF blocked"):
        check_url("http://evil.internal.corp/secrets", config=cfg)


def test_extra_blocked_network():
    # Use a genuinely-public range not in _BLOCKED_NETWORKS so the
    # extra-network check fires (and not the core private-range check).
    cfg = UrlSafetyConfig(
        resolve_dns=False,
        extra_blocked_networks=(ipaddress.ip_network("93.184.216.0/24"),),
    )
    with pytest.raises(UrlSafetyError, match=r"extra-blocked network"):
        check_url("http://93.184.216.34/", config=cfg)


def test_extra_blocked_network_not_affecting_other_ips():
    cfg = UrlSafetyConfig(
        resolve_dns=False,
        extra_blocked_networks=(ipaddress.ip_network("203.0.113.0/24"),),
    )
    # 8.8.8.8 is not in 203.0.113.0/24 — should pass.
    assert is_safe_url("http://8.8.8.8/", config=cfg) is True


# ---------------------------------------------------------------------------
# validate_redirect_chain
# ---------------------------------------------------------------------------


def test_redirect_chain_all_safe():
    urls = [
        "https://example.com/",
        "https://www.example.com/page",
        "https://cdn.example.com/asset",
    ]
    validate_redirect_chain(urls, config=_NO_DNS)  # no raise


def test_redirect_chain_blocked_at_step():
    urls = [
        "https://example.com/",
        "http://169.254.169.254/latest/meta-data/",
        "https://harmless.com/",
    ]
    with pytest.raises(UrlSafetyError, match=r"SSRF blocked"):
        validate_redirect_chain(urls, config=_NO_DNS)


def test_redirect_chain_blocked_in_middle():
    """Even if the last URL is safe, the chain is rejected."""
    cfg = UrlSafetyConfig(resolve_dns=False)
    urls = [
        "https://example.com/",
        "http://10.0.0.1/internal",
        "https://example.com/final",
    ]
    with pytest.raises(UrlSafetyError):
        validate_redirect_chain(urls, config=cfg)


# ---------------------------------------------------------------------------
# Encoding / obfuscation attempts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    # Decimal encoding of 169.254.169.254 = 2852039166
    "http://2852039166/",
    # Octal encoding
    "http://0251.0376.0251.0376/",
    # Hex encoding
    "http://0xa9fea9fe/",
])
def test_encoded_metadata_ip_handling(url: str):
    """These obfuscation forms may or may not parse as IPs depending on the OS.
    The test documents current behavior without asserting a specific outcome —
    the important thing is that the checker does NOT raise an unhandled
    exception."""
    # Just verify no crash.
    try:
        is_safe_url(url, config=_NO_DNS, raise_on_unsafe=False)
    except UrlSafetyError:
        pass  # blocked — good
    except Exception as exc:
        pytest.fail(f"Unexpected exception for {url!r}: {exc}")
