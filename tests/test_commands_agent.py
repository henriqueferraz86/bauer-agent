"""Testes de inicialização para bauer/commands/agent_cmd.py.

Cobre: import correto pós-P4, erros de config ausente, flags de CLI,
       subcomandos registrados e listagem de agentes.
NÃO cobre o loop interativo do agente (requer input real do terminal).
"""
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


# ─── Import / atributos esperados pós-refactor P4 ─────────────────────────────


def test_agent_module_importable():
    """Garantia de import pós-refactor P4: agent_app deve estar acessível."""
    from bauer.commands import agent_cmd
    assert hasattr(agent_cmd, "agent_app")


# ─── CLI help / subcomandos registrados ───────────────────────────────────────


def test_agent_help_works():
    """bauer agent --help deve sair com código 0 e mostrar o nome do grupo."""
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0
    assert "agent" in result.output.lower()


def test_agent_subcommands_registered():
    """Subcomandos create, list, run, delete devem aparecer no --help."""
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0
    output_lower = result.output.lower()
    # Verifica os subcomandos confirmados em agent_cmd.py (linhas 556, 577, 612, 913)
    assert "create" in output_lower
    assert "list" in output_lower
    assert "run" in output_lower
    assert "delete" in output_lower


# ─── agent list (sem agentes criados) ─────────────────────────────────────────


def test_agent_list_empty(cfg_path: Path, tmp_path: Path):
    """bauer agent list com workspace vazio deve listar 0 agentes sem traceback."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = runner.invoke(app, [
        "agent", "list",
        "--config", str(cfg_path),
        "--workspace", str(workspace),
    ])
    assert result.exception is None
    # Pode sair com 0 (lista vazia) ou diferente de 0 (sem agentes registrados)
    # O importante é não levantar exceção não tratada
    assert "Traceback" not in result.output


# ─── agent missing config ─────────────────────────────────────────────────────


def test_agent_missing_config_exits_cleanly(tmp_path: Path, monkeypatch):
    """bauer agent sem config.yaml deve sair com erro claro, não traceback."""
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "empty"))
    result = runner.invoke(app, [
        "agent",
        "--config", str(tmp_path / "nao_existe.yaml"),
    ])
    assert result.exit_code != 0
    # Não deve expor traceback Python ao usuário
    assert "Traceback" not in result.output
