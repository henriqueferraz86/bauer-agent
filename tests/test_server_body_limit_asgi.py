"""WEB-10 (auditoria 2026-10-12): limite de corpo aplicado ANTES do parser.

Antes, ``/transcribe`` só checava o tamanho dentro do endpoint, depois de o
parser multipart do Starlette ter recebido e "spooled" o upload inteiro. Um
JSON gigante em ``/chat``/``/v1`` era materializado inteiro antes do 422 do
Pydantic. Sem ``Content-Length`` (chunked), nada cortava.

As provas usam um ``receive`` ASGI que CONTA quantos chunks o app consumiu,
com limites pequenos via monkeypatch, sem volume real e sem rede.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi", reason="FastAPI nao instalado")
from fastapi.testclient import TestClient  # noqa: E402

import bauer.server_body_limit as body_limit  # noqa: E402
from bauer.server import create_app  # noqa: E402
from bauer.server_body_limit import BodySizeLimitMiddleware, limite_para  # noqa: E402

CHUNK = 1024


@pytest.fixture
def limites_pequenos(monkeypatch):
    import bauer.transcription as transcription

    monkeypatch.setattr(transcription, "MAX_AUDIO_BYTES", 4 * CHUNK)
    monkeypatch.setattr(body_limit, "MULTIPART_OVERHEAD_BYTES", CHUNK)
    monkeypatch.setattr(body_limit, "DEFAULT_MAX_BODY_BYTES", 8 * CHUNK)
    monkeypatch.setattr(body_limit, "OPENAI_MAX_BODY_BYTES", 16 * CHUNK)


@pytest.fixture
def app(tmp_path: Path):
    from bauer.tool_router import ToolRouter

    model_client = MagicMock()
    model_client.chat_stream.return_value = iter(["ok"])
    with patch("bauer.agent.run_one_turn", return_value=("ok", [])) as turn, \
         patch("bauer.agent.run_one_turn_with_fallback", return_value=("ok", [])):
        application = create_app(
            model_name="test-model", applied_context=4096,
            router=ToolRouter(workspace=tmp_path), client=model_client,
            system_prompt="test", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        application.state.turn_mock = turn
        yield application


def _scope(path: str, content_type: str, *, method: str = "POST",
           content_length: int | str | None = None) -> dict:
    headers = [(b"content-type", content_type.encode()), (b"host", b"testserver")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    return {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
        "query_string": b"", "root_path": "", "headers": headers,
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
    }


def _chamar(app, scope: dict, chunks: list[bytes]) -> tuple[int, list[int], bytes]:
    """Roda o app com um receive que conta os chunks entregues."""
    consumidos = [0]
    fila = iter(chunks)
    status: list[int] = []
    corpo = bytearray()

    async def receive():
        try:
            body = next(fila)
        except StopIteration:
            return {"type": "http.disconnect"}
        consumidos[0] += 1
        return {"type": "http.request", "body": body,
                "more_body": consumidos[0] < len(chunks)}

    async def send(message):
        if message["type"] == "http.response.start":
            status.append(message["status"])
        elif message["type"] == "http.response.body":
            corpo.extend(message.get("body") or b"")

    asyncio.run(app(scope, receive, send))
    return consumidos[0], status, bytes(corpo)


def _multipart(n_chunks: int) -> tuple[str, list[bytes]]:
    boundary = "BAUERWEB10"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            "filename=\"a.webm\"\r\nContent-Type: audio/webm\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return (f"multipart/form-data; boundary={boundary}",
            [head, *([b"x" * CHUNK] * n_chunks), tail])


# ── multipart (/transcribe) ────────────────────────────────────────────────


def test_multipart_chunked_e_cortado_antes_do_parser(app, limites_pequenos, monkeypatch):
    chamadas: list[object] = []
    monkeypatch.setattr("bauer.transcription.transcribe_audio",
                        lambda path: chamadas.append(path) or {"success": True})
    content_type, chunks = _multipart(200)

    consumidos, status, corpo = _chamar(app, _scope("/transcribe", content_type), chunks)

    assert status == [413]
    # limite = 4 KiB de áudio + 1 KiB de envelope: o app não vê o resto
    assert consumidos <= 7, f"o parser consumiu {consumidos} de {len(chunks)} chunks"
    assert chamadas == []
    assert "excede o limite" in json.loads(corpo)["detail"]


def test_multipart_com_content_length_grande_nao_le_nenhum_byte(app, limites_pequenos):
    content_type, chunks = _multipart(200)
    total = sum(len(c) for c in chunks)

    consumidos, status, _ = _chamar(
        app, _scope("/transcribe", content_type, content_length=total), chunks)

    assert status == [413]
    assert consumidos == 0


def test_content_length_mentiroso_nao_passa_do_limite(app, limites_pequenos):
    content_type, chunks = _multipart(200)

    consumidos, status, _ = _chamar(
        app, _scope("/transcribe", content_type, content_length=10), chunks)

    assert status == [413]
    assert consumidos <= 7


# ── JSON (/chat, /v1) ──────────────────────────────────────────────────────


def test_json_chunked_e_cortado_antes_do_parser(app, limites_pequenos):
    chunks = [b'{"message": "', *([b"y" * CHUNK] * 300), b'"}']

    consumidos, status, _ = _chamar(app, _scope("/chat", "application/json"), chunks)

    assert status == [413]
    assert consumidos <= 10, f"o parser consumiu {consumidos} de {len(chunks)} chunks"
    app.state.turn_mock.assert_not_called()


def test_v1_tem_limite_proprio_maior_que_o_padrao(app, limites_pequenos):
    messages = [{"role": "user", "content": "z" * (10 * CHUNK)}]
    corpo = json.dumps({"messages": messages}).encode()
    chunks = [corpo[i:i + CHUNK] for i in range(0, len(corpo), CHUNK)]
    assert 8 * CHUNK < len(corpo) < 16 * CHUNK

    _, status_v1, _ = _chamar(app, _scope("/v1/chat/completions", "application/json"), chunks)
    _, status_chat, _ = _chamar(app, _scope("/chat", "application/json"), chunks)

    assert status_v1 != [413]
    assert status_chat == [413]


def test_corpo_dentro_do_limite_continua_funcionando(app, limites_pequenos):
    corpo = json.dumps({"message": "olá"}).encode()

    consumidos, status, _ = _chamar(app, _scope("/chat", "application/json"), [corpo])

    assert status == [200]
    assert consumidos == 1


def test_content_length_invalido_e_400(app, limites_pequenos):
    _, status, _ = _chamar(
        app, _scope("/chat", "application/json", content_length="12abc"), [b"{}"])
    assert status == [400]


def test_testclient_real_recebe_413_por_content_length(app, limites_pequenos):
    response = TestClient(app).post("/chat", content=b"x" * (9 * CHUNK),
                                    headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_get_nao_e_afetado(app, limites_pequenos):
    assert TestClient(app).get("/health").status_code == 200


# ── middleware isolado ─────────────────────────────────────────────────────


def test_app_que_engole_a_excecao_ainda_responde_413():
    """Um endpoint com try/except genérico em volta da leitura não pode
    transformar o corte em 200/500: o flag decide, não a exceção."""
    async def app_teimoso(scope, receive, send):
        try:
            while True:
                message = await receive()
                if not message.get("more_body"):
                    break
        except Exception:  # noqa: BLE001 — é exatamente o caso testado
            pass
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = BodySizeLimitMiddleware(app_teimoso, limite_fn=lambda path: 10)
    consumidos, status, _ = _chamar(mw, _scope("/x", "text/plain"), [b"a" * 6] * 50)

    assert status == [413]
    assert consumidos == 2


def test_limite_por_rota():
    import bauer.transcription as transcription

    assert limite_para("/transcribe") == (transcription.MAX_AUDIO_BYTES
                                          + body_limit.MULTIPART_OVERHEAD_BYTES)
    assert limite_para("/v1/chat/completions") == body_limit.OPENAI_MAX_BODY_BYTES
    assert limite_para("/chat") == body_limit.DEFAULT_MAX_BODY_BYTES
    assert limite_para("/v1x") == body_limit.DEFAULT_MAX_BODY_BYTES
    # o teto do /v1 acompanha o próprio schema: 200 mensagens x 200 mil chars
    assert body_limit.OPENAI_MAX_BODY_BYTES > 200 * 200_000
    # e o padrão cabe com folga o maior /chat válido (100 mil chars escapados)
    assert body_limit.DEFAULT_MAX_BODY_BYTES > 6 * 100_000
