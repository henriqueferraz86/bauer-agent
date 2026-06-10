"""Testes do BauerGatewayRuntime — supervisão de bridges + outbox pump."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from bauer.channel_base import AgentBackend, BaseBridge, ChannelMessage
from bauer.gateway_runtime import BauerGatewayRuntime


class _FakeBridge(BaseBridge):
    name = "fake"

    def __init__(self, backend, crash_times: int = 0):
        super().__init__(backend)
        self.started = 0
        self.crash_times = crash_times

    def start(self):
        self.started += 1
        if self.crash_times > 0:
            self.crash_times -= 1
            raise RuntimeError("crash simulado")
        self._stop_event.wait()  # bloqueia até stop()

    def send_text(self, chat_id, text):
        pass

    def _is_authorized(self, msg):
        return True


class TestRuntimeLifecycle:
    def test_start_nonblock_e_stop(self, tmp_path):
        bridge = _FakeBridge(AgentBackend())
        rt = BauerGatewayRuntime(
            backend=bridge.backend, bridges=[bridge],
            workspace=tmp_path, outbox_drain_interval_s=1,
        )
        rt.start(block=False)
        time.sleep(0.3)
        assert bridge.started == 1
        st = rt.status()
        assert st["running"] is True
        assert st["bridges"][0]["name"] == "fake"
        rt.stop()
        assert rt.status()["running"] is False

    def test_bridge_crash_e_restartado(self, tmp_path):
        bridge = _FakeBridge(AgentBackend(), crash_times=1)
        rt = BauerGatewayRuntime(
            backend=bridge.backend, bridges=[bridge],
            workspace=tmp_path, outbox_drain_interval_s=60,
        )
        # backoff inicial de 5s tornaria o teste lento — reduz esperando direto
        rt.start(block=False)
        deadline = time.time() + 8
        while bridge.started < 2 and time.time() < deadline:
            time.sleep(0.2)
        rt.stop()
        assert bridge.started >= 2, "bridge deveria ter sido reiniciado após crash"

    def test_sem_bridges_nao_explode(self, tmp_path):
        rt = BauerGatewayRuntime(
            backend=AgentBackend(), bridges=[],
            workspace=tmp_path, outbox_drain_interval_s=1,
        )
        rt.start(block=False)
        time.sleep(0.2)
        rt.stop()


class TestOutboxPump:
    def test_pump_entrega_pendentes(self, tmp_path):
        from bauer.gateway_outbox import GatewayOutbox

        outbox = GatewayOutbox(tmp_path)
        outbox.enqueue(
            channel="file", target="saida.jsonl",
            payload={"text": "mensagem do pump"},
        )
        assert len(outbox.pending()) == 1

        rt = BauerGatewayRuntime(
            backend=AgentBackend(), bridges=[],
            workspace=tmp_path, outbox_drain_interval_s=1,
        )
        rt.start(block=False)
        deadline = time.time() + 6
        while outbox.pending() and time.time() < deadline:
            time.sleep(0.2)
        rt.stop()
        assert outbox.pending() == []
        assert rt.status()["outbox"]["delivered"] >= 1

    def test_status_outbox_pending_count(self, tmp_path):
        from bauer.gateway_outbox import GatewayOutbox

        outbox = GatewayOutbox(tmp_path)
        outbox.enqueue(channel="file", target="x.jsonl", payload={"a": 1})
        rt = BauerGatewayRuntime(
            backend=AgentBackend(), bridges=[],
            workspace=tmp_path, outbox_drain_interval_s=3600,
        )
        assert rt.status()["outbox"]["pending"] == 1


class TestFromConfig:
    def test_canais_desabilitados_sem_bridges(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "model:\n  provider: ollama\n  name: m\n"
            "  requested_context: 4096\n  minimum_context: 2048\n"
            f"agent:\n  workspace: {(tmp_path / 'ws').as_posix()}\n",
            encoding="utf-8",
        )
        rt = BauerGatewayRuntime.from_config(cfg_file)
        assert rt.bridges == []

    def test_telegram_habilitado_cria_bridge(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "model:\n  provider: ollama\n  name: m\n"
            "  requested_context: 4096\n  minimum_context: 2048\n"
            f"agent:\n  workspace: {(tmp_path / 'ws').as_posix()}\n"
            "telegram:\n  enabled: true\n  allowed_users: [1]\n",
            encoding="utf-8",
        )
        rt = BauerGatewayRuntime.from_config(cfg_file)
        assert [b.name for b in rt.bridges] == ["telegram"]
        # bridges compartilham o MESMO backend
        assert rt.bridges[0].backend is rt.backend
