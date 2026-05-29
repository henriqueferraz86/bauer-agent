"""Testes para _RateLimiter do servidor HTTP."""

from __future__ import annotations

import time
from unittest.mock import patch

from bauer.server import _RateLimiter


def test_allows_requests_within_limit():
    limiter = _RateLimiter(max_requests=5, window_s=60.0)
    for _ in range(5):
        assert limiter.is_allowed("192.168.1.1") is True


def test_blocks_after_limit_exceeded():
    limiter = _RateLimiter(max_requests=3, window_s=60.0)
    for _ in range(3):
        limiter.is_allowed("10.0.0.1")
    assert limiter.is_allowed("10.0.0.1") is False


def test_different_ips_are_independent():
    limiter = _RateLimiter(max_requests=2, window_s=60.0)
    limiter.is_allowed("1.1.1.1")
    limiter.is_allowed("1.1.1.1")
    assert limiter.is_allowed("1.1.1.1") is False
    # IP diferente ainda tem budget
    assert limiter.is_allowed("2.2.2.2") is True


def test_disabled_when_max_requests_zero():
    limiter = _RateLimiter(max_requests=0, window_s=60.0)
    for _ in range(1000):
        assert limiter.is_allowed("any-ip") is True


def test_window_resets_after_expiry():
    """Requisições antigas saem da janela e o IP volta a ser permitido."""
    limiter = _RateLimiter(max_requests=2, window_s=0.1)  # janela de 100ms
    limiter.is_allowed("ip-a")
    limiter.is_allowed("ip-a")
    assert limiter.is_allowed("ip-a") is False

    time.sleep(0.15)  # espera janela expirar
    assert limiter.is_allowed("ip-a") is True


def test_retry_after_positive_when_blocked():
    limiter = _RateLimiter(max_requests=1, window_s=60.0)
    limiter.is_allowed("blocked-ip")
    assert limiter.is_allowed("blocked-ip") is False
    retry = limiter.retry_after("blocked-ip")
    assert 0 < retry <= 60.0


def test_retry_after_zero_for_unknown_ip():
    limiter = _RateLimiter(max_requests=5, window_s=60.0)
    assert limiter.retry_after("new-ip") == 0.0


def test_sliding_window_partial_expiry():
    """Apenas requisições fora da janela são removidas — não todas."""
    limiter = _RateLimiter(max_requests=3, window_s=0.2)

    # 2 requisições agora
    limiter.is_allowed("ip-x")
    limiter.is_allowed("ip-x")

    time.sleep(0.15)  # essas 2 ainda estão na janela

    # 1 requisição mais recente — total 3 → no limite
    assert limiter.is_allowed("ip-x") is True  # 3a → OK (no limite)
    assert limiter.is_allowed("ip-x") is False  # 4a → bloqueado

    time.sleep(0.1)  # as 2 primeiras expiram (0.15 + 0.1 = 0.25 > 0.2)
    # Agora só a 3a ainda está na janela → budget de 2 disponível
    assert limiter.is_allowed("ip-x") is True
    assert limiter.is_allowed("ip-x") is True
    assert limiter.is_allowed("ip-x") is False
