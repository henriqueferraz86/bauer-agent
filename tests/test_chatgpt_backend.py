"""Testes do ChatGPTBackendClient (login via browser / Responses API)."""
from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest

from bauer.chatgpt_backend import ChatGPTBackendClient, DEFAULT_CHATGPT_BASE, OpenAIClientError


# ── Helpers ─────────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code
        self.request = None

    def iter_lines(self):
        yield from self._lines

    def iter_bytes(self):
        yield b"erro detalhado"


class _FakeStream:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, *a):
        return False


# ── account_id extraction (em auth.py, mas central p/ esta feature) ──────────

def test_extract_account_id_from_jwt():
    from bauer.auth import _extract_chatgpt_account_id
    payload = {"https://api.openai.com/auth": {"chatgpt_account_id": "acct-xyz"}}
    b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    jwt = f"header.{b64}.sig"
    assert _extract_chatgpt_account_id(jwt) == "acct-xyz"


def test_extract_account_id_handles_none_and_garbage():
    from bauer.auth import _extract_chatgpt_account_id
    assert _extract_chatgpt_account_id(None) == ""
    assert _extract_chatgpt_account_id("not-a-jwt") == ""
    assert _extract_chatgpt_account_id("a.b") == ""  # payload inválido


# ── Client build ─────────────────────────────────────────────────────────────

def test_client_headers_and_defaults():
    c = ChatGPTBackendClient(access_token="tok", account_id="acct-1", model="gpt-5")
    assert c.host == DEFAULT_CHATGPT_BASE
    assert c._headers["Authorization"] == "Bearer tok"
    assert c._headers["chatgpt-account-id"] == "acct-1"
    assert c._headers["originator"] == "codex_cli_rs"
    assert "session_id" in c._headers
    # Usa bridge de tools por texto, não native
    assert c.supports_native_tools is False
    assert c.is_alive() == (True, "")
    assert c.list_models() == []
    assert c.has_model("qualquer") is True


def test_client_without_account_id_omits_header():
    c = ChatGPTBackendClient(access_token="tok", account_id="", model="gpt-5")
    assert "chatgpt-account-id" not in c._headers


def test_custom_base_url():
    c = ChatGPTBackendClient(access_token="t", base_url="https://example.com/api")
    assert c.host == "https://example.com/api"


# ── Tradução chat → Responses input ──────────────────────────────────────────

def test_to_responses_input_separates_system():
    instr, items = ChatGPTBackendClient._to_responses_input([
        {"role": "system", "content": "Voce e o Bauer."},
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "ola"},
    ])
    assert instr == "Voce e o Bauer."
    assert len(items) == 2
    assert items[0]["role"] == "user"
    assert items[0]["content"][0]["type"] == "input_text"
    assert items[1]["role"] == "assistant"
    assert items[1]["content"][0]["type"] == "output_text"


def test_to_responses_input_merges_multiple_system():
    instr, items = ChatGPTBackendClient._to_responses_input([
        {"role": "system", "content": "A."},
        {"role": "system", "content": "B."},
        {"role": "user", "content": "x"},
    ])
    assert "A." in instr and "B." in instr
    assert len(items) == 1


# ── Streaming (Responses SSE) ────────────────────────────────────────────────

def test_chat_stream_yields_deltas_and_usage():
    lines = [
        'data: {"type":"response.output_text.delta","delta":"Ola"}',
        'data: {"type":"response.output_text.delta","delta":" mundo"}',
        'data: {"type":"response.completed","response":{"usage":{"input_tokens":3,"output_tokens":2}}}',
        "data: [DONE]",
    ]
    c = ChatGPTBackendClient(access_token="t", account_id="a")
    with patch("httpx.stream", return_value=_FakeStream(_FakeResp(lines))):
        out = "".join(c.chat_stream("gpt-5", [{"role": "user", "content": "oi"}]))
    assert out == "Ola mundo"
    assert c.last_usage == {"input_tokens": 3, "output_tokens": 2}


def test_chat_stream_ignores_unknown_events():
    lines = [
        'data: {"type":"response.output_item.added"}',
        'data: {"type":"response.output_text.delta","delta":"oi"}',
        'data: not-json',
        "",
    ]
    c = ChatGPTBackendClient(access_token="t")
    with patch("httpx.stream", return_value=_FakeStream(_FakeResp(lines))):
        out = "".join(c.chat_stream("gpt-5", [{"role": "user", "content": "x"}]))
    assert out == "oi"


def test_chat_stream_401_raises_clear_error():
    c = ChatGPTBackendClient(access_token="t", account_id="a")
    with patch("httpx.stream", return_value=_FakeStream(_FakeResp([], status_code=401))):
        with pytest.raises(OpenAIClientError) as exc:
            list(c.chat_stream("gpt-5", [{"role": "user", "content": "x"}]))
    assert "Token OAuth" in str(exc.value) or "401" in str(exc.value)


def test_chat_stream_error_event_raises():
    lines = ['data: {"type":"error","error":{"message":"boom"}}']
    c = ChatGPTBackendClient(access_token="t")
    with patch("httpx.stream", return_value=_FakeStream(_FakeResp(lines))):
        with pytest.raises(OpenAIClientError) as exc:
            list(c.chat_stream("gpt-5", [{"role": "user", "content": "x"}]))
    assert "boom" in str(exc.value)
