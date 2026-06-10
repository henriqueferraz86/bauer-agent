"""Testes das seções de config do Bauer Gateway (telegram/discord/gateway)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def _minimal_config(**overrides):
    from bauer.config_loader import BauerConfig
    base = {"model": {"provider": "ollama", "name": "test-model",
                      "requested_context": 4096, "minimum_context": 2048}}
    base.update(overrides)
    return BauerConfig(**base)


class TestTelegramSection:
    def test_defaults_seguro(self):
        cfg = _minimal_config()
        assert cfg.telegram.enabled is False
        assert cfg.telegram.allowed_users == []
        assert cfg.telegram.allow_all is False
        assert cfg.telegram.bot_token == ""

    def test_campos_validos(self):
        cfg = _minimal_config(telegram={
            "enabled": True,
            "allowed_users": [123, 456],
            "poll_interval": 5.0,
            "max_msgs_per_minute": 10,
        })
        assert cfg.telegram.enabled is True
        assert cfg.telegram.allowed_users == [123, 456]

    def test_campo_desconhecido_rejeitado(self):
        with pytest.raises(ValidationError):
            _minimal_config(telegram={"enbled": True})  # typo

    def test_poll_interval_fora_do_range(self):
        with pytest.raises(ValidationError):
            _minimal_config(telegram={"poll_interval": 0.1})


class TestDiscordSection:
    def test_defaults_seguro(self):
        cfg = _minimal_config()
        assert cfg.discord.enabled is False
        assert cfg.discord.mention_only is True
        assert cfg.discord.allowed_users == []
        assert cfg.discord.allow_all is False

    def test_allowlists(self):
        cfg = _minimal_config(discord={
            "enabled": True,
            "allowed_users": ["111"],
            "allowed_guilds": ["222"],
            "allowed_channels": ["333"],
        })
        assert cfg.discord.allowed_guilds == ["222"]

    def test_campo_desconhecido_rejeitado(self):
        with pytest.raises(ValidationError):
            _minimal_config(discord={"mention_onli": True})  # typo


class TestGatewaySection:
    def test_default_drain_interval(self):
        cfg = _minimal_config()
        assert cfg.gateway.outbox_drain_interval_s == 15

    def test_range_validado(self):
        with pytest.raises(ValidationError):
            _minimal_config(gateway={"outbox_drain_interval_s": 0})


class TestResolveToken:
    def test_env_tem_precedencia(self, monkeypatch):
        from bauer.channel_base import resolve_token
        monkeypatch.setenv("X_TEST_TOKEN", "do-env")
        assert resolve_token("do-config", "X_TEST_TOKEN") == "do-env"

    def test_fallback_para_config(self, monkeypatch):
        from bauer.channel_base import resolve_token
        monkeypatch.delenv("X_TEST_TOKEN", raising=False)
        assert resolve_token("do-config", "X_TEST_TOKEN") == "do-config"

    def test_vazio_quando_nenhum(self, monkeypatch):
        from bauer.channel_base import resolve_token
        monkeypatch.delenv("X_TEST_TOKEN", raising=False)
        assert resolve_token("", "X_TEST_TOKEN") == ""
