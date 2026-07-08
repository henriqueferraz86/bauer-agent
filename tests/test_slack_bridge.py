"""Testes do SlackBridge — lógica pura (filtros, envelope, chunking), sem WS real."""

from __future__ import annotations

import pytest

from bauer.channel_base import AgentBackend, ChannelMessage, chunk_text
from bauer.slack_bridge import (
    MAX_MESSAGE_CHARS,
    SlackBridge,
    should_respond,
    strip_bot_mention,
)

BOT_ID = "U0BOT999"


def _dm_event(text="oi", user="U42", channel="D1") -> dict:
    return {"type": "message", "text": text, "user": user, "channel": channel, "channel_type": "im"}


def _channel_msg_event(text="oi", user="U42", channel="C1") -> dict:
    return {"type": "message", "text": text, "user": user, "channel": channel, "channel_type": "channel"}


def _app_mention_event(text=f"<@{BOT_ID}> oi", user="U42", channel="C1") -> dict:
    return {"type": "app_mention", "text": text, "user": user, "channel": channel, "channel_type": "channel"}


class TestFiltrosDeEvento:
    def test_dm_sempre_responde(self):
        assert should_respond(_dm_event(), BOT_ID) is True

    def test_ignora_bots(self):
        data = _dm_event()
        data["bot_id"] = "B123"
        assert should_respond(data, BOT_ID) is False

    def test_ignora_a_si_mesmo(self):
        assert should_respond(_dm_event(user=BOT_ID), BOT_ID) is False

    def test_ignora_subtype_edicao_ou_join(self):
        data = _dm_event()
        data["subtype"] = "message_changed"
        assert should_respond(data, BOT_ID) is False

    def test_canal_sem_mencao_nao_responde_por_padrao(self):
        assert should_respond(_channel_msg_event(), BOT_ID, mention_only=True) is False

    def test_canal_com_mention_only_false_responde(self):
        assert should_respond(_channel_msg_event(), BOT_ID, mention_only=False) is True

    def test_app_mention_sempre_responde(self):
        assert should_respond(_app_mention_event(), BOT_ID, mention_only=True) is True

    def test_allowed_channels_filtra(self):
        data = _app_mention_event(channel="proibido")
        assert should_respond(data, BOT_ID, allowed_channels={"C1"}) is False
        data2 = _app_mention_event(channel="C1")
        assert should_respond(data2, BOT_ID, allowed_channels={"C1"}) is True


class TestMencao:
    def test_strip_remove_mencao(self):
        assert strip_bot_mention(f"<@{BOT_ID}> qual a previsão?", BOT_ID) == "qual a previsão?"

    def test_strip_sem_mencao_intacto(self):
        assert strip_bot_mention("texto normal", BOT_ID) == "texto normal"


class TestChunking:
    def test_limite_slack(self):
        chunks = chunk_text("y" * 7000, MAX_MESSAGE_CHARS)
        assert all(len(c) <= MAX_MESSAGE_CHARS for c in chunks)
        assert sum(len(c) for c in chunks) == 7000


class TestBridgeConstrucao:
    def test_start_sem_token_falha_claro(self):
        bridge = SlackBridge(bot_token="", app_token="", backend=AgentBackend())
        with pytest.raises(RuntimeError, match="SLACK_BOT_TOKEN"):
            bridge.start()

    def test_is_authorized_allowlist(self):
        bridge = SlackBridge(bot_token="x", app_token="y", backend=AgentBackend(), allowed_users=["42"])
        ok = ChannelMessage(channel="slack", user_id="42", chat_id="c", text="t")
        nope = ChannelMessage(channel="slack", user_id="666", chat_id="c", text="t")
        assert bridge._is_authorized(ok) is True
        assert bridge._is_authorized(nope) is False

    def test_allow_all(self):
        bridge = SlackBridge(bot_token="x", app_token="y", backend=AgentBackend(), allow_all=True)
        anyone = ChannelMessage(channel="slack", user_id="1", chat_id="c", text="t")
        assert bridge._is_authorized(anyone) is True

    def test_build_from_config_env_first(self, monkeypatch):
        from bauer.config_loader import BauerConfig
        from bauer.slack_bridge import build_bridge_from_config

        monkeypatch.setenv("SLACK_BOT_TOKEN", "env-bot-token")
        monkeypatch.setenv("SLACK_APP_TOKEN", "env-app-token")
        cfg = BauerConfig(**{
            "model": {"provider": "ollama", "name": "m",
                      "requested_context": 4096, "minimum_context": 2048},
            "slack": {"enabled": True, "bot_token": "cfg-token",
                      "allowed_users": ["1"], "mention_only": False},
        })
        bridge = build_bridge_from_config(cfg)
        assert bridge.bot_token == "env-bot-token"
        assert bridge.app_token == "env-app-token"
        assert bridge.mention_only is False


class TestOnEventsApi:
    """Roteamento de evento events_api → backend, com backend fake."""

    @pytest.mark.asyncio
    async def test_message_dm_responde(self, monkeypatch):
        received = []
        sent = []

        class FakeBackend(AgentBackend):
            @property
            def is_ready(self):
                return True

            def process(self, msg, **kwargs):
                received.append(msg)
                return "resposta do agent"

        bridge = SlackBridge(bot_token="x", app_token="y", backend=FakeBackend(), allowed_users=["42"])
        bridge.bot_user_id = BOT_ID
        monkeypatch.setattr(bridge, "send_text", lambda cid, t: sent.append((cid, t)))

        await bridge._on_events_api({
            "type": "events_api",
            "envelope_id": "env-1",
            "payload": {"event": _dm_event(text="qual o status?", user="42", channel="D1")},
        }, "env-1")
        assert received and received[0].text == "qual o status?"
        assert sent == [("D1", "resposta do agent")]

    @pytest.mark.asyncio
    async def test_envelope_duplicado_processa_uma_vez(self, monkeypatch):
        sent = []

        class FakeBackend(AgentBackend):
            @property
            def is_ready(self):
                return True

            def process(self, msg, **kwargs):
                return "resposta"

        bridge = SlackBridge(bot_token="x", app_token="y", backend=FakeBackend(), allow_all=True)
        bridge.bot_user_id = BOT_ID
        monkeypatch.setattr(bridge, "send_text", lambda cid, t: sent.append(t))

        envelope = {
            "type": "events_api",
            "envelope_id": "dup-1",
            "payload": {"event": _dm_event()},
        }
        await bridge._on_events_api(envelope, "dup-1")
        await bridge._on_events_api(envelope, "dup-1")
        assert len(sent) == 1

    @pytest.mark.asyncio
    async def test_mensagem_de_bot_ignorada(self, monkeypatch):
        sent = []
        bridge = SlackBridge(bot_token="x", app_token="y", backend=AgentBackend(), allow_all=True)
        bridge.bot_user_id = BOT_ID
        monkeypatch.setattr(bridge, "send_text", lambda cid, t: sent.append(t))
        data = _dm_event()
        data["bot_id"] = "B999"
        await bridge._on_events_api({
            "type": "events_api", "envelope_id": "e2", "payload": {"event": data},
        }, "e2")
        assert sent == []
