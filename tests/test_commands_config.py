"""Testes de integração para bauer/commands/config_cmd.py."""
from __future__ import annotations

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


# ─── config validate ──────────────────────────────────────────────────────────


def test_config_validate_ok(cfg_path: Path):
    result = runner.invoke(app, ["config", "validate", "--config", str(cfg_path)])
    assert result.exit_code == 0
    assert "OK" in result.output


def test_config_validate_file_not_found(tmp_path: Path, monkeypatch):
    # Isola BAUER_HOME para que load_config não faça fallback para ~/.bauer/config.yaml
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "empty"))
    result = runner.invoke(app, ["config", "validate", "--config", str(tmp_path / "nao_existe.yaml")])
    assert result.exit_code != 0


def test_config_validate_invalid_yaml(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("{invalid yaml: [\n", encoding="utf-8")
    result = runner.invoke(app, ["config", "validate", "--config", str(bad)])
    assert result.exit_code != 0


# ─── config show ─────────────────────────────────────────────────────────────


def test_config_show_ok(cfg_path: Path):
    result = runner.invoke(app, ["config", "show", "--config", str(cfg_path)])
    assert result.exit_code == 0
    # Deve exibir ao menos o provider ou o modelo
    assert "ollama" in result.output.lower() or "qwen" in result.output.lower()


def test_config_show_raw(cfg_path: Path):
    """--raw faz dump do dict Pydantic sem formatação Rich."""
    result = runner.invoke(app, ["config", "show", "--config", str(cfg_path), "--raw"])
    assert result.exit_code == 0


# ─── config path / env-path ──────────────────────────────────────────────────


def test_config_path_cmd(cfg_path: Path):
    result = runner.invoke(app, ["config", "path", "--config", str(cfg_path)])
    assert result.exit_code == 0
    # Deve imprimir um caminho que contenha o nome do arquivo
    assert "config" in result.output.lower()


def test_config_env_path_cmd():
    result = runner.invoke(app, ["config", "env-path"])
    assert result.exit_code == 0
    # Deve imprimir algum caminho (pode ser .env inexistente)
    assert len(result.output.strip()) > 0


# ─── config get ──────────────────────────────────────────────────────────────


def test_config_get_existing_key(cfg_path: Path):
    result = runner.invoke(app, ["config", "get", "model.provider", "--config", str(cfg_path)])
    assert result.exit_code == 0
    assert "ollama" in result.output


def test_config_get_missing_key(cfg_path: Path):
    result = runner.invoke(app, ["config", "get", "chave.inexistente", "--config", str(cfg_path)])
    assert result.exit_code != 0
