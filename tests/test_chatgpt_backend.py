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
    assert c.has_model("qualquer") is True


def test_list_models_delegates_to_discovery():
    """list_models nao pode voltar a ser lista fixa — ela apodreceu inteira."""
    c = ChatGPTBackendClient(access_token="tok", account_id="acct-1")
    with patch("bauer.chatgpt_backend.discover_models", return_value=["gpt-5.5"]) as disc:
        assert c.list_models() == ["gpt-5.5"]
    disc.assert_called_once_with("tok", "acct-1", base_url=DEFAULT_CHATGPT_BASE)


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


# ── Descoberta de modelos (sonda + cache) ────────────────────────────────────
#
# Contexto medido em 2026-08-07 contra a conta real: o token OAuth da
# assinatura recebe 403 "missing scopes: api.model.read" no /v1/models, e dos
# 31 candidatos sondados no backend Codex SÓ `gpt-5.5` foi aceito — os três da
# lista fixa antiga responderam "not supported when using Codex with a ChatGPT
# account". Daí a sonda.

def _stream_by_status(status_por_modelo: dict[str, int], chamadas: list[str] | None = None):
    """Fake de httpx.stream que responde conforme o modelo do body."""
    def _fake(method, url, **kw):
        model = kw["json"]["model"]
        if chamadas is not None:
            chamadas.append(model)
        return _FakeStream(_FakeResp([], status_code=status_por_modelo.get(model, 400)))
    return _fake


def test_probe_keeps_only_accepted_models():
    from bauer.chatgpt_backend import probe_supported_models

    with patch("httpx.stream", _stream_by_status({"gpt-5.5": 200, "o3-mini": 200})):
        out = probe_supported_models(
            "tok", "acct", candidates=["codex-mini-latest", "gpt-5.5", "o4-mini", "o3-mini"]
        )
    assert out == ["gpt-5.5", "o3-mini"]   # preserva a ordem dos candidatos


def test_probe_survives_network_error():
    from bauer.chatgpt_backend import probe_supported_models

    def _boom(*a, **kw):
        raise OSError("rede caiu")

    with patch("httpx.stream", _boom):
        assert probe_supported_models("tok", None, candidates=["gpt-5.5"]) == []


def test_discover_caches_and_avoids_second_probe(tmp_path, monkeypatch):
    from bauer import chatgpt_backend as cb

    monkeypatch.setattr(cb, "_cache_path", lambda: tmp_path / "chatgpt_models.json")
    chamadas: list[str] = []

    with patch("httpx.stream", _stream_by_status({"gpt-5.5": 200}, chamadas)):
        first = cb.discover_models("tok", "acct")
        n_primeira = len(chamadas)
        second = cb.discover_models("tok", "acct")

    assert first == ["gpt-5.5"] and second == ["gpt-5.5"]
    assert n_primeira > 0
    assert len(chamadas) == n_primeira, "cache nao evitou a segunda sonda"


def test_discover_force_ignores_cache(tmp_path, monkeypatch):
    from bauer import chatgpt_backend as cb

    monkeypatch.setattr(cb, "_cache_path", lambda: tmp_path / "c.json")
    chamadas: list[str] = []
    with patch("httpx.stream", _stream_by_status({"gpt-5.5": 200}, chamadas)):
        cb.discover_models("tok", "acct")
        n = len(chamadas)
        cb.discover_models("tok", "acct", force=True)
    assert len(chamadas) > n


def test_discover_expired_cache_reprobes(tmp_path, monkeypatch):
    from bauer import chatgpt_backend as cb

    path = tmp_path / "c.json"
    monkeypatch.setattr(cb, "_cache_path", lambda: path)
    path.write_text(json.dumps({"acct": {"models": ["velho"], "checked_at": 0}}), encoding="utf-8")

    with patch("httpx.stream", _stream_by_status({"gpt-5.5": 200})):
        assert cb.discover_models("tok", "acct") == ["gpt-5.5"]


def test_discover_does_not_cache_empty_result(tmp_path, monkeypatch):
    """Sonda vazia = rede/token ruim. Cachear deixaria o menu vazio por 24h."""
    from bauer import chatgpt_backend as cb

    path = tmp_path / "c.json"
    monkeypatch.setattr(cb, "_cache_path", lambda: path)

    with patch("httpx.stream", _stream_by_status({})):      # tudo 400
        out = cb.discover_models("tok", "acct")

    assert out == list(cb.CHATGPT_FALLBACK_MODELS)
    assert not path.exists(), "cacheou resultado vazio"


def test_discover_separates_cache_by_account(tmp_path, monkeypatch):
    from bauer import chatgpt_backend as cb

    monkeypatch.setattr(cb, "_cache_path", lambda: tmp_path / "c.json")
    with patch("httpx.stream", _stream_by_status({"gpt-5.5": 200})):
        cb.discover_models("tok", "conta-A")
    with patch("httpx.stream", _stream_by_status({"gpt-5.6": 200})):
        assert cb.discover_models("tok", "conta-B") == ["gpt-5.6"]
    # conta-A nao foi contaminada
    assert cb.discover_models("tok", "conta-A") == ["gpt-5.5"]


def test_discover_survives_corrupt_cache(tmp_path, monkeypatch):
    from bauer import chatgpt_backend as cb

    path = tmp_path / "c.json"
    monkeypatch.setattr(cb, "_cache_path", lambda: path)
    path.write_text("{lixo", encoding="utf-8")

    with patch("httpx.stream", _stream_by_status({"gpt-5.5": 200})):
        assert cb.discover_models("tok", "acct") == ["gpt-5.5"]


def test_dead_models_are_not_offered_as_default():
    """Regressao: a lista fixa anunciava 'codex-mini-latest (recomendado)' e os
    tres modelos dela sao invalidos para conta ChatGPT."""
    from bauer.chatgpt_backend import CHATGPT_FALLBACK_MODELS
    from bauer.model_switcher import CHATGPT_CODEX_MODELS

    mortos = {"codex-mini-latest", "o4-mini", "o3-mini"}
    assert not (set(CHATGPT_FALLBACK_MODELS) & mortos)
    assert not ({m for m, _ in CHATGPT_CODEX_MODELS} & mortos)
