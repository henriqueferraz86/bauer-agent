"""Testes do TelegramBridge — httpx.MockTransport, sem rede."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.telegram_bridge import MAX_MESSAGE_CHARS, TelegramBridge


class _EchoBackend(AgentBackend):
    """Backend fake — responde sem tocar em LLM/config."""

    def __init__(self):
        super().__init__()
        self.received: list[ChannelMessage] = []

    @property
    def is_ready(self):
        return True

    def process(self, msg: ChannelMessage) -> str:
        self.received.append(msg)
        return f"resposta para: {msg.text}"


def _make_bridge(tmp_path: Path, transport_handler, **kw) -> TelegramBridge:
    bridge = TelegramBridge(
        token="123:FAKE",
        backend=_EchoBackend(),
        allowed_users=kw.pop("allowed_users", [42]),
        state_dir=tmp_path / "state",
        **kw,
    )
    bridge._http = httpx.Client(transport=httpx.MockTransport(transport_handler))
    return bridge


def _update(update_id: int, user_id: int, chat_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "chat": {"id": chat_id},
            "from": {"id": user_id, "username": "tester"},
        },
    }


class TestApiETokens:
    def test_get_me_ok(self, tmp_path):
        def handler(request):
            assert "/bot123:FAKE/getMe" in str(request.url)
            return httpx.Response(200, json={"ok": True, "result": {"username": "bauer_bot"}})

        bridge = _make_bridge(tmp_path, handler)
        assert bridge.get_me()["username"] == "bauer_bot"

    def test_api_erro_telegram_levanta(self, tmp_path):
        def handler(request):
            return httpx.Response(200, json={"ok": False, "description": "Unauthorized"})

        bridge = _make_bridge(tmp_path, handler)
        with pytest.raises(RuntimeError, match="Unauthorized"):
            bridge.get_me()

    def test_start_sem_token_falha_claro(self, tmp_path):
        bridge = TelegramBridge(token="", backend=_EchoBackend(), state_dir=tmp_path)
        with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
            bridge.start()


class TestSendText:
    def test_chunking_4096(self, tmp_path):
        sent: list[str] = []

        def handler(request):
            if "sendMessage" in str(request.url):
                sent.append(json.loads(request.content)["text"])
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        bridge.send_text("42", "x" * 9000)
        assert len(sent) == 3
        assert all(len(s) <= MAX_MESSAGE_CHARS for s in sent)

    def test_falha_de_envio_nao_propaga(self, tmp_path):
        def handler(request):
            return httpx.Response(500)

        bridge = _make_bridge(tmp_path, handler)
        bridge.send_text("42", "oi")  # não levanta
        assert "sendMessage" in bridge.last_error


class TestHandleUpdate:
    def test_autorizado_recebe_resposta(self, tmp_path):
        sent: list[dict] = []

        def handler(request):
            if "sendMessage" in str(request.url):
                sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        bridge._handle_update(_update(1, user_id=42, chat_id=42, text="oi bauer"))
        assert any("resposta para: oi bauer" in m["text"] for m in sent)

    def test_nao_autorizado_sem_resposta(self, tmp_path):
        sent: list[dict] = []

        def handler(request):
            if "sendMessage" in str(request.url):
                sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True, "result": {}})

        bridge = _make_bridge(tmp_path, handler)
        bridge._handle_update(_update(1, user_id=666, chat_id=666, text="hackear"))
        assert sent == []
        assert bridge.msgs_dropped == 1

    def test_allow_all_libera(self, tmp_path):
        bridge = _make_bridge(
            tmp_path, lambda r: httpx.Response(200, json={"ok": True, "result": {}}),
            allowed_users=[], allow_all=True,
        )
        msg = ChannelMessage(channel="telegram", user_id="777", chat_id="777", text="oi")
        assert bridge._is_authorized(msg) is True

    def test_update_sem_texto_ignorado(self, tmp_path):
        bridge = _make_bridge(tmp_path, lambda r: httpx.Response(200, json={"ok": True, "result": {}}))
        bridge._handle_update({"update_id": 5, "message": {"chat": {"id": 1}, "from": {"id": 42}}})
        assert bridge.backend.received == []


class TestOffsetPersistence:
    def test_offset_sobrevive_restart(self, tmp_path):
        handler = lambda r: httpx.Response(200, json={"ok": True, "result": {}})
        bridge = _make_bridge(tmp_path, handler)
        bridge._offset = 12345
        bridge._save_offset()

        bridge2 = _make_bridge(tmp_path, handler)
        assert bridge2._offset == 12345

    def test_offset_corrompido_volta_a_zero(self, tmp_path):
        state = tmp_path / "state"
        state.mkdir(parents=True)
        (state / "telegram_offset.json").write_text("{lixo", encoding="utf-8")
        bridge = _make_bridge(tmp_path, lambda r: httpx.Response(200, json={"ok": True, "result": {}}))
        assert bridge._offset == 0


class TestBuildFromConfig:
    def test_monta_do_bauer_config(self, tmp_path, monkeypatch):
        from bauer.config_loader import BauerConfig
        from bauer.telegram_bridge import build_bridge_from_config

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        cfg = BauerConfig(**{
            "model": {"provider": "ollama", "name": "m",
                      "requested_context": 4096, "minimum_context": 2048},
            "agent": {"workspace": str(tmp_path / "ws")},
            "telegram": {"enabled": True, "allowed_users": [1, 2],
                         "bot_token": "config-token"},
        })
        bridge = build_bridge_from_config(cfg)
        assert bridge.token == "env-token"  # env vence
        assert bridge.allowed_users == {1, 2}
