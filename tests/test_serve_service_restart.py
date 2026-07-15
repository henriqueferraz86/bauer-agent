"""Teste do subcomando `bauer serve service restart` (atalho stop + start)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from bauer.cli import app

runner = CliRunner()


def test_restart_calls_stop_then_start():
    mgr = MagicMock()
    mgr.stop.return_value = "Serviço parado."
    mgr.start.return_value = "Serviço iniciado."
    order: list[str] = []
    mgr.stop.side_effect = lambda: order.append("stop") or "Serviço parado."
    mgr.start.side_effect = lambda: order.append("start") or "Serviço iniciado."

    with patch("bauer.commands.serve_cmd._serve_svc_manager", return_value=mgr):
        result = runner.invoke(app, ["serve", "service", "restart"])

    assert result.exit_code == 0
    assert order == ["stop", "start"]  # para ANTES de iniciar
    assert "parado" in result.output and "iniciado" in result.output


def test_restart_error_exits_1():
    mgr = MagicMock()
    mgr.stop.side_effect = RuntimeError("boom")
    with patch("bauer.commands.serve_cmd._serve_svc_manager", return_value=mgr):
        result = runner.invoke(app, ["serve", "service", "restart"])
    assert result.exit_code == 1
