"""Sprint 80: conexão derrubada no meio da resposta é falha transitória.

Visto na Beelink em 2026-11-12: um /loop parou com
"OpenAIClientError: Erro HTTP: peer closed connection without sending
complete message body (incomplete chunked read)". O classificador devolvia
UNKNOWN, então nem o retry do provider nem o da rodada do loop agiam.
"""

from __future__ import annotations

import httpx
import pytest

from bauer.error_classifier import FailReason, classify_api_error
from bauer.openai_client import OpenAIClientError
from bauer.provider_retry import is_retryable_provider_error

BEELINK = ("Erro HTTP: peer closed connection without sending complete message "
           "body (incomplete chunked read)")


@pytest.mark.parametrize("message", [
    BEELINK,
    "Erro HTTP: Server disconnected without sending a response.",
    "Erro HTTP: [Errno 32] Broken pipe",
    "Erro HTTP: Connection aborted.",
    "Erro HTTP: unexpected EOF while reading",
])
def test_transport_drops_are_transient(message):
    error = OpenAIClientError(message)
    assert classify_api_error(error).reason is FailReason.TIMEOUT
    assert is_retryable_provider_error(error)


def test_wrapped_transport_cause_is_transient_even_with_a_vague_message():
    try:
        try:
            raise httpx.RemoteProtocolError("boom")
        except httpx.RemoteProtocolError as cause:
            raise OpenAIClientError("Erro HTTP: algo deu errado") from cause
    except OpenAIClientError as error:
        assert classify_api_error(error).reason is FailReason.TIMEOUT
        assert is_retryable_provider_error(error)


def test_auth_errors_stay_permanent():
    error = OpenAIClientError("[Provedor HTTP 401] Unauthorized: invalid api key", status_code=401)
    assert not is_retryable_provider_error(error)


def test_loop_round_retries_the_beelink_error(monkeypatch):
    import bauer.serve_loop as serve_loop
    from bauer.autonomous_budget import AutonomousBudget

    monkeypatch.setattr(serve_loop, "_sleep", lambda _s: None)
    monkeypatch.setattr(serve_loop, "ROUND_RETRY_DELAYS", (0.0, 0.0, 0.0))

    class _Ctx:
        def __init__(self):
            self.messages = []

        def add_user(self, text):
            self.messages.append({"role": "user", "content": text})

    outcomes = iter([OpenAIClientError(BEELINK), ("feito", []), ("confirmo", [])])

    def _turn():
        item = next(outcomes)
        if isinstance(item, Exception):
            raise item
        return item

    stop, rounds, last, _ = serve_loop.run_loop_rounds(
        goal="x", ctx=_Ctx(), turn_fn=_turn,
        budget=AutonomousBudget(max_tool_calls=10, max_wall_seconds=60))
    assert stop == "completed" and last == "confirmo"
