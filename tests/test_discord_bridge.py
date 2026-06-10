"""Testes do DiscordBridge — lógica pura (filtros, intents, chunking), sem WS real."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.channel_base import AgentBackend, ChannelMessage, chunk_text
from bauer.discord_bridge import (
    MAX_MESSAGE_CHARS,
    DiscordBridge,
    compute_intents,
    is_dm,
    mentions_bot,
    should_respond,
    strip_bot_mention,
)

BOT_ID = "999000999"


def _guild_msg(content="oi", author_id="42", bot=False, mentions=None,
               guild="g1", channel="c1") -> dict:
    return {
        "content": content,
        "author": {"id": author_id, "bot": bot, "username": "tester"},
        "guild_id": guild,
        "channel_id": channel,
        "mentions": mentions or [],
    }


def _dm_msg(content="oi", author_id="42") -> dict:
    return {
        "content": content,
        "author": {"id": author_id, "bot": False, "username": "tester"},
        "channel_id": "dm1",
        "mentions": [],
    }


class TestIntents:
    def test_valor_fixo(self):
        # GUILDS(1) + GUILD_MESSAGES(512) + DIRECT_MESSAGES(4096) + MESSAGE_CONTENT(32768)
        assert compute_intents() == 37377


class TestFiltrosMessageCreate:
    def test_dm_sempre_responde(self):
        assert should_respond(_dm_msg(), BOT_ID) is True

    def test_ignora_bots(self):
        assert should_respond(_dm_msg(author_id="555") | {"author": {"id": "555", "bot": True}}, BOT_ID) is False

    def test_ignora_a_si_mesmo(self):
        assert should_respond(_dm_msg(author_id=BOT_ID), BOT_ID) is False

    def test_guild_sem_mencao_nao_responde(self):
        assert should_respond(_guild_msg(), BOT_ID, mention_only=True) is False

    def test_guild_com_mencao_responde(self):
        data = _guild_msg(mentions=[{"id": BOT_ID}])
        assert should_respond(data, BOT_ID, mention_only=True) is True

    def test_mencao_crua_no_texto(self):
        data = _guild_msg(content=f"<@{BOT_ID}> me ajuda")
        assert should_respond(data, BOT_ID, mention_only=True) is True

    def test_mention_only_false_responde_tudo(self):
        assert should_respond(_guild_msg(), BOT_ID, mention_only=False) is True

    def test_allowed_guilds_filtra(self):
        data = _guild_msg(mentions=[{"id": BOT_ID}], guild="outra")
        assert should_respond(data, BOT_ID, allowed_guilds={"g1"}) is False
        data2 = _guild_msg(mentions=[{"id": BOT_ID}], guild="g1")
        assert should_respond(data2, BOT_ID, allowed_guilds={"g1"}) is True

    def test_allowed_channels_filtra(self):
        data = _guild_msg(mentions=[{"id": BOT_ID}], channel="proibido")
        assert should_respond(data, BOT_ID, allowed_channels={"c1"}) is False


class TestMencao:
    def test_mentions_bot_lista(self):
        assert mentions_bot({"mentions": [{"id": BOT_ID}], "content": ""}, BOT_ID)

    def test_mentions_bot_nickname_form(self):
        assert mentions_bot({"mentions": [], "content": f"<@!{BOT_ID}> oi"}, BOT_ID)

    def test_strip_remove_mencao(self):
        assert strip_bot_mention(f"<@{BOT_ID}> qual a previsão?", BOT_ID) == "qual a previsão?"
        assert strip_bot_mention(f"<@!{BOT_ID}>oi", BOT_ID) == "oi"

    def test_strip_sem_mencao_intacto(self):
        assert strip_bot_mention("texto normal", BOT_ID) == "texto normal"


class TestChunking:
    def test_limite_discord_2000(self):
        chunks = chunk_text("y" * 4500, MAX_MESSAGE_CHARS)
        assert all(len(c) <= 2000 for c in chunks)
        assert sum(len(c) for c in chunks) == 4500


class TestBridgeConstrucao:
    def test_start_sem_token_falha_claro(self):
        bridge = DiscordBridge(token="", backend=AgentBackend())
        with pytest.raises(RuntimeError, match="DISCORD_BOT_TOKEN"):
            bridge.start()

    def test_is_authorized_allowlist(self):
        bridge = DiscordBridge(token="x", backend=AgentBackend(), allowed_users=["42"])
        ok = ChannelMessage(channel="discord", user_id="42", chat_id="c", text="t")
        nope = ChannelMessage(channel="discord", user_id="666", chat_id="c", text="t")
        assert bridge._is_authorized(ok) is True
        assert bridge._is_authorized(nope) is False

    def test_allow_all(self):
        bridge = DiscordBridge(token="x", backend=AgentBackend(), allow_all=True)
        anyone = ChannelMessage(channel="discord", user_id="1", chat_id="c", text="t")
        assert bridge._is_authorized(anyone) is True

    def test_build_from_config_env_first(self, tmp_path, monkeypatch):
        from bauer.config_loader import BauerConfig
        from bauer.discord_bridge import build_bridge_from_config

        monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-dc-token")
        cfg = BauerConfig(**{
            "model": {"provider": "ollama", "name": "m",
                      "requested_context": 4096, "minimum_context": 2048},
            "discord": {"enabled": True, "bot_token": "cfg-token",
                        "allowed_users": ["1"], "mention_only": False},
        })
        bridge = build_bridge_from_config(cfg)
        assert bridge.token == "env-dc-token"
        assert bridge.mention_only is False


class TestOnDispatch:
    """Roteamento de MESSAGE_CREATE → backend, com backend fake."""

    @pytest.mark.asyncio
    async def test_message_create_responde_dm(self, tmp_path, monkeypatch):
        received = []
        sent = []

        class FakeBackend(AgentBackend):
            @property
            def is_ready(self):
                return True

            def process(self, msg):
                received.append(msg)
                return "resposta do agent"

        bridge = DiscordBridge(token="x", backend=FakeBackend(), allowed_users=["42"])
        bridge.bot_id = BOT_ID
        monkeypatch.setattr(bridge, "send_text", lambda cid, t: sent.append((cid, t)))
        monkeypatch.setattr(bridge, "_send_typing", lambda cid: None)

        await bridge._on_dispatch({
            "t": "MESSAGE_CREATE",
            "d": _dm_msg(content="qual o status?", author_id="42"),
        })
        assert received and received[0].text == "qual o status?"
        assert sent == [("dm1", "resposta do agent")]

    @pytest.mark.asyncio
    async def test_ready_guarda_session(self):
        bridge = DiscordBridge(token="x", backend=AgentBackend())
        await bridge._on_dispatch({
            "t": "READY",
            "d": {"session_id": "sess123", "resume_gateway_url": "wss://resume.here"},
        })
        assert bridge._session_id == "sess123"
        assert bridge._resume_url == "wss://resume.here"

    @pytest.mark.asyncio
    async def test_mensagem_de_bot_ignorada(self, monkeypatch):
        sent = []
        bridge = DiscordBridge(token="x", backend=AgentBackend(), allow_all=True)
        bridge.bot_id = BOT_ID
        monkeypatch.setattr(bridge, "send_text", lambda cid, t: sent.append(t))
        data = _dm_msg()
        data["author"]["bot"] = True
        await bridge._on_dispatch({"t": "MESSAGE_CREATE", "d": data})
        assert sent == []
