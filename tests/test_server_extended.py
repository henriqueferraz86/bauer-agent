"""Testes para server.py — endpoints FastAPI via TestClient."""

from __future__ import annotations

import time
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


def test_chat_refreshes_system_prompt_each_request(tmp_path: Path):
    """Server nao deve congelar data/hora do system prompt no startup."""
    from bauer.tool_router import ToolRouter

    mock_client = MagicMock()
    mock_client.chat_stream.side_effect = [iter(["ok 1"]), iter(["ok 2"])]
    router = ToolRouter(workspace=tmp_path)
    app = create_app(
        model_name="phi4-mini",
        applied_context=4096,
        router=router,
        client=mock_client,
        system_prompt="startup prompt",
        sessions_dir=tmp_path / "sessions",
        api_key="",
        rate_limit_requests=0,
    )

    with patch("bauer.agent._build_system_prompt", side_effect=["fresh time 1", "fresh time 2"]):
        client = TestClient(app)
        assert client.post("/chat", json={"message": "que horas sao?", "session_id": "a"}).status_code == 200
        assert client.post("/chat", json={"message": "que horas sao?", "session_id": "b"}).status_code == 200

    first_payload = mock_client.chat_stream.call_args_list[0][0][1]
    second_payload = mock_client.chat_stream.call_args_list[1][0][1]
    assert first_payload[0]["content"] == "fresh time 1"
    assert second_payload[0]["content"] == "fresh time 2"


def test_chat_response_is_trimmed_and_blank_lines_normalized(tmp_path: Path):
    """Server devolve texto limpo para clientes REST simples."""
    with patch("bauer.agent.run_one_turn") as mock_turn:
        mock_turn.return_value = ("\n\n  Hoje sao 10:30.\n\n\n\n", [])
        client = _make_app(tmp_path)
        resp = client.post("/chat", json={"message": "hora"})

    assert resp.status_code == 200
    assert resp.json()["response"] == "Hoje sao 10:30."


def test_chat_error_returns_500(tmp_path: Path):
    """Quando run_one_turn lança exceção, retorna 500."""
    with patch("bauer.agent.run_one_turn") as mock_turn:
        mock_turn.side_effect = RuntimeError("modelo falhou")
        client = _make_app(tmp_path)
        resp = client.post("/chat", json={"message": "oi"})

    assert resp.status_code == 500


# ─── /transcribe ─────────────────────────────────────────────────────────────


