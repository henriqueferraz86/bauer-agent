"""Testes do OpenAIClient (provider openai-compatible)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bauer.openai_client import OpenAIClient, OpenAIClientError


@pytest.fixture
def client() -> OpenAIClient:
    return OpenAIClient(
        host="http://localhost:1234",
        timeout_seconds=5,
        api_key="",
        model="test-model",
    )


@pytest.fixture
def authed_client() -> OpenAIClient:
    return OpenAIClient(
        host="http://localhost:1234",
        timeout_seconds=5,
        api_key="sk-test",
        model="test-model",
    )


# --- headers ----------------------------------------------------------------


def test_auth_header_set_when_key_provided(authed_client: OpenAIClient):
    assert authed_client._headers.get("Authorization") == "Bearer sk-test"


def test_no_auth_header_when_key_empty(client: OpenAIClient):
    assert "Authorization" not in client._headers


# --- is_alive ---------------------------------------------------------------


def test_is_alive_returns_true_on_200():
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        c = OpenAIClient("http://localhost:1234", 5, "", "m")
        ok, msg = c.is_alive()
        assert ok is True
        assert msg == ""


def test_is_alive_returns_true_on_401():
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_get.return_value = mock_resp

        c = OpenAIClient("http://localhost:1234", 5, "", "m")
        ok, _ = c.is_alive()
        assert ok is True  # 401 = API alive, just needs auth


def test_is_alive_returns_false_on_connect_error():
    import httpx
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        c = OpenAIClient("http://localhost:1234", 5, "", "m")
        ok, msg = c.is_alive()
        assert ok is False
        assert "recusada" in msg.lower() or "refused" in msg.lower()


def test_is_alive_returns_false_on_timeout():
    import httpx
    with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
        c = OpenAIClient("http://localhost:1234", 5, "", "m")
        ok, msg = c.is_alive()
        assert ok is False
        assert "timeout" in msg.lower() or "Timeout" in msg


# --- list_models ------------------------------------------------------------


def test_list_models_returns_ids():
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "data": [{"id": "model-a"}, {"id": "model-b"}]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        c = OpenAIClient("http://localhost:1234", 5, "", "m")
        models = c.list_models()
        assert "model-a" in models
        assert "model-b" in models


def test_list_models_raises_on_error():
    with patch("httpx.get", side_effect=Exception("network error")):
        c = OpenAIClient("http://localhost:1234", 5, "", "m")
        with pytest.raises(OpenAIClientError):
            c.list_models()


# --- has_model --------------------------------------------------------------


def test_has_model_true():
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"id": "gpt-4o"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        c = OpenAIClient("http://localhost:1234", 5, "", "m")
        assert c.has_model("gpt-4o") is True


def test_has_model_false():
    with patch("httpx.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": [{"id": "gpt-4o"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        c = OpenAIClient("http://localhost:1234", 5, "", "m")
        assert c.has_model("llama3") is False


def test_has_model_returns_true_on_list_error():
    with patch("httpx.get", side_effect=Exception("no list endpoint")):
        c = OpenAIClient("http://localhost:1234", 5, "", "m")
        assert c.has_model("any-model") is True  # assumes available if can't list


# --- chat_stream ------------------------------------------------------------


def test_chat_stream_yields_chunks():
    import httpx

    sse_lines = [
        'data: {"choices":[{"delta":{"content":"Ola"}}]}',
        'data: {"choices":[{"delta":{"content":" mundo"}}]}',
        "data: [DONE]",
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.iter_lines.return_value = iter(sse_lines)
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("httpx.Client.stream", return_value=mock_response):
        c = OpenAIClient("http://localhost:1234", 5, "", "m")
        with patch.object(httpx, "stream", return_value=mock_response):
            chunks = list(c.chat_stream("m", [{"role": "user", "content": "oi"}]))

    assert "Ola" in chunks
    assert " mundo" in chunks


def test_chat_stream_raises_on_connect_error():
    import httpx

    with patch("httpx.stream", side_effect=httpx.ConnectError("refused")):
        c = OpenAIClient("http://localhost:1234", 5, "", "m")
        with pytest.raises(OpenAIClientError, match="Conexao"):
            list(c.chat_stream("m", []))


def test_chat_stream_429_with_int_code_does_not_crash():
    """Regressão: alguns providers (openrouter) devolvem error.code como INT
    (ex.: 429). O handler de 429 fazia `"x" in _error_type` e crashava com
    TypeError: argument of type 'int' is not iterable — mascarando o erro real
    de rate-limit e quebrando o fallback."""
    import json
    import httpx

    body = json.dumps({"error": {"code": 429, "message": "Rate limit exceeded"}})
    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.request = MagicMock()
    mock_response.iter_bytes.return_value = iter([body.encode("utf-8")])
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("httpx.stream", return_value=mock_response):
        c = OpenAIClient("http://localhost:1234", 5, "", "m")
        # Deve levantar OpenAIClientError limpo (rate limit), NÃO TypeError.
        with pytest.raises(OpenAIClientError) as exc_info:
            list(c.chat_stream("m", [{"role": "user", "content": "oi"}]))
    assert not isinstance(exc_info.value.__cause__, TypeError)
    assert "429" in str(exc_info.value) or "rate" in str(exc_info.value).lower() or "limit" in str(exc_info.value).lower()
