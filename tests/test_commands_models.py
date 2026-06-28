"""Testes de integração para bauer/commands/models_cmd.py."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer.cli import app

runner = CliRunner()


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def models_path(tmp_path: Path) -> Path:
    m = tmp_path / "models.yaml"
    m.write_text(
        "models:\n"
        "  qwen2.5:3b:\n"
        "    provider: ollama\n"
        "    ram_base_mb: 2500\n"
        "    ram_per_1k_ctx_mb: 40\n"
        "    max_context_safe: 32768\n"
        "    supports_tools: false\n"
        "    ram_profile: low\n",
        encoding="utf-8",
    )
    return m


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


# ─── models list ─────────────────────────────────────────────────────────────

# NOTA: models list aceita apenas --models (não --config) — ver models_cmd.py L79-L110.


def test_models_list_renders(models_path: Path):
    """Lista os modelos do models.yaml com tabela formatada."""
    result = runner.invoke(app, ["models", "list", "--models", str(models_path)])
    assert result.exit_code == 0
    assert "qwen2.5" in result.output or "ollama" in result.output


def test_models_list_missing_models_file(tmp_path: Path):
    """Arquivo models.yaml ausente deve falhar de forma limpa."""
    result = runner.invoke(app, ["models", "list", "--models", str(tmp_path / "nao_existe.yaml")])
    # ModelRegistryError → exit code 2
    assert result.exit_code != 0


def test_models_list_empty_models(tmp_path: Path):
    """models.yaml vazio (sem entradas) deve listar tabela vazia sem traceback."""
    empty = tmp_path / "models.yaml"
    empty.write_text("models: {}\n", encoding="utf-8")
    result = runner.invoke(app, ["models", "list", "--models", str(empty)])
    assert result.exit_code == 0
    assert result.exception is None


# ─── models test (requer conexão Ollama — usa mock) ───────────────────────────


def test_models_test_offline(cfg_path: Path, models_path: Path):
    """models test deve reportar Ollama offline sem travar (mock is_alive=False)."""
    from unittest.mock import patch, MagicMock

    mock_client = MagicMock()
    mock_client.is_alive.return_value = (False, "Connection refused")
    mock_client.has_model.return_value = False

    with patch("bauer.commands._runtime._build_client", return_value=mock_client):
        result = runner.invoke(app, [
            "models", "test", "qwen2.5:3b",
            "--config", str(cfg_path),
            "--models", str(models_path),
        ])
    # Deve completar sem exceção (exit 0 com status "nao pronto")
    assert result.exception is None
    assert result.exit_code == 0
    assert "offline" in result.output.lower() or "nao pronto" in result.output.lower()
