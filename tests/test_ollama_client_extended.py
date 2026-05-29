"""Testes para OllamaClient — cobre linhas 48-58, 63-101, 136-172."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from bauer.ollama_client import OllamaClient, OllamaError, _extract_num_ctx, ModelfileParams


# ─── Fixtures ─────────────────────────────────────────────────────────────────


def _client(host: str = "http://localhost:11434") -> OllamaClient:
    return OllamaClient(host=host)


# ─── is_alive ────────────────────────────────────────────────────────────────


def test_is_alive_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    with patch("httpx.get", return_value=mock_resp):
        alive, reason = _client().is_alive()
    assert alive is True
    assert reason == ""


def test_is_alive_non_200():
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    with patch("httpx.get", return_value=mock_resp):
        alive, reason = _client().is_alive()
    assert alive is False
    assert "503" in reason


def test_is_alive_connect_error():
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        alive, reason = _client().is_alive()
    assert alive is False
    assert "recusada" in reason or "refused" in reason


def test_is_alive_timeout():
    with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
        alive, reason = _client().is_alive()
    assert alive is False
    assert "Timeout" in reason or "timeout" in reason.lower()


def test_is_alive_http_error():
    with patch("httpx.get", side_effect=httpx.HTTPError("generic http error")):
        alive, reason = _client().is_alive()
    assert alive is False
    assert "HTTP" in reason or "http" in reason.lower()


def test_is_alive_unexpected_exception():
    with patch("httpx.get", side_effect=RuntimeError("unexpected")):
        alive, reason = _client().is_alive()
    assert alive is False
    assert "unexpected" in reason or "inesperada" in reason


# ─── list_models ─────────────────────────────────────────────────────────────


def test_list_models_success():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"models": [{"name": "phi4-mini"}, {"name": "qwen3:0.6b"}]}
    mock_resp.raise_for_status.return_value = None
    with patch("httpx.get", return_value=mock_resp):
        models = _client().list_models()
    assert "phi4-mini" in models
    assert "qwen3:0.6b" in models


def test_list_models_http_error():
    with patch("httpx.get", side_effect=httpx.HTTPError("connection failed")):
        with pytest.raises(OllamaError, match="listar modelos"):
            _client().list_models()


def test_list_models_unexpected_error():
    with patch("httpx.get", side_effect=RuntimeError("boom")):
        with pytest.raises(OllamaError, match="inesperada"):
            _client().list_models()


# ─── has_model ────────────────────────────────────────────────────────────────


def test_has_model_true():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"models": [{"name": "phi4-mini"}]}
    with patch("httpx.get", return_value=mock_resp):
        assert _client().has_model("phi4-mini") is True


def test_has_model_false_on_ollamaerror():
    with patch("httpx.get", side_effect=httpx.HTTPError("down")):
        assert _client().has_model("any") is False


# ─── show_model ───────────────────────────────────────────────────────────────


def test_show_model_success():
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"parameters": {"num_ctx": 4096}, "modelfile": "..."}
    with patch("httpx.post", return_value=mock_resp):
        result = _client().show_model("phi4-mini")
    assert isinstance(result, ModelfileParams)
    assert result.num_ctx == 4096


def test_show_model_404_raises():
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    exc = httpx.HTTPStatusError("not found", request=MagicMock(), response=mock_resp)
    with patch("httpx.post", side_effect=exc):
        with pytest.raises(OllamaError, match="nao encontrado"):
            _client().show_model("modelo-inexistente")


def test_show_model_other_http_status_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    exc = httpx.HTTPStatusError("server error", request=MagicMock(), response=mock_resp)
    with patch("httpx.post", side_effect=exc):
        with pytest.raises(OllamaError, match="Erro ao consultar"):
            _client().show_model("phi4-mini")


def test_show_model_http_error():
    with patch("httpx.post", side_effect=httpx.HTTPError("conn failed")):
        with pytest.raises(OllamaError, match="api/show"):
            _client().show_model("phi4-mini")


def test_show_model_unexpected_error():
    with patch("httpx.post", side_effect=RuntimeError("boom")):
        with pytest.raises(OllamaError, match="inesperada"):
            _client().show_model("phi4-mini")


# ─── chat_stream ─────────────────────────────────────────────────────────────


def _make_stream_response(lines: list[str]):
    """Cria mock de httpx.stream context manager."""
    import json as _json

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.iter_lines.return_value = iter(lines)

    ctx = MagicMock()
    ctx.__enter__.return_value = mock_response
    ctx.__exit__.return_value = False
    return ctx


def test_chat_stream_yields_chunks():
    import json as _json
    lines = [
        _json.dumps({"message": {"content": "hello "}, "done": False}),
        _json.dumps({"message": {"content": "world"}, "done": False}),
        _json.dumps({"done": True}),
    ]
    with patch("httpx.stream", return_value=_make_stream_response(lines)):
        chunks = list(_client().chat_stream("phi4-mini", [{"role": "user", "content": "oi"}]))
    assert chunks == ["hello ", "world"]


def test_chat_stream_skips_empty_lines():
    import json as _json
    lines = [
        "",  # linha vazia ignorada
        _json.dumps({"message": {"content": "resposta"}, "done": False}),
        _json.dumps({"done": True}),
    ]
    with patch("httpx.stream", return_value=_make_stream_response(lines)):
        chunks = list(_client().chat_stream("phi4-mini", []))
    assert chunks == ["resposta"]


def test_chat_stream_skips_invalid_json():
    import json as _json
    lines = [
        "isso nao e json",  # linha inválida ignorada
        _json.dumps({"message": {"content": "ok"}, "done": False}),
        _json.dumps({"done": True}),
    ]
    with patch("httpx.stream", return_value=_make_stream_response(lines)):
        chunks = list(_client().chat_stream("phi4-mini", []))
    assert chunks == ["ok"]


def test_chat_stream_connect_error():
    with patch("httpx.stream", side_effect=httpx.ConnectError("refused")):
        with pytest.raises(OllamaError, match="Conexao recusada"):
            list(_client().chat_stream("phi4-mini", []))


def test_chat_stream_timeout():
    with patch("httpx.stream", side_effect=httpx.TimeoutException("timeout")):
        with pytest.raises(OllamaError, match="Timeout"):
            list(_client().chat_stream("phi4-mini", []))


def test_chat_stream_http_status_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    exc = httpx.HTTPStatusError("not found", request=MagicMock(), response=mock_resp)
    with patch("httpx.stream", side_effect=exc):
        with pytest.raises(OllamaError, match="404"):
            list(_client().chat_stream("phi4-mini", []))


def test_chat_stream_http_error():
    with patch("httpx.stream", side_effect=httpx.HTTPError("generic")):
        with pytest.raises(OllamaError, match="api/chat"):
            list(_client().chat_stream("phi4-mini", []))


# ─── _extract_num_ctx ────────────────────────────────────────────────────────


def test_extract_num_ctx_dict_int():
    assert _extract_num_ctx({"num_ctx": 4096}) == 4096


def test_extract_num_ctx_dict_str():
    assert _extract_num_ctx({"num_ctx": "8192"}) == 8192


def test_extract_num_ctx_dict_missing():
    assert _extract_num_ctx({"other": "value"}) is None


def test_extract_num_ctx_string_format():
    assert _extract_num_ctx("num_ctx 4096\nother 100") == 4096


def test_extract_num_ctx_string_no_match():
    assert _extract_num_ctx("no num ctx here") is None


def test_extract_num_ctx_none():
    assert _extract_num_ctx(None) is None


def test_client_with_api_key():
    client = OllamaClient(host="http://localhost", api_key="mykey")
    assert "Authorization" in client._headers
    assert "mykey" in client._headers["Authorization"]
