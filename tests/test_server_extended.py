"""Testes para server.py — endpoints FastAPI via TestClient."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Verifica se FastAPI está disponível antes de importar
fastapi = pytest.importorskip("fastapi", reason="FastAPI não instalado")
from fastapi.testclient import TestClient  # noqa: E402

from bauer.server import create_app  # noqa: E402


# ─── Fixture de app ─────────────────────────────────────────────────────────


def _make_app(
    tmp_path: Path,
    api_key: str = "",
    rate_limit: int = 0,  # 0 = desabilitado nos testes
    client_reply: str = "resposta do modelo",
) -> TestClient:
    from bauer.tool_router import ToolRouter

    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter([client_reply])
    mock_client.list_models.return_value = ["phi4-mini", "qwen3:0.6b"]
    mock_client.has_model.return_value = True

    router = ToolRouter(workspace=tmp_path)

    app = create_app(
        model_name="phi4-mini",
        applied_context=4096,
        router=router,
        client=mock_client,
        system_prompt="Voce e o Bauer.",
        sessions_dir=tmp_path / "sessions",
        api_key=api_key,
        rate_limit_requests=rate_limit,
        rate_limit_window_s=60.0,
    )
    return TestClient(app, raise_server_exceptions=True)


# ─── /health ─────────────────────────────────────────────────────────────────


def test_health_ok(tmp_path: Path):
    client = _make_app(tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["model"] == "phi4-mini"


# ─── /status ─────────────────────────────────────────────────────────────────


def test_status_returns_model_and_tools(tmp_path: Path):
    client = _make_app(tmp_path)
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "phi4-mini"
    assert "tools" in data
    assert "context_tokens" in data
    assert data["auth_enabled"] is False


def test_status_auth_enabled_with_key(tmp_path: Path):
    client = _make_app(tmp_path, api_key="secret123")
    resp = client.get("/status", headers={"X-API-Key": "secret123"})
    assert resp.json()["auth_enabled"] is True


# ─── /tools ──────────────────────────────────────────────────────────────────


def test_tools_list(tmp_path: Path):
    client = _make_app(tmp_path)
    resp = client.get("/tools")
    assert resp.status_code == 200
    tools = resp.json()
    assert isinstance(tools, list)
    # ToolRouter sempre tem pelo menos list_dir, read_file, etc.
    names = [t["name"] for t in tools]
    assert "list_dir" in names


# ─── /models ─────────────────────────────────────────────────────────────────


def test_models_list(tmp_path: Path):
    client = _make_app(tmp_path)
    resp = client.get("/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] == "phi4-mini"
    assert "phi4-mini" in data["installed"]


def test_models_list_client_error(tmp_path: Path):
    """Se o cliente falhar ao listar modelos, retorna lista vazia."""
    from bauer.tool_router import ToolRouter
    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter(["ok"])
    mock_client.list_models.side_effect = RuntimeError("ollama offline")
    mock_client.has_model.return_value = True

    router = ToolRouter(workspace=tmp_path)
    app = create_app(
        model_name="phi4-mini",
        applied_context=4096,
        router=router,
        client=mock_client,
        system_prompt="s",
        sessions_dir=tmp_path / "sessions",
        api_key="",
        rate_limit_requests=0,
    )
    tc = TestClient(app)
    resp = tc.get("/models")
    assert resp.status_code == 200
    assert resp.json()["installed"] == []


# ─── /models/switch ──────────────────────────────────────────────────────────


def test_models_switch_success(tmp_path: Path):
    client = _make_app(tmp_path)
    resp = client.post("/models/switch", json={"model": "qwen3:0.6b"})
    assert resp.status_code == 200
    assert resp.json()["active"] == "qwen3:0.6b"


def test_models_switch_missing_model_field(tmp_path: Path):
    client = _make_app(tmp_path)
    resp = client.post("/models/switch", json={})
    assert resp.status_code == 400


def test_models_switch_model_not_found(tmp_path: Path):
    from bauer.tool_router import ToolRouter
    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter(["ok"])
    mock_client.has_model.return_value = False

    router = ToolRouter(workspace=tmp_path)
    app = create_app(
        model_name="phi4-mini", applied_context=4096,
        router=router, client=mock_client,
        system_prompt="s", sessions_dir=tmp_path / "sessions",
        api_key="", rate_limit_requests=0,
    )
    tc = TestClient(app)
    resp = tc.post("/models/switch", json={"model": "nao-existe"})
    assert resp.status_code == 404


# ─── Auth ────────────────────────────────────────────────────────────────────


def test_auth_required_without_key_returns_401(tmp_path: Path):
    client = _make_app(tmp_path, api_key="my-secret")
    resp = client.get("/sessions")
    assert resp.status_code == 401


def test_auth_with_x_api_key_header(tmp_path: Path):
    client = _make_app(tmp_path, api_key="my-secret")
    resp = client.get("/sessions", headers={"X-API-Key": "my-secret"})
    assert resp.status_code == 200


def test_auth_with_bearer_token(tmp_path: Path):
    client = _make_app(tmp_path, api_key="my-secret")
    resp = client.get("/sessions", headers={"Authorization": "Bearer my-secret"})
    assert resp.status_code == 200


def test_auth_wrong_key_returns_401(tmp_path: Path):
    client = _make_app(tmp_path, api_key="correct")
    resp = client.get("/sessions", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401


def test_no_auth_when_api_key_empty(tmp_path: Path):
    client = _make_app(tmp_path, api_key="")
    resp = client.get("/sessions")
    assert resp.status_code == 200


# ─── /sessions ───────────────────────────────────────────────────────────────


def test_list_sessions_empty(tmp_path: Path):
    client = _make_app(tmp_path)
    resp = client.get("/sessions")
    assert resp.status_code == 200
    assert resp.json()["sessions"] == []


def test_delete_session_not_found(tmp_path: Path):
    client = _make_app(tmp_path)
    resp = client.delete("/sessions/nao-existe")
    assert resp.status_code == 404


def test_delete_session_success(tmp_path: Path):
    from bauer.session_store import SessionStore
    store = SessionStore(tmp_path / "sessions")
    store.save("sess01", [{"role": "user", "content": "oi"}])

    client = _make_app(tmp_path)
    resp = client.delete("/sessions/sess01")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == "sess01"


# ─── /chat ───────────────────────────────────────────────────────────────────


def test_chat_simple_message(tmp_path: Path):
    """POST /chat com mensagem simples — sem tool calls."""
    with patch("bauer.agent.run_one_turn") as mock_turn:
        mock_turn.return_value = ("resposta do bauer", [])
        client = _make_app(tmp_path)
        resp = client.post("/chat", json={"message": "oi bauer"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["response"] == "resposta do bauer"
    assert data["model"] == "phi4-mini"
    assert data["tool_calls"] == []
    assert "session_id" in data


def test_chat_with_session_id(tmp_path: Path):
    """POST /chat persiste e retoma sessão."""
    with patch("bauer.agent.run_one_turn") as mock_turn:
        mock_turn.return_value = ("resposta", [])
        client = _make_app(tmp_path)
        # Primeira mensagem
        resp1 = client.post("/chat", json={"message": "oi", "session_id": "fixed-id"})
        assert resp1.status_code == 200
        assert resp1.json()["session_id"] == "fixed-id"

        # Segunda mensagem na mesma sessão
        resp2 = client.post("/chat", json={"message": "tudo bem?", "session_id": "fixed-id"})
        assert resp2.status_code == 200


def test_chat_with_tool_calls(tmp_path: Path):
    """POST /chat com tool calls retornadas."""
    with patch("bauer.agent.run_one_turn") as mock_turn:
        mock_turn.return_value = (
            "listei os arquivos",
            [{"tool": "list_dir", "result": "arquivo.txt"}],
        )
        client = _make_app(tmp_path)
        resp = client.post("/chat", json={"message": "liste arquivos"})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["tool_calls"]) == 1
    assert data["tool_calls"][0]["tool"] == "list_dir"


def test_chat_error_returns_500(tmp_path: Path):
    """Quando run_one_turn lança exceção, retorna 500."""
    with patch("bauer.agent.run_one_turn") as mock_turn:
        mock_turn.side_effect = RuntimeError("modelo falhou")
        client = _make_app(tmp_path)
        resp = client.post("/chat", json={"message": "oi"})

    assert resp.status_code == 500


# ─── Rate Limiting ────────────────────────────────────────────────────────────


def test_rate_limit_blocks_after_limit(tmp_path: Path):
    """Após N requisições em endpoint com auth, retorna 429."""
    client = _make_app(tmp_path, rate_limit=2)
    # /sessions tem Depends(_verify_key) que invoca _check_rate_limit
    r1 = client.get("/sessions")
    r2 = client.get("/sessions")
    assert r1.status_code == 200
    assert r2.status_code == 200
    # Terceira requisição deve ser bloqueada
    r3 = client.get("/sessions")
    assert r3.status_code == 429
    assert "Retry-After" in r3.headers


def test_rate_limit_disabled_when_zero(tmp_path: Path):
    """rate_limit=0 desativa o limiter."""
    client = _make_app(tmp_path, rate_limit=0)
    for _ in range(10):
        resp = client.get("/health")
        assert resp.status_code == 200


# ─── /stream ─────────────────────────────────────────────────────────────────


def test_stream_response_sse_format(tmp_path: Path):
    """GET /stream retorna SSE com dados."""
    from bauer.tool_router import ToolRouter
    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter(["Ola ", "mundo"])
    router = ToolRouter(workspace=tmp_path)

    with patch("bauer.agent._try_parse_tool", return_value=None):
        app = create_app(
            model_name="phi4-mini", applied_context=4096,
            router=router, client=mock_client,
            system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        tc = TestClient(app)
        resp = tc.get("/stream?message=oi")

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]


# ─── /v1/chat/completions (OpenAI-compat / Claw3D) ───────────────────────────


class TestOAIChatCompletions:
    """Testa o endpoint OpenAI-compatible para integração com Claw3D."""

    def _make_oai_app(self, tmp_path, reply="resposta bauer"):
        from unittest.mock import MagicMock, patch
        from bauer.tool_router import ToolRouter

        mock_client = MagicMock()
        mock_client.chat_stream.return_value = iter([reply])

        with patch("bauer.agent._try_parse_tool", return_value=None), \
             patch("bauer.agent.run_one_turn", return_value=(reply, [])):
            router = ToolRouter(workspace=tmp_path)
            app = create_app(
                model_name="phi4-mini", applied_context=4096,
                router=router, client=mock_client,
                system_prompt="s", sessions_dir=tmp_path / "sessions",
                api_key="", rate_limit_requests=0,
            )
            return TestClient(app, raise_server_exceptions=True), mock_client

    def test_post_non_streaming_returns_oai_format(self, tmp_path):
        tc, _ = self._make_oai_app(tmp_path)
        resp = tc.post("/v1/chat/completions", json={
            "model": "phi4-mini",
            "messages": [{"role": "user", "content": "hello"}],
            "stream": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "choices" in data
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert data["choices"][0]["finish_reason"] == "stop"
        assert "usage" in data
        assert "id" in data
        assert data["id"].startswith("chatcmpl-bauer-")

    def test_post_non_streaming_returns_session_header(self, tmp_path):
        tc, _ = self._make_oai_app(tmp_path)
        resp = tc.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
        })
        assert resp.status_code == 200
        assert "x-hermes-session-id" in resp.headers

    def test_post_streaming_returns_sse(self, tmp_path):
        from unittest.mock import MagicMock, patch
        from bauer.tool_router import ToolRouter

        mock_client = MagicMock()
        mock_client.chat_stream.return_value = iter(["hello", " world"])

        with patch("bauer.agent._try_parse_tool", return_value=None):
            router = ToolRouter(workspace=tmp_path)
            app = create_app(
                model_name="phi4-mini", applied_context=4096,
                router=router, client=mock_client,
                system_prompt="s", sessions_dir=tmp_path / "sessions",
                api_key="", rate_limit_requests=0,
            )
            tc = TestClient(app)
            resp = tc.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = resp.text
        assert "data:" in body
        assert "[DONE]" in body

    def test_streaming_chunks_have_oai_format(self, tmp_path):
        import json
        from unittest.mock import MagicMock, patch
        from bauer.tool_router import ToolRouter

        mock_client = MagicMock()
        mock_client.chat_stream.return_value = iter(["Hello"])

        with patch("bauer.agent._try_parse_tool", return_value=None):
            router = ToolRouter(workspace=tmp_path)
            app = create_app(
                model_name="phi4-mini", applied_context=4096,
                router=router, client=mock_client,
                system_prompt="s", sessions_dir=tmp_path / "sessions",
                api_key="", rate_limit_requests=0,
            )
            tc = TestClient(app)
            resp = tc.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            })

        # Parseia cada linha SSE data:
        chunks = []
        for line in resp.text.splitlines():
            if line.startswith("data:") and "[DONE]" not in line:
                payload = line[5:].strip()
                if payload:
                    chunks.append(json.loads(payload))

        assert len(chunks) > 0
        # Cada chunk deve ter o formato OpenAI
        for c in chunks:
            assert "choices" in c
            assert "delta" in c["choices"][0]

    def test_session_id_header_is_honored(self, tmp_path):
        tc, _ = self._make_oai_app(tmp_path)
        resp = tc.post(
            "/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "hi"}], "stream": False},
            headers={"X-Hermes-Session-Id": "test-session-abc"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("x-hermes-session-id") == "test-session-abc"

    def test_multi_turn_messages(self, tmp_path):
        tc, _ = self._make_oai_app(tmp_path)
        resp = tc.post("/v1/chat/completions", json={
            "messages": [
                {"role": "user",      "content": "what is 2+2?"},
                {"role": "assistant", "content": "4"},
                {"role": "user",      "content": "and 3+3?"},
            ],
            "stream": False,
        })
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"]


# ─── /v1/models ──────────────────────────────────────────────────────────────


def test_oai_models_endpoint(tmp_path):
    tc = _make_app(tmp_path)
    resp = tc.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    assert len(data["data"]) >= 1
    assert data["data"][0]["id"] == "phi4-mini"
    assert data["data"][0]["owned_by"] == "bauer-agent"
