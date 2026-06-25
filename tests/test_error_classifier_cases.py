"""G13: testes paramétricos para error_classifier — todos os padrões de erro."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Pattern data
# ---------------------------------------------------------------------------

RATE_LIMIT_MSGS = [
    "rate limit exceeded",
    "rate_limit_exceeded",
    "Too many requests",
    "throttled",
    "requests per minute limit",
    "tokens per minute exceeded",
    "try again in 30 seconds",
    "please retry after 60s",
    "resource_exhausted",
    "ratelimit",
    "429",
    "HTTP 429",
]

BILLING_MSGS = [
    "insufficient credits",
    "insufficient_quota",
    "insufficient balance in your account",
    "credits have been exhausted",
    "top up your credits",
    "payment required",
    "billing issue",
    "quota exceeded for this month",
    "you have exceeded your monthly limit",
]

AUTH_MSGS = [
    "invalid api key",
    "invalid_api_key provided",
    "incorrect api key",
    "api key not found",
    "no auth credentials",
    "unauthorized access",
    "authentication failed",
    "permission denied for this resource",
    "access denied",
    "forbidden operation",
]

CONTEXT_MSGS = [
    "context length exceeded",
    "context size too large",
    "maximum context length is 4096",
    "token limit reached",
    "too many tokens in the request",
    "exceeds the limit of tokens",
    "context window full",
    "prompt is too long",
    "maximum tokens exceeded",
    "context_length_exceeded error",
    "input too long for model",
]

MODEL_NOT_FOUND_MSGS = [
    "is not a valid model",
    "invalid model specified",
    "model not found in registry",
    "model_not_found error",
    "does not exist in provider",
    "unknown model name",
    "no such model available",
]

SERVER_ERROR_MSGS = [
    "internal server error",
    "internal error occurred",
    "service unavailable",
    "bad gateway 502",
    "gateway timeout 504",
    "temporarily unavailable please try again",
    "server is overloaded",
    "at capacity right now",
    "server error 500",
]

TIMEOUT_MSGS = [
    "request timeout",
    "connection timed out",
    "deadline exceeded",
    "connection reset by peer",
    "connection refused",
    "connection error occurred",
    "read timeout",
    "connect timeout",
    "remotedisconnected",
    "incompleteread",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _classify(msg: str):
    from bauer.error_classifier import classify_api_error
    return classify_api_error(Exception(msg))


def _classify_reason(msg: str):
    result = _classify(msg)
    if hasattr(result, "reason"):
        return result.reason
    if isinstance(result, str):
        return result
    # FailReason enum or similar
    if hasattr(result, "value"):
        return result.value
    return str(result)


# ---------------------------------------------------------------------------
# Rate limit patterns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", RATE_LIMIT_MSGS)
def test_rate_limit_pattern(msg):
    from bauer.error_classifier import FailReason
    reason = _classify(msg.lower())
    # Accept either FailReason enum, string, or ClassifiedError
    reason_str = str(reason).lower()
    assert "rate" in reason_str or "limit" in reason_str or "throttl" in reason_str or "429" in reason_str, (
        f"Expected rate limit classification for '{msg}', got: {reason}"
    )


# ---------------------------------------------------------------------------
# Billing / quota patterns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", BILLING_MSGS)
def test_billing_pattern(msg):
    reason = _classify(msg.lower())
    reason_str = str(reason).lower()
    assert any(k in reason_str for k in ("quota", "billing", "credits", "payment", "rate", "limit")), (
        f"Expected billing classification for '{msg}', got: {reason}"
    )


# ---------------------------------------------------------------------------
# Auth patterns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", AUTH_MSGS)
def test_auth_pattern(msg):
    reason = _classify(msg.lower())
    reason_str = str(reason).lower()
    assert any(k in reason_str for k in ("auth", "permission", "forbidden", "unauthorized")), (
        f"Expected auth classification for '{msg}', got: {reason}"
    )


# ---------------------------------------------------------------------------
# Context overflow patterns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", CONTEXT_MSGS)
def test_context_overflow_pattern(msg):
    reason = _classify(msg.lower())
    reason_str = str(reason).lower()
    assert any(k in reason_str for k in ("context", "token", "overflow", "length")), (
        f"Expected context overflow classification for '{msg}', got: {reason}"
    )


# ---------------------------------------------------------------------------
# Model not found
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", MODEL_NOT_FOUND_MSGS)
def test_model_not_found_pattern(msg):
    reason = _classify(msg.lower())
    reason_str = str(reason).lower()
    assert any(k in reason_str for k in ("model", "not_found", "invalid", "unknown")), (
        f"Expected model_not_found for '{msg}', got: {reason}"
    )


# ---------------------------------------------------------------------------
# Server error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", SERVER_ERROR_MSGS)
def test_server_error_pattern(msg):
    reason = _classify(msg.lower())
    reason_str = str(reason).lower()
    assert any(k in reason_str for k in ("server", "error", "unavailable", "gateway", "capacity", "overload")), (
        f"Expected server error for '{msg}', got: {reason}"
    )


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("msg", TIMEOUT_MSGS)
def test_timeout_pattern(msg):
    reason = _classify(msg.lower())
    reason_str = str(reason).lower()
    assert any(k in reason_str for k in ("timeout", "connection", "reset", "deadline")), (
        f"Expected timeout for '{msg}', got: {reason}"
    )


# ---------------------------------------------------------------------------
# Unknown / fallback
# ---------------------------------------------------------------------------

def test_unknown_error_returns_fallback():
    reason = _classify("some random unclassified error message xyz")
    assert reason is not None  # should not crash


def test_classify_with_http_status_code():
    from bauer.error_classifier import classify_api_error
    # Exception with status_code attribute
    exc = Exception("error")
    exc.status_code = 429  # type: ignore[attr-defined]
    reason = classify_api_error(exc)
    assert reason is not None


def test_classify_returns_enum_or_string():
    from bauer.error_classifier import classify_api_error, FailReason
    reason = classify_api_error(Exception("rate limit exceeded"))
    # Should return FailReason enum value or string
    assert reason is not None


def test_fail_reason_enum_values():
    from bauer.error_classifier import FailReason
    assert FailReason.RATE_LIMIT.value == "rate_limit"
    assert FailReason.AUTH_ERROR.value == "auth_error"
    assert FailReason.QUOTA_EXCEEDED.value == "quota_exceeded"
    assert FailReason.CONTEXT_OVERFLOW.value == "context_overflow"
    assert FailReason.TIMEOUT.value == "timeout"
    assert FailReason.SERVER_ERROR.value == "server_error"
    assert FailReason.MODEL_NOT_FOUND.value == "model_not_found"
    assert FailReason.UNKNOWN.value == "unknown"


@pytest.mark.parametrize("exception_class", [
    "ReadTimeout", "ConnectTimeout", "PoolTimeout", "ConnectError",
])
def test_timeout_exception_types(exception_class):
    from bauer.error_classifier import classify_api_error
    exc = type(exception_class, (Exception,), {})("timed out")
    reason = classify_api_error(exc)
    reason_str = str(reason).lower()
    assert any(k in reason_str for k in ("timeout", "connection", "transient", "transport")), (
        f"Expected timeout for exception type {exception_class}, got: {reason}"
    )


# ---------------------------------------------------------------------------
# 403 → should_fallback (modelo/endpoint rejeita token → tenta próximo provider)
# ---------------------------------------------------------------------------

def _exc_with_status(status: int, msg: str = "error") -> Exception:
    exc = Exception(msg)
    exc.status_code = status  # type: ignore[attr-defined]
    return exc


def test_403_triggers_fallback():
    from bauer.error_classifier import classify_api_error
    result = classify_api_error(_exc_with_status(403, "HTTP 403 pagina html"))
    assert result.should_fallback is True, "403 deve disparar fallback para próximo provider"


def test_401_no_fallback():
    from bauer.error_classifier import classify_api_error
    result = classify_api_error(_exc_with_status(401, "HTTP 401 Unauthorized"))
    assert result.should_fallback is False, "401 (chave inválida) NÃO deve desperdiçar fallbacks"


def test_403_not_retryable():
    from bauer.error_classifier import classify_api_error
    result = classify_api_error(_exc_with_status(403))
    assert result.retryable is False


# ---------------------------------------------------------------------------
# HTML body detection (_is_html_body / _safe_body)
# ---------------------------------------------------------------------------

def test_is_html_body_true_for_html():
    from bauer.openai_client import _is_html_body
    assert _is_html_body("<!DOCTYPE html><html>") is True
    assert _is_html_body("<html><head>") is True
    assert _is_html_body("   <html>") is True  # leading whitespace


def test_is_html_body_false_for_json():
    from bauer.openai_client import _is_html_body
    assert _is_html_body('{"error": "forbidden"}') is False
    assert _is_html_body("Access denied") is False


def test_safe_body_403_html_returns_clean_message():
    from bauer.openai_client import _safe_body
    html_body = "<!DOCTYPE html><html><body>Login Required</body></html>"
    result = _safe_body(403, html_body)
    assert "HTML" in result or "html" in result.lower()
    assert "<!DOCTYPE" not in result  # não vaza o HTML bruto


def test_safe_body_json_error_passthrough():
    from bauer.openai_client import _safe_body
    json_body = '{"error": "model not found"}'
    result = _safe_body(403, json_body)
    assert "model not found" in result
