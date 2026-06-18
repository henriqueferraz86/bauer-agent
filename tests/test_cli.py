"""Testes de integração para CLI commands (Typer CliRunner)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer.cli import app


runner = CliRunner()


# --- helpers ----------------------------------------------------------------


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
def mem_dir(tmp_path: Path) -> Path:
    d = tmp_path / "memory"
    d.mkdir()
    from bauer.memory_manager import MemoryManager
    MemoryManager(d).init_files()
    return d


# --- config validate --------------------------------------------------------


def test_config_validate_ok(cfg_path: Path):
    result = runner.invoke(app, ["config", "validate", "--config", str(cfg_path)])
    assert result.exit_code == 0
    assert "OK" in result.output


def test_config_validate_missing_file(tmp_path: Path):
    result = runner.invoke(app, ["config", "validate", "--config", str(tmp_path / "missing.yaml")])
    assert result.exit_code != 0


def test_config_validate_invalid_yaml(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("{invalid yaml: [", encoding="utf-8")
    result = runner.invoke(app, ["config", "validate", "--config", str(bad)])
    assert result.exit_code != 0


# --- config show ------------------------------------------------------------


def test_config_show(cfg_path: Path):
    result = runner.invoke(app, ["config", "show", "--config", str(cfg_path)])
    assert result.exit_code == 0
    assert "qwen2.5:3b" in result.output or "ollama" in result.output


# --- models list ------------------------------------------------------------


def test_models_list(models_path: Path):
    result = runner.invoke(app, ["models", "list", "--models", str(models_path)])
    assert result.exit_code == 0
    # Rich table may truncate the colon — check for model family name instead
    assert "qwen2.5" in result.output or "ollama" in result.output


# --- memory init / list / show ----------------------------------------------


def test_memory_init(tmp_path: Path):
    mem = tmp_path / "memory"
    result = runner.invoke(app, ["memory", "init", "--dir", str(mem)])
    assert result.exit_code == 0
    assert mem.exists()


def test_memory_list(mem_dir: Path):
    result = runner.invoke(app, ["memory", "list", "--dir", str(mem_dir)])
    assert result.exit_code == 0
    assert "MODEL_EXPERIENCE" in result.output or "arquivo" in result.output.lower()


def test_memory_show_decisions(mem_dir: Path):
    result = runner.invoke(app, ["memory", "show", "decisions", "--dir", str(mem_dir)])
    assert result.exit_code == 0


def test_memory_add_decision(mem_dir: Path):
    result = runner.invoke(
        app, ["memory", "add-decision", "Decisao teste", "Motivo teste", "--dir", str(mem_dir)]
    )
    assert result.exit_code == 0
    content = (mem_dir / "DECISIONS.md").read_text(encoding="utf-8")
    assert "Decisao teste" in content


def test_memory_add_failure(mem_dir: Path):
    result = runner.invoke(
        app, ["memory", "add-failure", "Falha teste", "Erro teste", "--dir", str(mem_dir)]
    )
    assert result.exit_code == 0
    content = (mem_dir / "FAILED_ATTEMPTS.md").read_text(encoding="utf-8")
    assert "Falha teste" in content


def test_memory_summarize(mem_dir: Path):
    result = runner.invoke(app, ["memory", "summarize", "--dir", str(mem_dir)])
    assert result.exit_code == 0
    assert "MODEL_EXPERIENCE" in result.output or "arquivo" in result.output.lower()


# --- learning show / explain / reset / forget-model / export ---------------


def test_learning_show(mem_dir: Path):
    result = runner.invoke(app, ["learning", "show", "--dir", str(mem_dir)])
    assert result.exit_code == 0
    assert "entradas" in result.output.lower() or "MODEL_EXPERIENCE" in result.output


def test_learning_explain(mem_dir: Path):
    result = runner.invoke(app, ["learning", "explain", "--dir", str(mem_dir)])
    assert result.exit_code == 0
    assert "recomendac" in result.output.lower() or "Nenhuma" in result.output


def test_learning_reset_with_confirm(mem_dir: Path):
    from bauer.memory_manager import MemoryManager
    MemoryManager(mem_dir).add_failure("titulo", "erro")

    result = runner.invoke(app, ["learning", "reset", "--confirm", "--dir", str(mem_dir)])
    assert result.exit_code == 0
    assert "resetado" in result.output.lower() or "FAILED_ATTEMPTS" in result.output


def test_learning_forget_model_no_data(mem_dir: Path):
    result = runner.invoke(
        app, ["learning", "forget-model", "nenhum-modelo", "--confirm", "--dir", str(mem_dir)]
    )
    assert result.exit_code == 0
    assert "Nenhuma entrada" in result.output or "nenhum" in result.output.lower()


def test_learning_forget_model_removes_entry(mem_dir: Path):
    from bauer.memory_manager import MemoryManager
    MemoryManager(mem_dir).add_model_experience("target-model", 4096, "oom", 0, "abc")

    result = runner.invoke(
        app, ["learning", "forget-model", "target-model", "--confirm", "--dir", str(mem_dir)]
    )
    assert result.exit_code == 0
    assert "removido" in result.output.lower()


def test_learning_export(mem_dir: Path, tmp_path: Path):
    from bauer.memory_manager import MemoryManager
    MemoryManager(mem_dir).add_model_experience("m", 4096, "ok", 2048, "abc")

    out_dir = tmp_path / "datasets"
    result = runner.invoke(
        app,
        ["learning", "export", "--dir", str(mem_dir), "--output", str(out_dir)],
    )
    assert result.exit_code == 0
    assert (out_dir / "model_experience.jsonl").exists()
    assert (out_dir / "failed_attempts.jsonl").exists()


def test_learning_export_jsonl_valid(mem_dir: Path, tmp_path: Path):
    from bauer.memory_manager import MemoryManager
    MemoryManager(mem_dir).add_model_experience("m", 4096, "ok", 2048, "abc")

    out_dir = tmp_path / "datasets"
    runner.invoke(app, ["learning", "export", "--dir", str(mem_dir), "--output", str(out_dir)])

    lines = (out_dir / "model_experience.jsonl").read_text(encoding="utf-8").splitlines()
    for line in lines:
        obj = json.loads(line)  # deve ser JSON válido
        assert "input" in obj
        assert "output" in obj


# --- logs -------------------------------------------------------------------


def test_logs_missing_file(tmp_path: Path):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "agent:\n  name: Test\n  workspace: ./workspace\n"
        "model:\n  provider: ollama\n  name: m\n"
        "  requested_context: 4096\n  minimum_context: 2048\n"
        "ollama:\n  host: http://localhost:11434\n"
        "openai:\n  host: http://localhost:1234\n"
        "runtime:\n  profile: low\n  ram_limit_mb: 4096\n  safety_margin_mb: 512\n"
        f"logging:\n  level: info\n  file: {tmp_path / 'no_such.log'}\n"
        "tools:\n  shell_enabled: false\n  safe_mode: true\n"
        "serve:\n  host: 0.0.0.0\n  port: 8000\n  api_key: ''\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["logs", "--config", str(cfg)])
    assert result.exit_code != 0
    assert "nao encontrado" in result.output.lower() or "Log" in result.output


def test_logs_shows_last_lines(tmp_path: Path, cfg_path: Path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "bauer.log"
    log_file.write_text("linha 1\nlinha 2\nlinha 3\n", encoding="utf-8")

    # Pontua o config para usar este log
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8").replace("file: null", f"file: {log_file}"),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["logs", "--config", str(cfg_path), "--lines", "2"])
    assert result.exit_code == 0
    assert "linha 3" in result.output
    assert "linha 2" in result.output


# --- tools list -------------------------------------------------------------


def test_tools_list(cfg_path: Path, tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    result = runner.invoke(
        app, ["tools", "list", "--config", str(cfg_path), "--workspace", str(ws)]
    )
    assert result.exit_code == 0
    assert "list_dir" in result.output or "read_file" in result.output
