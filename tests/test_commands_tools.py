"""Testes de integração para bauer/commands/tools_cmd.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer.cli import app

runner = CliRunner()


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    c = tmp_path / "config.yaml"
    c.write_text(
        "agent:\n  name: Test\n  workspace: ./workspace\n"
        "model:\n  provider: ollama\n  name: qwen2.5:3b\n"
        "  requested_context: 8192\n  minimum_context: 4096\n"
        "  auto_downgrade_context: true\n"
        "ollama:\n  host: http://localhost:11434\n  timeout_seconds: 10\n  api_key: ''\n"
        "openai:\n  host: http://localhost:1234\n  timeout_seconds: 30\n  api_key: ''\n"
        "runtime:\n  profile: low\n  ram_limit_mb: 4096\n  safety_margin_mb: 512\n"
        "logging:\n  level: info\n  file: null\n"
        "tools:\n  shell_enabled: false\n  safe_mode: true\n"
        "  timeout_seconds: 30\n  max_output_kb: 50\n"
        "serve:\n  host: 0.0.0.0\n  port: 8000\n  api_key: ''\n  workers: 1\n",
        encoding="utf-8",
    )
    return c


# ─── tools list ───────────────────────────────────────────────────────────────


def test_tools_list_ok(cfg_path: Path, tmp_path: Path):
    """tools list deve exibir tabela com ferramentas disponíveis."""
    result = runner.invoke(app, [
        "tools", "list",
        "--config", str(cfg_path),
        "--workspace", str(tmp_path),
    ])
    assert result.exit_code == 0
    assert result.exception is None


def test_tools_list_no_config(tmp_path: Path):
    """tools list sem config válido deve ainda funcionar (router com defaults)."""
    missing_cfg = tmp_path / "nao_existe.yaml"
    result = runner.invoke(app, [
        "tools", "list",
        "--config", str(missing_cfg),
        "--workspace", str(tmp_path),
    ])
    # ConfigError é capturado e cfg=None é passado para o router — deve continuar
    assert result.exit_code == 0
    assert result.exception is None


# ─── tools run ────────────────────────────────────────────────────────────────


def test_tools_run_list_dir(cfg_path: Path, tmp_path: Path):
    """tools run com list_dir deve retornar listagem do diretório."""
    action = json.dumps({"action": "list_dir", "args": {"path": str(tmp_path)}})
    result = runner.invoke(app, [
        "tools", "run", action,
        "--config", str(cfg_path),
        "--workspace", str(tmp_path),
    ])
    assert result.exit_code == 0
    assert result.exception is None


def test_tools_run_from_json_file(cfg_path: Path, tmp_path: Path):
    """tools run aceita caminho de arquivo .json como alternativa ao JSON inline."""
    action_file = tmp_path / "action.json"
    action_file.write_text(
        json.dumps({"action": "list_dir", "args": {"path": str(tmp_path)}}),
        encoding="utf-8",
    )
    result = runner.invoke(app, [
        "tools", "run", str(action_file),
        "--config", str(cfg_path),
        "--workspace", str(tmp_path),
    ])
    assert result.exit_code == 0
    assert result.exception is None


def test_tools_run_invalid_json(cfg_path: Path, tmp_path: Path):
    """JSON inválido deve resultar em exit code != 0 (ToolError → Exit(1))."""
    result = runner.invoke(app, [
        "tools", "run", "{nao-e-json}",
        "--config", str(cfg_path),
        "--workspace", str(tmp_path),
    ])
    assert result.exit_code != 0


def test_tools_run_unknown_action(cfg_path: Path, tmp_path: Path):
    """Ação desconhecida (JSON válido, action inexistente) deve exit != 0."""
    action = json.dumps({"action": "acao_inexistente", "args": {}})
    result = runner.invoke(app, [
        "tools", "run", action,
        "--config", str(cfg_path),
        "--workspace", str(tmp_path),
    ])
    assert result.exit_code != 0
