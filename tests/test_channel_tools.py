"""Testes das tools channel_send / channel_list do ToolRouter."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.tool_router import ToolError, ToolRouter


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def router(ws: Path) -> ToolRouter:
    return ToolRouter(workspace=ws)


def _register_channel(ws: Path, name="alerts", platform="file", target="alerts.jsonl",
                      enabled=True):
    from bauer.gateway_channels import GatewayChannelRegistry
    reg = GatewayChannelRegistry(ws)
    return reg.upsert(name=name, platform=platform, target=target,
                      enabled=enabled, metadata={})


class TestChannelList:
    def test_vazio_orienta_registro(self, router):
        out = router.execute({"action": "channel_list", "args": {}})
        assert "Nenhum canal" in out
        assert "gateway-channel-add" in out

    def test_lista_canais(self, router, ws):
        _register_channel(ws, name="alerts")
        _register_channel(ws, name="oncall", platform="webhook",
                          target="https://x.example/hook", enabled=False)
        out = router.execute({"action": "channel_list", "args": {}})
        assert "alerts" in out and "oncall" in out
        assert "(off)" in out


class TestChannelSend:
    def test_enfileira_no_outbox(self, router, ws):
        from bauer.gateway_outbox import GatewayOutbox

        _register_channel(ws)
        out = router.execute({
            "action": "channel_send",
            "args": {"channel": "alerts", "text": "deploy concluído"},
        })
        assert "enfileirada" in out
        pending = GatewayOutbox(ws).pending()
        assert len(pending) == 1
        assert pending[0].payload["text"] == "deploy concluído"
        assert pending[0].channel == "file"

    def test_roundtrip_pump_entrega(self, router, ws):
        from bauer.gateway_outbox import GatewayOutbox

        _register_channel(ws)
        router.execute({
            "action": "channel_send",
            "args": {"channel": "alerts", "text": "fim do benchmark"},
        })
        result = GatewayOutbox(ws).deliver_once()
        assert len(result.delivered) == 1
        assert result.failed == []

    def test_canal_inexistente_erro_claro(self, router, ws):
        _register_channel(ws, name="alerts")
        with pytest.raises(ToolError, match="não existe"):
            router.execute({
                "action": "channel_send",
                "args": {"channel": "nada", "text": "oi"},
            })

    def test_canal_desabilitado_rejeitado(self, router, ws):
        _register_channel(ws, name="mudo", enabled=False)
        with pytest.raises(ToolError, match="desabilitado"):
            router.execute({
                "action": "channel_send",
                "args": {"channel": "mudo", "text": "oi"},
            })

    def test_args_obrigatorios(self, router):
        with pytest.raises(ToolError, match="channel"):
            router.execute({"action": "channel_send", "args": {"text": "x"}})
        with pytest.raises(ToolError, match="text"):
            router.execute({"action": "channel_send", "args": {"channel": "x"}})


class TestMetadata:
    def test_tools_aparecem_no_available(self, router):
        tools = router.available_tools()
        assert "channel_send" in tools
        assert "channel_list" in tools

    def test_risk_levels(self):
        from bauer.tool_router import _TOOL_SECURITY
        assert _TOOL_SECURITY["channel_send"]["risk"] == "medium"
        assert _TOOL_SECURITY["channel_send"]["permission"] == "network"
        assert _TOOL_SECURITY["channel_list"]["risk"] == "low"