def test_transcribe_success(tmp_path: Path):
    with patch("bauer.transcription.transcribe_audio") as mock_stt:
        mock_stt.return_value = {"success": True, "transcript": "oi bauer", "provider": "local"}
        client = _make_app(tmp_path)
        resp = client.post(
            "/transcribe",
            files={"file": ("voice.webm", b"fake-audio-bytes", "audio/webm")},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["transcript"] == "oi bauer"
    assert data["provider"] == "local"
    called_path = mock_stt.call_args[0][0]
    assert str(called_path).endswith(".webm")


def test_transcribe_failure_returns_422(tmp_path: Path):
    with patch("bauer.transcription.transcribe_audio") as mock_stt:
        mock_stt.return_value = {"success": False, "transcript": "", "error": "sem provider STT"}
        client = _make_app(tmp_path)
        resp = client.post(
            "/transcribe",
            files={"file": ("voice.webm", b"fake-audio-bytes", "audio/webm")},
        )

    assert resp.status_code == 422
    assert "sem provider STT" in resp.json()["detail"]


def test_transcribe_requires_auth(tmp_path: Path):
    client = _make_app(tmp_path, api_key="secret")
    resp = client.post("/transcribe", files={"file": ("voice.webm", b"abc", "audio/webm")})
    assert resp.status_code == 401


def test_transcribe_cleans_up_temp_file(tmp_path: Path):
    """O arquivo temporário não deve sobreviver após a transcrição."""
    captured: dict = {}

    def _fake_transcribe(path, model=None):
        captured["path"] = Path(path)
        assert captured["path"].exists()
        return {"success": True, "transcript": "ok", "provider": "local"}

    with patch("bauer.transcription.transcribe_audio", side_effect=_fake_transcribe):
        client = _make_app(tmp_path)
        resp = client.post("/transcribe", files={"file": ("voice.ogg", b"abc", "audio/ogg")})

    assert resp.status_code == 200
    assert not captured["path"].exists()


def test_transcribe_rejects_bad_extension_415(tmp_path: Path):
    """Extensão fora da whitelist é rejeitada ANTES de escrever em disco."""
    with patch("bauer.transcription.transcribe_audio") as mock_stt:
        client = _make_app(tmp_path)
        resp = client.post("/transcribe", files={"file": ("payload.exe", b"abc", "application/octet-stream")})

    assert resp.status_code == 415
    mock_stt.assert_not_called()  # nunca chegou a transcrever


def test_transcribe_rejects_oversized_413(tmp_path: Path):
    """Corpo acima do limite é cortado no streaming → 413 sem materializar tudo.

    Reduz MAX_AUDIO_BYTES para um valor minúsculo e envia um corpo maior — o
    corte por chunk deve disparar 413 e nunca chamar a transcrição."""
    with patch("bauer.transcription.MAX_AUDIO_BYTES", 8), \
         patch("bauer.transcription.transcribe_audio") as mock_stt:
        client = _make_app(tmp_path)
        resp = client.post(
            "/transcribe",
            files={"file": ("voice.webm", b"x" * 4096, "audio/webm")},
        )

    assert resp.status_code == 413
    mock_stt.assert_not_called()


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


def test_stream_turn_timeout_returns_friendly_message(tmp_path: Path):
    """Turno que estoura o timeout de wall-clock retorna mensagem amigavel em
    vez de ficar pendurado pra sempre (regressao: /stream nao tinha paridade
    com o TurnTimeout ja existente no gateway/Telegram).

    Usa um time.sleep real (curto) em vez de mockar time.monotonic — mockar o
    relogio globalmente via "bauer.server.time.monotonic" afeta o modulo time
    inteiro (o mesmo objeto usado pelo scheduling interno do asyncio/uvicorn),
    o que trava o event loop em vez de so afetar o codigo sob teste.

    NÃO afirma `chat_stream.call_count`: desde que /stream passou a rodar
    run_one_turn_with_fallback numa thread de fundo (motor compartilhado com
    o CLI), o loop de tool calls do worker continua de propósito DEPOIS que o
    gerador desiste e devolve a mensagem de timeout — é exatamente o
    comportamento que permite o turno concluir em segundo plano. Quantas
    chamadas já rodaram no instante exato do assert é ruído de agendamento de
    thread, não um invariante do comportamento.
    """
    from bauer.tool_router import ToolRouter

    def _slow_response(*_a, **_k):
        time.sleep(0.05)
        return iter(['{"action": "datetime_now", "args": {}}'])

    mock_client = MagicMock()
    mock_client.chat_stream.side_effect = _slow_response
    router = ToolRouter(workspace=tmp_path)

    app = create_app(
        model_name="phi4-mini", applied_context=4096,
        router=router, client=mock_client,
        system_prompt="s", sessions_dir=tmp_path / "sessions",
        api_key="", rate_limit_requests=0,
    )
    tc = TestClient(app)

    with patch("bauer.server._STREAM_TURN_TIMEOUT_SECONDS", 0.03):
        resp = tc.get("/stream?message=que horas sao")

    assert resp.status_code == 200
    assert "passou de" in resp.text  # mensagem honesta sobre timeout (2026-07-09)
    assert "event: done" in resp.text


def test_stream_timeout_worker_persists_full_history_after_background_completes(tmp_path: Path):
    """Regressão real do usuário: turno estourou o timeout, a UI mostrou
    'cancelei', mas o arquivo só apareceu na 2ª mensagem — porque a thread do
    turno segue rodando em segundo plano após o timeout (é uma thread órfã,
    não cancelada de verdade), e a persistência antiga só acontecia no
    caminho feliz do gerador. Se o timeout cortava ANTES do turno terminar, a
    sessão salva ficava incompleta (perdia os tool calls que rodaram DEPOIS
    do corte) e a run ficava "failed" para sempre — mesmo quando o trabalho
    real concluiu com sucesso.

    Agora o `_worker` persiste sozinho, sempre, no fim do turno real — este
    teste espera o trabalho de fundo terminar e confirma que a sessão salva
    contém a conversa completa e a run convergiu para "completed"."""
    import time as _time
    from bauer.tool_router import ToolRouter

    calls = {"n": 0}

    def _slow_then_fast(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            _time.sleep(0.15)  # ultrapassa o deadline minúsculo do teste
            return iter(['{"action": "calculate", "args": {"expression": "1+1"}}'])
        return iter(["Cálculo concluído: 2."])

    mock_client = MagicMock()
    mock_client.chat_stream.side_effect = _slow_then_fast
    router = ToolRouter(workspace=tmp_path)

    app = create_app(
        model_name="phi4-mini", applied_context=4096,
        router=router, client=mock_client,
        system_prompt="s", sessions_dir=tmp_path / "sessions",
        api_key="", rate_limit_requests=0,
    )
    tc = TestClient(app)

    with patch("bauer.server._STREAM_TURN_TIMEOUT_SECONDS", 0.05):
        resp = tc.get("/stream?message=calcule 1+1")

    assert resp.status_code == 200
    assert "passou de" in resp.text  # timeout amigável já disparou na stream

    sid = resp.headers["X-Session-ID"]
    run_id = resp.headers["X-Bauer-Run-ID"]

    from bauer.core.runtime.run_manager import RunManager
    from bauer.session_store import SessionStore

    runtime_root = tmp_path / "sessions" / ".." / "runtime"
    rm = RunManager(root=(tmp_path / "runtime"))
    store = SessionStore(tmp_path / "sessions")

    # A thread de fundo ainda está terminando o turno real — espera convergir.
    deadline = _time.monotonic() + 3.0
    run = rm.get_run(run_id)
    while _time.monotonic() < deadline and (run is None or run.status != "completed"):
        _time.sleep(0.02)
        run = rm.get_run(run_id)

    assert run is not None and run.status == "completed", (
        "run deveria convergir para 'completed' quando o trabalho de fundo termina"
    )

    saved = store.load(sid)
    blob = "\n".join(str(m.get("content", "")) for m in saved)
    assert "Cálculo concluído: 2." in blob, (
        "sessão salva deveria conter a resposta final gerada DEPOIS do timeout"
    )


def test_stream_falls_back_on_provider_429(tmp_path: Path):
    """Primário dá 429 antes de qualquer chunk → /stream cai no fallback e
    entrega a resposta do provider alternativo (paridade com o CLI)."""
    from bauer.openai_client import OpenAIClientError
    from bauer.tool_router import ToolRouter

    primary = MagicMock()
    primary.chat_stream.side_effect = OpenAIClientError("HTTP 429 do provider")
    fb = MagicMock()
    fb.chat_stream.side_effect = lambda *a, **k: iter(["resposta ", "do fallback"])
    router = ToolRouter(workspace=tmp_path)

    with patch("bauer.agent._try_parse_tool", return_value=None):
        app = create_app(
            model_name="phi4-mini", applied_context=4096,
            router=router, client=primary,
            system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
            fallback_clients=[(fb, "fallback-model")],
        )
        tc = TestClient(app)
        resp = tc.get("/stream?message=oi")

    assert resp.status_code == 200
    assert "resposta do fallback" in resp.text.replace("data: ", "").replace("\n", "")
    assert "event: done" in resp.text
    fb.chat_stream.assert_called()


def test_stream_no_fallback_shows_error(tmp_path: Path):
    """Sem fallback, um erro de provider vira mensagem de erro (comportamento antigo)."""
    from bauer.openai_client import OpenAIClientError
    from bauer.tool_router import ToolRouter

    primary = MagicMock()
    primary.chat_stream.side_effect = OpenAIClientError("HTTP 429 do provider")
    router = ToolRouter(workspace=tmp_path)

    app = create_app(
        model_name="phi4-mini", applied_context=4096,
        router=router, client=primary,
        system_prompt="s", sessions_dir=tmp_path / "sessions",
        api_key="", rate_limit_requests=0,
    )
    tc = TestClient(app)
    resp = tc.get("/stream?message=oi")

    assert resp.status_code == 200
    assert "[Erro:" in resp.text


def test_stream_tool_loop_hard_stop(tmp_path: Path):
    """Mesma tool com os mesmos args e resultado 5x seguidas -> interrompe
    automaticamente (mesma protecao de _detect_loop que run_one_turn ja tem,
    agora tambem em /stream)."""
    from bauer.tool_router import ToolRouter
    mock_client = MagicMock()
    mock_client.chat_stream.side_effect = (
        lambda *a, **k: iter(['{"action": "calculate", "args": {"expression": "1+1"}}'])
    )
    router = ToolRouter(workspace=tmp_path)

    app = create_app(
        model_name="phi4-mini", applied_context=4096,
        router=router, client=mock_client,
        system_prompt="s", sessions_dir=tmp_path / "sessions",
        api_key="", rate_limit_requests=0,
    )
    tc = TestClient(app)
    resp = tc.get("/stream?message=calcule 1+1 varias vezes")

    assert resp.status_code == 200
    assert "Loop detectado" in resp.text
    assert "event: done" in resp.text
    # hard-stop em 5 repeticoes consecutivas — nao deve ter rodado ate MAX_TOOL_TURNS
    assert mock_client.chat_stream.call_count <= 6


def _sse_events(raw: str) -> list[tuple[str, str]]:
    """Decodifica SSE como o client web (desktop/src/api/client.ts): blocos
    separados por linha em branco, múltiplas linhas `data:` unidas com \\n."""
    events: list[tuple[str, str]] = []
    for block in raw.split("\n\n"):
        ev = "message"
        datas: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                datas.append(line[5:].removeprefix(" "))
        if datas:
            events.append((ev, "\n".join(datas)))
    return events


def _stream_text(raw: str) -> str:
    return "".join(d for ev, d in _sse_events(raw) if ev == "message")


def _has_tool_event(events: list[tuple[str, str]], tool_name: str) -> bool:
    """True se há um evento `tool` para `tool_name`. O payload virou JSON
    {name,label,icon} (narração de fase, S37); tolera o formato antigo (nome cru)."""
    import json as _json

    for ev, data in events:
        if ev != "tool":
            continue
        try:
            if _json.loads(data).get("name") == tool_name:
                return True
        except (ValueError, TypeError):
            if data == tool_name:
                return True
    return False


def test_stream_preserves_newlines_in_markdown(tmp_path: Path):
    """Regressão: chunks com \\n eram emitidos como `data: {chunk}\\n\\n` cru,
    corrompendo o frame SSE — o client descartava as quebras e o markdown
    chegava colapsado numa linha só."""
    from bauer.tool_router import ToolRouter

    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter(
        ["## Análise\n", "- item 1\n- item 2\n\n", "fim"]
    )
    router = ToolRouter(workspace=tmp_path)

    with patch("bauer.agent._try_parse_tool", return_value=None):
        app = create_app(
            model_name="phi4-mini", applied_context=4096,
            router=router, client=mock_client,
            system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        tc = TestClient(app)
        resp = tc.get("/stream?message=analise")

    assert resp.status_code == 200
    assert _stream_text(resp.text) == "## Análise\n- item 1\n- item 2\n\nfim"


def test_stream_does_not_leak_action_json_after_narration(tmp_path: Path):
    """Regressão: modelo que narra antes do JSON da action (`Vou verificar…
    ```json {"action": ...}```) vazava o JSON cru no chat, porque o streaming
    já tinha começado quando o bloco chegava."""
    from bauer.tool_router import ToolRouter

    turns = [
        iter(['Vou verificar a hora.\n', '```json\n', '{"action": "datetime_now"',
              ', "args": {}}\n', '```']),
        iter(["Agora sim: meio-dia."]),
    ]
    mock_client = MagicMock()
    mock_client.chat_stream.side_effect = lambda *a, **k: turns.pop(0)
    router = ToolRouter(workspace=tmp_path)

    app = create_app(
        model_name="phi4-mini", applied_context=4096,
        router=router, client=mock_client,
        system_prompt="s", sessions_dir=tmp_path / "sessions",
        api_key="", rate_limit_requests=0,
    )
    tc = TestClient(app)
    resp = tc.get("/stream?message=que horas sao")

    assert resp.status_code == 200
    events = _sse_events(resp.text)
    text = _stream_text(resp.text)
    assert "Vou verificar a hora." in text
    assert "Agora sim: meio-dia." in text
    assert '"action"' not in text          # JSON não vaza pro chat
    assert "```" not in text               # fence do bloco também não
    assert _has_tool_event(events, "datetime_now")


def test_stream_emits_selected_skill_event(tmp_path: Path):
    """A skill auto-selecionada é anunciada num evento SSE `skill` (paridade
    com a linha "↳ skill 'X' (NN%)" do CLI) para a UI mostrar que disparou."""
    import json as _json
    from bauer.skill_match import MatchedSkill
    from bauer.tool_router import ToolRouter

    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter(["Rodando análise."])
    router = ToolRouter(workspace=tmp_path)
    fake_skill = MatchedSkill(name="Docker Ops", score=0.85, content="guia", source="builtin")

    with patch("bauer.agent._try_parse_tool", return_value=None), \
         patch("bauer.skill_match.match_skill", return_value=fake_skill):
        app = create_app(
            model_name="phi4-mini", applied_context=4096,
            router=router, client=mock_client,
            system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        tc = TestClient(app)
        resp = tc.get("/stream?message=faça uma analise docker")

    assert resp.status_code == 200
    skill_events = [d for ev, d in _sse_events(resp.text) if ev == "skill"]
    assert skill_events, "esperava um evento SSE 'skill'"
    payload = _json.loads(skill_events[0])
    assert payload["name"] == "Docker Ops"
    assert payload["score"] == 0.85


def test_stream_strips_all_action_json_from_narration(tmp_path: Path):
    """Regressão: modelo que emite VÁRIOS tool calls numa resposta, intercalados
    com prosa, vazava o 2º/3º JSON no chat (só o 1º era removido). Agora todos
    os blocos de action são retirados do texto exibido."""
    from bauer.tool_router import ToolRouter

    resp1 = (
        "Vou fazer uma análise completa do Docker.\n\n"
        '{"action": "list_dir", "args": {"path": "."}}\n\n'
        "Vejo vários projetos mas nenhum Dockerfile. Vou verificar alguns.\n\n"
        '{"action": "list_dir", "args": {"path": "barbearia-site"}}\n'
        '{"action": "run_command", "args": {"command": "docker ps -a"}}'
    )
    turns = [iter([resp1]), iter(["Pronto: ambiente mapeado."])]
    mock_client = MagicMock()
    mock_client.chat_stream.side_effect = lambda *a, **k: turns.pop(0)
    router = ToolRouter(workspace=tmp_path)

    app = create_app(
        model_name="phi4-mini", applied_context=4096,
        router=router, client=mock_client,
        system_prompt="s", sessions_dir=tmp_path / "sessions",
        api_key="", rate_limit_requests=0,
    )
    tc = TestClient(app)
    resp = tc.get("/stream?message=analise docker")

    assert resp.status_code == 200
    text = _stream_text(resp.text)
    assert '"action"' not in text          # nenhum JSON de action vaza
    assert "Vou fazer uma análise completa do Docker." in text
    assert "Vejo vários projetos" in text
    assert "Pronto: ambiente mapeado." in text


def test_stream_releases_false_positive_braces(tmp_path: Path):
    """Chaves legítimas no texto (ex.: `{{.ServerVersion}}` de um comando
    docker) não podem ficar presas na gate anti-vazamento — o texto completo
    tem que chegar ao cliente."""
    from bauer.tool_router import ToolRouter

    reply = (
        "Use `docker info --format '{{.ServerVersion}}'` para ver a versão. "
        "Depois compare com o changelog para decidir o upgrade — sem pressa."
    )
    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter([reply[i:i + 7] for i in range(0, len(reply), 7)])
    router = ToolRouter(workspace=tmp_path)

    with patch("bauer.agent._try_parse_tool", return_value=None):
        app = create_app(
            model_name="phi4-mini", applied_context=4096,
            router=router, client=mock_client,
            system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        tc = TestClient(app)
        resp = tc.get("/stream?message=docker")

    assert resp.status_code == 200
    assert _stream_text(resp.text) == reply


def test_stream_runs_on_shared_agent_engine(tmp_path: Path):
    """/stream roda no MESMO motor do CLI (run_one_turn_with_fallback) —
    regressão da divergência de comportamento entre `bauer agent` e o serve
    (o /stream reimplementava um mini-loop próprio, só tool-bridge)."""
    from bauer.tool_router import ToolRouter

    def fake_engine(ctx, router, client, model, fallbacks, **kw):
        from bauer.delta_stream import emit_delta, emit_round_start, emit_tool
        emit_round_start()
        emit_delta("Analisando o Docker…")
        emit_tool("run_command")
        emit_round_start()
        emit_delta("## Relatório\n- tudo ok")
        return "## Relatório\n- tudo ok", [
            {"tool": "run_command", "args_sig": "x", "result": "ok"}
        ]

    router = ToolRouter(workspace=tmp_path)
    with patch("bauer.agent.run_one_turn_with_fallback", side_effect=fake_engine):
        app = create_app(
            model_name="phi4-mini", applied_context=4096,
            router=router, client=MagicMock(),
            system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        tc = TestClient(app)
        resp = tc.get("/stream?message=analise docker")

    assert resp.status_code == 200
    events = _sse_events(resp.text)
    text = _stream_text(resp.text)
    assert "Analisando o Docker…" in text
    assert "## Relatório\n- tudo ok" in text
    assert _has_tool_event(events, "run_command")


def test_stream_delivers_final_response_without_deltas(tmp_path: Path):
    """Native tool calling não emite deltas de texto — a resposta chega só no
    retorno do run_one_turn e o /stream tem que entregá-la mesmo assim."""
    from bauer.tool_router import ToolRouter

    def fake_engine(ctx, router, client, model, fallbacks, **kw):
        from bauer.delta_stream import emit_tool
        emit_tool("calculate")
        return "Resposta final sem streaming.", [
            {"tool": "calculate", "args_sig": "x", "result": "2"}
        ]

    router = ToolRouter(workspace=tmp_path)
    with patch("bauer.agent.run_one_turn_with_fallback", side_effect=fake_engine):
        app = create_app(
            model_name="phi4-mini", applied_context=4096,
            router=router, client=MagicMock(),
            system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        tc = TestClient(app)
        resp = tc.get("/stream?message=calcule")

    assert resp.status_code == 200
    events = _sse_events(resp.text)
    assert "Resposta final sem streaming." in _stream_text(resp.text)
    assert _has_tool_event(events, "calculate")


def test_stream_records_cost_tokens_and_budget(tmp_path: Path):
    """Regressão: o serve nunca registrava custo/tokens — a Observabilidade
    ('tokens hoje') e o budget do painel ('budget usado') ficavam em zero para
    sempre. Cada LLM call do turno vira linha no cost_history.jsonl, o total
    do turno entra no ledger do BudgetManager e no cost_estimate da run."""
    from bauer.tool_router import ToolRouter

    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter(["Olá! Tudo certo."])
    mock_client.last_usage = {"prompt_tokens": 100, "completion_tokens": 50}
    router = ToolRouter(workspace=tmp_path)
    cost_file = tmp_path / "cost_history.jsonl"

    with patch("bauer.agent._try_parse_tool", return_value=None), \
         patch("bauer.usage_pricing.estimate_cost_usd", return_value=0.0123), \
         patch("bauer.cost_tracker._DEFAULT_COST_FILE", cost_file):
        app = create_app(
            model_name="phi4-mini", applied_context=4096,
            router=router, client=mock_client,
            system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        tc = TestClient(app)
        resp = tc.get("/stream?message=oi")

    assert resp.status_code == 200

    # cost_history.jsonl (fonte da Observabilidade / tokens hoje)
    import json as _json
    lines = [_json.loads(ln) for ln in cost_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines, "esperava registro no cost_history.jsonl"
    assert lines[0]["total_tokens"] == 150
    assert lines[0]["cost_usd"] == 0.0123

    # Ledger de budget (fonte do 'budget usado' do painel)
    from bauer.core.runtime.autonomy import BudgetManager
    daily = BudgetManager(root=tmp_path / "runtime").status()["daily"]
    assert daily["used_usd"] == 0.0123

    # cost_estimate na run (coluna 'custo' da Observabilidade)
    from bauer.core.runtime.run_manager import RunManager
    runs = RunManager(root=tmp_path / "runtime").list_runs()
    assert runs and runs[-1].cost_estimate == 0.0123


def _payload_blob_after_stream(tmp_path, workspace, project_dir, message="cria um componente"):
    """Sobe /stream com um projeto ativo e devolve o texto de todas as mensagens
    de sistema enviadas ao modelo (para checar a injeção do hint de projeto)."""
    from unittest.mock import patch as _patch
    from bauer.tool_router import ToolRouter
    from bauer import projects_registry as pr

    reg = tmp_path / "projects.json"
    captured: dict = {}

    def _cap(model, payload):
        captured["payload"] = payload
        return iter(["ok"])

    mock_client = MagicMock()
    mock_client.chat_stream.side_effect = _cap
    router = ToolRouter(workspace=workspace)

    with _patch("bauer.projects_registry._DEFAULT_REGISTRY", reg), \
         _patch("bauer.agent._try_parse_tool", return_value=None):
        if project_dir is not None:
            pr.add_project(project_dir)  # registra e vira ativo (primeiro projeto)
        app = create_app(
            model_name="m", applied_context=4096, router=router, client=mock_client,
            system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        resp = TestClient(app).get(f"/stream?message={message}")

    assert resp.status_code == 200
    payload = captured.get("payload", [])
    return "\n".join(m.get("content", "") for m in payload if isinstance(m, dict))


def test_stream_no_hint_for_subfolder_project_once_phase1_routes_it(tmp_path: Path):
    """Antes da Fase 1 (router-por-projeto), um projeto ativo que é subpasta
    do workspace recebia o nudge de prompt <projeto-ativo> (Fase 0 sozinha).
    Com a Fase 1 no ar, esse MESMO cenário resolve para um ToolRouter PRÓPRIO
    do projeto — a sandbox real já confina a pasta, então o nudge fica
    redundante e a função corretamente NÃO o injeta mais (ver
    test_stream_uses_project_router_for_subfolder_project para a prova de que
    o isolamento real está de fato em vigor)."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "barbearia-site"
    proj.mkdir()

    blob = _payload_blob_after_stream(tmp_path, ws, proj)
    assert "<projeto-ativo>" not in blob


def test_stream_no_hint_for_project_outside_workspace(tmp_path: Path):
    """Projeto fora do workspace do serve também ganha router próprio (Fase 1
    não exige subpasta) — mesma razão do teste acima, o nudge fica redundante."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "fora"
    outside.mkdir()

    blob = _payload_blob_after_stream(tmp_path, ws, outside)
    assert "<projeto-ativo>" not in blob


# ─── Fase 1 — router-por-projeto (isolamento real) ───────────────────────────


def _stream_with_project_router(
    tmp_path: Path,
    ws: Path,
    *,
    active_project: "Path | None" = None,
    project_id_param: "str | None" = None,
    reg_path: "Path | None" = None,
    session_id: "str | None" = None,
    write_target: str = "arquivo.txt",
    message: str = "cria um arquivo",
):
    """Sobe /stream com um cache de router-por-projeto real, mandando o modelo
    escrever um arquivo via `write_file`. Devolve (resp, app, reg).

    O mock do LLM emite UM tool call (write_file) e depois texto final — o
    mesmo padrão das outras regressões deste arquivo."""
    from unittest.mock import patch as _patch
    from bauer.tool_router import ToolRouter
    from bauer import projects_registry as pr

    reg = reg_path or (tmp_path / "projects.json")
    turns = [
        iter([
            '{"action": "write_file", "args": {"path": "%s", '
            '"content": "conteudo teste"}}' % write_target
        ]),
        iter(["Pronto."]),
    ]
    mock_client = MagicMock()
    mock_client.chat_stream.side_effect = lambda *a, **k: turns.pop(0)
    router = ToolRouter(workspace=ws)

    with _patch("bauer.projects_registry._DEFAULT_REGISTRY", reg):
        if active_project is not None:
            pr.add_project(active_project)  # 1º projeto = ativo automaticamente
        app = create_app(
            model_name="m", applied_context=4096, router=router, client=mock_client,
            system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        qs = f"/stream?message={message}"
        if session_id:
            qs += f"&session_id={session_id}"
        if project_id_param:
            qs += f"&project_id={project_id_param}"
        resp = TestClient(app).get(qs)
    return resp, app, reg


def test_stream_uses_project_router_for_subfolder_project(tmp_path: Path):
    """Isolamento real: com um projeto ativo, o write_file do turno grava
    DENTRO da pasta do projeto — não na raiz do workspace do serve, e sem
    precisar de prefixo (a sandbox do turno JÁ é a pasta do projeto)."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "barbearia-site"
    proj.mkdir()

    resp, _app, _reg = _stream_with_project_router(tmp_path, ws, active_project=proj)

    assert resp.status_code == 200
    assert (proj / "arquivo.txt").read_text(encoding="utf-8") == "conteudo teste"
    assert not (ws / "arquivo.txt").exists()  # não vazou pro workspace do serve


def test_stream_uses_project_router_for_project_outside_workspace(tmp_path: Path):
    """Fase 1 não exige que o projeto seja subpasta do workspace do serve —
    ao contrário da Fase 0 (que dependia de caminho relativo dentro da
    sandbox), o router de projeto usa a pasta registrada como raiz própria."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outro-lugar" / "meu-projeto"
    outside.mkdir(parents=True)

    resp, _app, _reg = _stream_with_project_router(tmp_path, ws, active_project=outside)

    assert resp.status_code == 200
    assert (outside / "arquivo.txt").read_text(encoding="utf-8") == "conteudo teste"


def test_stream_explicit_project_id_overrides_active(tmp_path: Path):
    """project_id explícito no request vence o projeto ativo global."""
    from bauer import projects_registry as pr

    ws = tmp_path / "workspace"
    ws.mkdir()
    proj_a = ws / "projeto-a"
    proj_a.mkdir()
    proj_b = ws / "projeto-b"
    proj_b.mkdir()
    reg = tmp_path / "projects.json"

    from unittest.mock import patch as _patch
    with _patch("bauer.projects_registry._DEFAULT_REGISTRY", reg):
        pr.add_project(proj_a)  # vira ativo
        pid_b = pr.add_project(proj_b)["id"]

    resp, _app, _reg = _stream_with_project_router(
        tmp_path, ws, reg_path=reg, project_id_param=pid_b,
    )

    assert resp.status_code == 200
    assert (proj_b / "arquivo.txt").exists()
    assert not (proj_a / "arquivo.txt").exists()


def test_stream_session_sticky_project_survives_active_change(tmp_path: Path):
    """Uma vez fixado o projeto na sessão (1ª mensagem), trocar o ativo global
    NÃO troca o projeto da sessão em andamento — só uma sessão NOVA (ou um
    project_id explícito) usaria o novo ativo."""
    from unittest.mock import patch as _patch
    from bauer import projects_registry as pr

    ws = tmp_path / "workspace"
    ws.mkdir()
    proj_a = ws / "projeto-a"
    proj_a.mkdir()
    proj_b = ws / "projeto-b"
    proj_b.mkdir()
    reg = tmp_path / "projects.json"

    resp1, app, _reg = _stream_with_project_router(
        tmp_path, ws, active_project=proj_a, reg_path=reg,
        write_target="primeiro.txt",
    )
    assert resp1.status_code == 200
    sid = resp1.headers["X-Session-ID"]
    assert (proj_a / "primeiro.txt").exists()

    # Troca o projeto ativo global para B...
    with _patch("bauer.projects_registry._DEFAULT_REGISTRY", reg):
        pid_b = pr.add_project(proj_b)["id"]
        pr.set_active(pid_b)

    # ...mas a MESMA sessão continua presa ao projeto A. O mock do client já
    # esgotou seu roteiro de respostas (helper acima) — não importa: o que
    # este teste verifica é a RESOLUÇÃO do projeto (gravada na sessão antes
    # de qualquer chamada ao LLM), não o conteúdo da 2ª resposta.
    resp2 = TestClient(app).get(f"/stream?message=oi de novo&session_id={sid}")
    assert resp2.status_code == 200

    # A run/sessão registrou projeto A (sticky), não B.
    from bauer.core.runtime.session_manager import SessionManager
    sm = SessionManager(root=tmp_path / "runtime")
    session = sm.get_session(sid)
    assert session is not None
    assert session.state.get("project_id") == pr.project_id(proj_a)


def test_stream_invalid_explicit_project_id_falls_back_safely(tmp_path: Path):
    """project_id explícito mas inexistente/lixo não derruba o turno — cai no
    router default do serve silenciosamente."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    resp, _app, _reg = _stream_with_project_router(
        tmp_path, ws, project_id_param="lixo-nao-existe",
    )

    assert resp.status_code == 200
    assert (ws / "arquivo.txt").read_text(encoding="utf-8") == "conteudo teste"


def test_stream_run_input_tagged_with_project_id(tmp_path: Path):
    """A run fica taggeada com project_id quando um router de projeto é usado
    — runs continuam globais, só marcadas p/ filtrar na Observabilidade depois."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "meu-projeto"
    proj.mkdir()

    from bauer import projects_registry as pr
    resp, _app, reg = _stream_with_project_router(tmp_path, ws, active_project=proj)
    assert resp.status_code == 200

    run_id = resp.headers["X-Bauer-Run-ID"]
    from bauer.core.runtime.run_manager import RunManager
    run = RunManager(root=tmp_path / "runtime").get_run(run_id)
    assert run is not None

    from unittest.mock import patch as _patch
    with _patch("bauer.projects_registry._DEFAULT_REGISTRY", reg):
        expected_pid = pr.find_project_for_cwd(proj)
    assert run.input.get("project_id") == expected_pid


def test_stream_project_tool_events_reach_serve_event_bus(tmp_path: Path):
    """Regressão do wiring de eventos por-projeto: um turno que roda no router
    de PROJETO publica os eventos de tool no EventBus do SERVE (runtime_root),
    não num store próprio do router de projeto. Sem o override
    `_wire_router_to_serve`, a atividade de tool dos turnos por-projeto sumia da
    Observabilidade/`/audit` do serve — este teste trava essa regressão."""
    import time as _t
    from bauer.core.events import EventBus

    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "meu-projeto"
    proj.mkdir()

    resp, _app, _reg = _stream_with_project_router(tmp_path, ws, active_project=proj)
    assert resp.status_code == 200
    run_id = resp.headers["X-Bauer-Run-ID"]

    bus = EventBus(root=tmp_path / "runtime")  # o runtime_root do serve
    deadline = _t.monotonic() + 3.0
    tool_events: list = []
    while _t.monotonic() < deadline and not tool_events:
        dicts = [EventBus.to_dict(e) for e in bus.list_events(run_id=run_id)]
        tool_events = [d for d in dicts if str(d.get("event_type", "")).startswith("tool.call")]
        if not tool_events:
            _t.sleep(0.02)

    assert tool_events, "eventos tool.call.* do turno por-projeto deveriam estar no event_bus do serve"
    assert any(d.get("tool_name") == "write_file" for d in tool_events)


def test_stream_no_hint_when_no_active_project(tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    blob = _payload_blob_after_stream(tmp_path, ws, None)
    assert "<projeto-ativo>" not in blob


# ─── Memória por projeto: PROJECT.md (B) + prefetch de memória (A) ───────────


def test_stream_injects_project_brief_from_project_md(tmp_path: Path):
    """B: o PROJECT.md do projeto ativo entra no turno como brief/convenções."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "meu-projeto"
    proj.mkdir()
    (proj / "PROJECT.md").write_text(
        "# Projeto: Meu Projeto\n\n## Convenções\n\n"
        "- commits em PT-BR\n- TypeScript strict mode\n",
        encoding="utf-8",
    )
    blob = _payload_blob_after_stream(tmp_path, ws, proj)
    assert "<projeto-brief>" in blob
    assert "commits em PT-BR" in blob


def test_stream_skips_placeholder_project_md(tmp_path: Path):
    """O PROJECT.md boilerplate do `bauer project init` (descrição vazia) não
    é injetado — seria só ruído."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "meu-projeto"
    proj.mkdir()
    (proj / "PROJECT.md").write_text(
        "# Projeto: Meu Projeto\n\ncriado: 2026-07-09\n\n"
        "## Descricao\n\nSem descricao.\n\n---\n",
        encoding="utf-8",
    )
    blob = _payload_blob_after_stream(tmp_path, ws, proj)
    assert "<projeto-brief>" not in blob


def test_stream_truncates_large_project_md_with_readfile_pointer(tmp_path: Path):
    """PROJECT.md acima do teto entra truncado + ponteiro pro read_file."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "meu-projeto"
    proj.mkdir()
    big = "# Projeto\n\n" + ("linha de contexto do projeto. " * 200)  # >> 1500 chars
    (proj / "PROJECT.md").write_text(big, encoding="utf-8")

    blob = _payload_blob_after_stream(tmp_path, ws, proj)
    assert "<projeto-brief>" in blob
    assert "read_file" in blob and "PROJECT.md" in blob  # aponta pro restante
    # não despejou o arquivo inteiro
    assert blob.count("linha de contexto do projeto.") < 200


def test_stream_prefetches_project_memory_scoped_to_project(tmp_path: Path):
    """A: o serve chama prefetch_memory_context com a pasta do PROJETO (não a
    raiz do serve) e injeta o bloco <memory-context> retornado."""
    from unittest.mock import patch as _patch

    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "meu-projeto"
    proj.mkdir()

    seen: dict = {}

    def _fake_prefetch(user_input, workspace=None, **kw):
        seen["workspace"] = workspace
        seen["user_input"] = user_input
        return "<memory-context>\n[recordado]\n• decisão passada: usar Postgres\n</memory-context>"

    with _patch("bauer.memory_context.prefetch_memory_context", side_effect=_fake_prefetch):
        blob = _payload_blob_after_stream(tmp_path, ws, proj, message="continua o trabalho")

    assert "<memory-context>" in blob
    assert "usar Postgres" in blob
    assert str(proj) in str(seen.get("workspace"))  # escopo = pasta do projeto
    assert seen.get("user_input") == "continua o trabalho"


def test_stream_memory_prefetch_can_be_disabled(tmp_path: Path):
    """BAUER_SERVE_MEMORY_PREFETCH=0 pula o prefetch (síncrono no request) —
    escape hatch de time-to-first-token; nem chama prefetch_memory_context."""
    from unittest.mock import patch as _patch

    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "meu-projeto"
    proj.mkdir()

    called = {"n": 0}

    def _fake_prefetch(*a, **k):
        called["n"] += 1
        return "<memory-context>\nnão deveria aparecer\n</memory-context>"

    with _patch("bauer.server._MEMORY_PREFETCH_ENABLED", False), \
         _patch("bauer.memory_context.prefetch_memory_context", side_effect=_fake_prefetch):
        blob = _payload_blob_after_stream(tmp_path, ws, proj)

    assert "<memory-context>" not in blob
    assert called["n"] == 0


def test_kanban_endpoint_reads_active_project_board(tmp_path: Path):
    """Fase 1 end-to-end: /api/kanban do serve resolve o projeto ativo e lê o
    board DELE (não o TASKS.md do workspace raiz). Fecha o gap em que tarefas
    criadas por projeto sumiam do painel."""
    from unittest.mock import patch as _patch
    from bauer.tool_router import ToolRouter
    from bauer.workspace_manager_factory import get_workspace_manager
    from bauer import projects_registry as pr

    ws = tmp_path / "workspace"
    ws.mkdir()
    proj = ws / "bauerinvest"
    proj.mkdir()
    # Tarefa no board do PROJETO; e uma diferente no board da RAIZ do serve.
    get_workspace_manager(proj).add_task("tarefa do projeto bauerinvest")
    get_workspace_manager(ws).add_task("tarefa da raiz do serve")

    reg = tmp_path / "projects.json"
    with _patch("bauer.projects_registry._DEFAULT_REGISTRY", reg):
        pr.add_project(proj)  # vira ativo
        app = create_app(
            model_name="m", applied_context=4096, router=ToolRouter(workspace=ws),
            client=MagicMock(), system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        data = TestClient(app).get("/api/kanban").json()

    titles = [c["title"] for col in data["columns"].values() for c in col]
    assert "tarefa do projeto bauerinvest" in titles  # mostra o board do projeto
    assert "tarefa da raiz do serve" not in titles     # não o board da raiz


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
