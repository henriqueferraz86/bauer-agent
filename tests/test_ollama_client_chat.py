"""Testes do OllamaClient.chat_stream (Fase 2).

Testa erros de rede sem precisar de Ollama real (porta inválida = falha imediata).
Testa parsing de NDJSON via mock do httpx.stream.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bauer.ollama_client import OllamaClient, OllamaError


def test_chat_stream_raises_on_connect_error():
    """Porta 1 é inválida — deve levantar OllamaError com mensagem clara."""
    client = OllamaClient("http://127.0.0.1:1", timeout_seconds=1)
    with pytest.raises(OllamaError, match="recusada|Timeout|Erro"):
        list(client.chat_stream("model", [{"role": "user", "content": "hi"}]))


def _make_stream_mock(ndjson_lines: list[str]) -> MagicMock:
    """Monta um mock do context manager retornado por httpx.stream."""
    mock_response = MagicMock()
    mock_response.iter_lines.return_value = iter(ndjson_lines)
    mock_response.raise_for_status.return_value = None
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


def test_chat_stream_yields_chunks():
    """Chunks de conteudo chegam na ordem correta."""
    lines = [
        '{"message": {"role": "assistant", "content": "Ola"}, "done": false}',
        '{"message": {"role": "assistant", "content": " mundo"}, "done": false}',
        '{"done": true}',
    ]
    client = OllamaClient("http://localhost:11434")
    with patch("httpx.stream", return_value=_make_stream_mock(lines)):
        chunks = list(client.chat_stream("test-model", [{"role": "user", "content": "hi"}]))
    assert chunks == ["Ola", " mundo"]


def test_chat_stream_stops_at_done():
    """Linhas apos done:true sao ignoradas."""
    lines = [
        '{"message": {"role": "assistant", "content": "fim"}, "done": false}',
        '{"done": true}',
        '{"message": {"role": "assistant", "content": "nao deve aparecer"}, "done": false}',
    ]
    client = OllamaClient("http://localhost:11434")
    with patch("httpx.stream", return_value=_make_stream_mock(lines)):
        chunks = list(client.chat_stream("test-model", [{"role": "user", "content": "hi"}]))
    assert chunks == ["fim"]


def test_chat_stream_skips_empty_lines():
    """Linhas em branco nao causam erro."""
    lines = [
        "",
        '{"message": {"role": "assistant", "content": "ok"}, "done": false}',
        "",
        '{"done": true}',
    ]
    client = OllamaClient("http://localhost:11434")
    with patch("httpx.stream", return_value=_make_stream_mock(lines)):
        chunks = list(client.chat_stream("test-model", [{"role": "user", "content": "hi"}]))
    assert chunks == ["ok"]


def test_chat_stream_skips_invalid_json():
    """JSON malformado e ignorado sem crashar."""
    lines = [
        "isso nao e json",
        '{"message": {"role": "assistant", "content": "valido"}, "done": false}',
        '{"done": true}',
    ]
    client = OllamaClient("http://localhost:11434")
    with patch("httpx.stream", return_value=_make_stream_mock(lines)):
        chunks = list(client.chat_stream("test-model", [{"role": "user", "content": "hi"}]))
    assert chunks == ["valido"]


def test_chat_stream_skips_empty_content():
    """Chunks com content vazio nao sao emitidos."""
    lines = [
        '{"message": {"role": "assistant", "content": ""}, "done": false}',
        '{"message": {"role": "assistant", "content": "real"}, "done": false}',
        '{"done": true}',
    ]
    client = OllamaClient("http://localhost:11434")
    with patch("httpx.stream", return_value=_make_stream_mock(lines)):
        chunks = list(client.chat_stream("test-model", [{"role": "user", "content": "hi"}]))
    assert chunks == ["real"]
