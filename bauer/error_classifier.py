"""Classificador de erros de providers LLM.

Inspirado no error_classifier.py do Hermes Agent v0.14.0.
Classifica exceções em categorias semânticas para guiar retry e fallback.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enum de razões de falha
# ---------------------------------------------------------------------------

class FailReason(enum.Enum):
    """Por que a chamada ao provider falhou — determina estratégia de recovery."""
    # Auth / autorização
    AUTH_ERROR = "auth_error"
    AUTH_PERMANENT = "auth_permanent"
    # Billing / cota
    QUOTA_EXCEEDED = "quota_exceeded"
    RATE_LIMIT = "rate_limit"
    # Server-side
    SERVER_ERROR = "server_error"
    PROVIDER_DOWN = "provider_down"
    # Transport
    TIMEOUT = "timeout"
    # Context / payload
    CONTEXT_OVERFLOW = "context_overflow"
    # Modelo
    MODEL_NOT_FOUND = "model_not_found"
    # Desconhecido
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Pattern lists (baseados no Hermes error_classifier.py)
# ---------------------------------------------------------------------------

_BILLING_PATTERNS = [
    "insufficient credits",
    "insufficient_quota",
    "insufficient balance",
    "credits have been exhausted",
    "top up your credits",
    "payment required",
    "billing",
    "quota exceeded",
    "you have exceeded",
]

_RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit",
    "too many requests",
    "throttled",
    "requests per minute",
    "tokens per minute",
    "try again in",
    "please retry after",
    "resource_exhausted",
    "ratelimit",
    "429",
]

_CONTEXT_PATTERNS = [
    "context length",
    "context size",
    "maximum context",
    "token limit",
    "too many tokens",
    "exceeds the limit",
    "context window",
    "prompt is too long",
    "maximum tokens",
    "context_length_exceeded",
    "input too long",
]

_AUTH_PATTERNS = [
    "invalid api key",
    "invalid_api_key",
    "incorrect api key",
    "api key not found",
    "no auth",
    "unauthorized",
    "authentication failed",
    "permission denied",
    "access denied",
    "forbidden",
]

_MODEL_NOT_FOUND_PATTERNS = [
    "is not a valid model",
    "invalid model",
    "model not found",
    "model_not_found",
    "does not exist",
    "unknown model",
    "no such model",
]

_SERVER_ERROR_PATTERNS = [
    "internal server error",
    "internal error",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "temporarily unavailable",
    "overloaded",
    "capacity",
    "server error",
]

_TIMEOUT_PATTERNS = [
    "timeout",
    "timed out",
    "deadline exceeded",
    "connection reset",
    "connection refused",
    "connection error",
    "read timeout",
    "connect timeout",
    "remotedisconnected",
    "incompleteread",
]

_TRANSPORT_ERROR_TYPES = frozenset({
    "ReadTimeout", "ConnectTimeout", "PoolTimeout",
    "ConnectError", "RemoteProtocolError",
    "ConnectionError", "ConnectionResetError",
    "APIConnectionError", "APITimeoutError",
    "TimeoutError", "OSError",
})


# ---------------------------------------------------------------------------
# ClassifiedError
# ---------------------------------------------------------------------------

@dataclass
class ClassifiedError:
    """Erro classificado com hints de recovery."""
    reason: FailReason
    status_code: Optional[int] = None
    message: str = ""
    # Recovery hints
    retryable: bool = True
    should_compress: bool = False   # tente comprimir contexto antes de retry
    should_fallback: bool = False   # tente provider alternativo

    @property
    def is_auth(self) -> bool:
        return self.reason in {FailReason.AUTH_ERROR, FailReason.AUTH_PERMANENT}

    @property
    def is_rate_limit(self) -> bool:
        return self.reason == FailReason.RATE_LIMIT

    @property
    def is_context_overflow(self) -> bool:
        return self.reason == FailReason.CONTEXT_OVERFLOW

    def __str__(self) -> str:
        parts = [f"[{self.reason.value}]"]
        if self.status_code:
            parts.append(f"HTTP {self.status_code}")
        if self.message:
            parts.append(self.message[:120])
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Extratores de metadados
# ---------------------------------------------------------------------------

def _extract_status_code(error: Exception) -> Optional[int]:
    """Percorre a cadeia de causa para encontrar status HTTP."""
    current: Any = error
    for _ in range(5):
        code = getattr(current, "status_code", None)
        if isinstance(code, int):
            return code
        code = getattr(current, "status", None)
        if isinstance(code, int) and 100 <= code < 600:
            return code
        cause = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        if cause is None or cause is current:
            break
        current = cause
    return None


def _build_error_msg(error: Exception) -> str:
    """Combina mensagem de erro + body JSON em string lowercase para pattern matching."""
    parts = [str(error).lower()]
    body = getattr(error, "response", None)
    if body is not None:
        try:
            import json as _json
            bd = body.json() if callable(getattr(body, "json", None)) else {}
            msg = (bd.get("error", {}) or {}).get("message", "")
            if msg:
                parts.append(str(msg).lower())
        except Exception:
            try:
                parts.append(body.text.lower()[:500])
            except Exception:
                pass
    return " ".join(parts)


def _matches_any(msg: str, patterns: list[str]) -> bool:
    return any(p in msg for p in patterns)


# ---------------------------------------------------------------------------
# Classificador principal
# ---------------------------------------------------------------------------

def classify_api_error(
    error: Exception,
    *,
    approx_tokens: int = 0,
    context_length: int = 200_000,
) -> ClassifiedError:
    """Classifica uma exceção de provider em ClassifiedError com hints de recovery.

    Pipeline de 7 passos (do mais específico ao mais genérico):
      1. Status HTTP 401/403 → AUTH
      2. Status HTTP 402/429 → BILLING / RATE_LIMIT
      3. Status HTTP 5xx → SERVER_ERROR / PROVIDER_DOWN
      4. Pattern matching de mensagem → contexto, quota, rate limit, auth, modelo
      5. Tipo de exceção → transport/timeout
      6. Context overflow heurístico por tokens
      7. Fallback → UNKNOWN retryable
    """
    status = _extract_status_code(error)
    msg = _build_error_msg(error)
    error_type = type(error).__name__

    def _result(
        reason: FailReason,
        retryable: bool = True,
        compress: bool = False,
        fallback: bool = False,
    ) -> ClassifiedError:
        return ClassifiedError(
            reason=reason,
            status_code=status,
            message=str(error)[:200],
            retryable=retryable,
            should_compress=compress,
            should_fallback=fallback,
        )

    # 1. Auth errors
    if status in (401, 403):
        permanent = status == 403 and not _matches_any(msg, _RATE_LIMIT_PATTERNS)
        return _result(
            FailReason.AUTH_PERMANENT if permanent else FailReason.AUTH_ERROR,
            retryable=False,
        )

    # 2. Payment / rate limit
    if status == 402 or (status is None and _matches_any(msg, _BILLING_PATTERNS)):
        return _result(FailReason.QUOTA_EXCEEDED, retryable=False, fallback=True)

    if status == 429 or _matches_any(msg, _RATE_LIMIT_PATTERNS):
        return _result(FailReason.RATE_LIMIT, retryable=True)

    # 3. Server errors
    if status is not None and status >= 500:
        reason = FailReason.PROVIDER_DOWN if status in (502, 503, 504) else FailReason.SERVER_ERROR
        return _result(reason, retryable=True, fallback=status in (502, 503, 504))

    # 4. Message pattern matching (sem status code)
    if _matches_any(msg, _CONTEXT_PATTERNS):
        return _result(FailReason.CONTEXT_OVERFLOW, retryable=True, compress=True)

    if _matches_any(msg, _AUTH_PATTERNS):
        return _result(FailReason.AUTH_ERROR, retryable=False)

    if _matches_any(msg, _BILLING_PATTERNS):
        return _result(FailReason.QUOTA_EXCEEDED, retryable=False, fallback=True)

    if _matches_any(msg, _SERVER_ERROR_PATTERNS):
        return _result(FailReason.PROVIDER_DOWN, retryable=True, fallback=True)

    if _matches_any(msg, _MODEL_NOT_FOUND_PATTERNS):
        return _result(FailReason.MODEL_NOT_FOUND, retryable=False)

    # 5. Tipo de exceção transport/timeout
    if error_type in _TRANSPORT_ERROR_TYPES or _matches_any(msg, _TIMEOUT_PATTERNS):
        return _result(FailReason.TIMEOUT, retryable=True)

    # 6. Context overflow por token count heurístico
    if approx_tokens > 0 and context_length > 0:
        if approx_tokens > context_length * 0.9:
            return _result(FailReason.CONTEXT_OVERFLOW, retryable=True, compress=True)

    # 7. Fallback — desconhecido mas retryable
    return _result(FailReason.UNKNOWN, retryable=True)
