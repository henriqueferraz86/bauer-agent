"""Testes LISO no AgentBackend (fila busy) e tools send_message/transcribe."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.tool_router import ToolError, ToolRouter


def _msg(text: str, chat: str = "42") -> ChannelMessage:
    return ChannelMessage(
        channel="telegram", user_id=chat, chat_id=chat, text=text,
    )


class _SlowClient:
    """Client fake: segura o turno até o evento liberar."""

    host = "http://localhost:11434"

    def __init__(self):
        self.release = threading.Event()
        self.calls: list[str] = []

    def chat_stream(self, model, messages):
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        self.calls.append(last_user)
        self.release.wait(timeout=5.0)
        yield f"eco: {last_user[:40]}"


def _make_backend(tmp_path: Path, client=None) -> AgentBackend:
    backend = AgentBackend(
        config_path=tmp_path / "config.yaml",
        sessions_dir=tmp_path / "sessions",
    )
    backend._client = client or _SlowClient()
    backend._model_name = "fake-model"
    backend._provider = "ollama"
    backend._applied_context = 4096

    from bauer.tool_router import ToolRouter as _TR
    backend._router = _TR(workspace=tmp_path / "ws")

    class _NullStore:
        def load(self, key):
            return []

        def save(self, key, messages):
            pass

        def delete(self, key):
            pass

    backend._store = _NullStore()
    backend._config_mtime = -1.0
    backend._maybe_reload = lambda: None  # sem config.yaml real
    return backend


class TestBusyQueue:
    def test_mensagem_durante_turno_entra_na_fila(self, tmp_path):
        client = _SlowClient()
        backend = _make_backend(tmp_path, client)
        delivered: list[str] = []
        results: dict[str, str] = {}

        def first_turn():
            results["first"] = backend.process(
                _msg("primeira"), send_fn=delivered.append
            )

        t = threading.Thread(target=first_turn)
        t.start()
        # espera o turno 1 segurar o lock
        deadline = time.monotonic() + 3.0
        while not client.calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert client.calls, "primeiro turno não começou"

        # segunda mensagem com a sessão ocupada → feedback imediato
        resp2 = backend.process(_msg("segunda"))
        assert "fila" in resp2.lower()

        client.release.set()
        t.join(timeout=5.0)
        assert "eco: primeira" in results["first"]
        # a segunda foi drenada pela thread ativa e entregue via send_fn
        assert any("eco: segunda" in d for d in delivered)

    def test_fila_cheia_recusa(self, tmp_path):
        client = _SlowClient()
        backend = _make_backend(tmp_path, client)

        t = threading.Thread(
            target=lambda: backend.process(_msg("primeira"), send_fn=lambda s: None)
        )
        t.start()
        deadline = time.monotonic() + 3.0
        while not client.calls and time.monotonic() < deadline:
            time.sleep(0.01)

        from bauer.channel_base import _MAX_PENDING_PER_SESSION
        for i in range(_MAX_PENDING_PER_SESSION):
            resp = backend.process(_msg(f"extra {i}"))
            assert "fila" in resp.lower()
        resp = backend.process(_msg("estourou"))
        assert "cheia" in resp.lower()

        client.release.set()
        t.join(timeout=10.0)

    def test_clear_session_limpa_fila(self, tmp_path):
        backend = _make_backend(tmp_path)
        backend._pending["tg:42"] = __import__("collections").deque(["pendente"])
        backend._clear_session("tg:42")
        assert "tg:42" not in backend._pending


class TestSendMessageTool:
    @pytest.fixture()
    def router(self, tmp_path):
        return ToolRouter(workspace=tmp_path / "ws")

    def test_sem_canal_da_erro(self, router):
        with pytest.raises(ToolError, match="channel"):
            router.execute({"action": "send_message", "args": {"chat_id": "1", "text": "x"}})

    def test_sem_chat_id_da_erro(self, router):
        with pytest.raises(ToolError, match="chat_id"):
            router.execute({"action": "send_message",
                            "args": {"channel": "telegram", "text": "x"}})

    def test_bridge_vivo_entrega_direto(self, router):
        from bauer import live_bridges

        sent: list[tuple[str, str]] = []

        class _FakeBridge:
            def send_text(self, chat_id, text):
                sent.append((chat_id, text))

            def send_media(self, chat_id, path):
                sent.append((chat_id, f"media:{path}"))
                return True

        live_bridges.clear()
        live_bridges.register("telegram", _FakeBridge())
        try:
            out = router.execute({
                "action": "send_message",
                "args": {"channel": "telegram", "chat_id": "99", "text": "olá!"},
            })
            assert "entregue" in out
            assert sent == [("99", "olá!")]
        finally:
            live_bridges.clear()

    def test_bridge_vivo_com_media(self, router, tmp_path):
        from bauer import live_bridges

        sent: list[tuple[str, str]] = []

        class _FakeBridge:
            def send_text(self, chat_id, text):
                sent.append((chat_id, text))

            def send_media(self, chat_id, path):
                sent.append((chat_id, f"media:{path}"))
                return True

        img = tmp_path / "x.png"
        img.write_bytes(b"PNG")
        live_bridges.clear()
        live_bridges.register("telegram", _FakeBridge())
        try:
            out = router.execute({
                "action": "send_message",
                "args": {"channel": "telegram", "chat_id": "99",
                         "text": "veja", "media_path": str(img)},
            })
            assert "texto + mídia" in out
            assert len(sent) == 2
        finally:
            live_bridges.clear()

    def test_sem_gateway_enfileira_outbox(self, router, tmp_path):
        from bauer import live_bridges
        from bauer.gateway_outbox import GatewayOutbox

        live_bridges.clear()
        out = router.execute({
            "action": "send_message",
            "args": {"channel": "telegram", "chat_id": "55", "text": "depois"},
        })
        assert "enfileirada" in out
        pending = GatewayOutbox(tmp_path / "ws").pending(limit=10)
        assert any(m.target == "55" for m in pending)


class TestTranscribeAudioTool:
    def test_path_obrigatorio(self, tmp_path):
        router = ToolRouter(workspace=tmp_path / "ws")
        with pytest.raises(ToolError, match="path"):
            router.execute({"action": "transcribe_audio", "args": {}})

    def test_transcricao_ok(self, tmp_path, monkeypatch):
        router = ToolRouter(workspace=tmp_path / "ws")
        monkeypatch.setattr(
            "bauer.transcription.transcribe_audio",
            lambda p: {"success": True, "transcript": "texto do áudio",
                       "provider": "groq"},
        )
        out = router.execute({
            "action": "transcribe_audio", "args": {"path": str(tmp_path / "a.ogg")},
        })
        assert "texto do áudio" in out
        assert "groq" in out

    def test_falha_vira_tool_error(self, tmp_path, monkeypatch):
        router = ToolRouter(workspace=tmp_path / "ws")
        monkeypatch.setattr(
            "bauer.transcription.transcribe_audio",
            lambda p: {"success": False, "transcript": "", "error": "sem key"},
        )
        with pytest.raises(ToolError, match="sem key"):
            router.execute({
                "action": "transcribe_audio",
                "args": {"path": str(tmp_path / "a.ogg")},
            })


class TestLiveBridges:
    def test_register_get_unregister(self):
        from bauer import live_bridges

        live_bridges.clear()
        obj = object()
        live_bridges.register("x", obj)
        assert live_bridges.get("x") is obj
        assert live_bridges.names() == ["x"]
        live_bridges.unregister("x")
        assert live_bridges.get("x") is None
        live_bridges.clear()
