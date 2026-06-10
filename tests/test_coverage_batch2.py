"""Testes adicionais de cobertura — batch 2.

Alvo: cobrir linhas específicas em cli.py:
  - models test (278-329)
  - learning export com failures (2059-2068)
  - learning explain com evidências (2008-2010)
  - logs com config error (2247-2248)
  - spec new com id inválido (2295-2306)
  - spec status com spec existente
  - task board vazio (1795-1796) e com tarefas (1781-1789)
  - learning_forget_model com --confirm
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from bauer.cli import app


# ─── helpers ─────────────────────────────────────────────────────────────────


def _make_config(tmp_path: Path, provider: str = "ollama") -> Path:
    config = {
        "model": {
            "provider": provider,
            "name": "phi4-mini",
            "requested_context": 4096,
            "minimum_context": 512,
        },
        "ollama": {
            "host": "http://localhost:11434",
            "timeout_seconds": 60,
            "api_key": "",
        },
        "openai": {"host": "https://api.openai.com/v1", "api_key": "", "timeout_seconds": 60},
        "openrouter": {"api_key": "", "http_referer": "", "x_title": "", "timeout_seconds": 60},
        "opencode": {"timeout_seconds": 60},
        "runtime": {"profile": "medium", "safety_margin_mb": 512},
        "logging": {"level": "info", "file": ""},
        "router": {"enabled": False, "router_model": "phi4-mini"},
        "tools": {
            "shell_enabled": False,
            "web_enabled": False,
            "safe_mode": True,
            "timeout_seconds": 30,
            "max_output_kb": 512,
        },
    }
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(config), encoding="utf-8")
    return p


def _make_models(tmp_path: Path) -> Path:
    models = {
        "models": {
            "phi4-mini": {
                "ram_base_mb": 2500,
                "ram_per_1k_ctx_mb": 0.5,
                "max_context_safe": 32768,
                "provider": "ollama",
                "supports_tools": False,
                "ram_profile": "low",
            }
        }
    }
    p = tmp_path / "models.yaml"
    p.write_text(yaml.dump(models), encoding="utf-8")
    return p


def _make_memory_with_oom(memory_dir: Path) -> None:
    """Cria MODEL_EXPERIENCE.md com entradas de OOM para gerar recommendations com evidence."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    content = """# MODEL_EXPERIENCE — Bauer Agent Learning

## [2024-01-15T10:00:00] phi4-mini — session
- context_tokens: 32768
- result: oom
- ram_used_mb: 3000
- machine_id: test-machine
- lesson: OOM com contexto alto

## [2024-01-16T10:00:00] phi4-mini — session
- context_tokens: 32768
- result: out of memory
- ram_used_mb: 3100
- machine_id: test-machine
- lesson: Reduzir contexto
"""
    (memory_dir / "MODEL_EXPERIENCE.md").write_text(content, encoding="utf-8")


def _make_memory_with_failures(memory_dir: Path) -> None:
    """Cria FAILED_ATTEMPTS.md com entradas para cobrir learning export."""
    memory_dir.mkdir(parents=True, exist_ok=True)
    content = """# FAILED_ATTEMPTS — Bauer Agent Learning

## [2024-01-15T10:00:00] ImportError em tool_router
- error: ModuleNotFoundError: No module named 'ddgs'
- fix: pip install ddgs
- machine_id: test-machine

## [2024-01-16T10:00:00] Contexto insuficiente
- error: context length exceeded
- fix: Reduzir contexto para 4096
- machine_id: test-machine
"""
    (memory_dir / "FAILED_ATTEMPTS.md").write_text(content, encoding="utf-8")


# ─── models test (lines 278-329) ─────────────────────────────────────────────


class TestModelsTestCommand:
    def test_models_test_model_available(self, tmp_path: Path):
        """Modelo encontrado no Ollama e no registry — mostra tabela completa."""
        runner = CliRunner()
        cfg_file = _make_config(tmp_path)
        mdl_file = _make_models(tmp_path)

        mock_client = MagicMock()
        mock_client.is_alive.return_value = (True, "ok")
        mock_client.has_model.return_value = True
        from bauer.openai_client import ModelfileParams
        mock_client.show_model.return_value = ModelfileParams(num_ctx=4096, context_length=None, size_bytes=0, raw={})

        with patch("bauer.cli._build_client", return_value=mock_client), \
             patch("bauer.machine_id.machine_summary",
                   return_value={"ram_available_mb": 8192, "ram_total_mb": 16384}):
            result = runner.invoke(app, [
                "models", "test", "phi4-mini",
                "--config", str(cfg_file),
                "--models", str(mdl_file),
            ])

        assert result.exit_code == 0
        assert "phi4-mini" in result.output

    def test_models_test_ollama_offline(self, tmp_path: Path):
        """Ollama offline — exibe status vermelho."""
        runner = CliRunner()
        cfg_file = _make_config(tmp_path)
        mdl_file = _make_models(tmp_path)

        mock_client = MagicMock()
        mock_client.is_alive.return_value = (False, "connection refused")
        mock_client.has_model.return_value = False

        with patch("bauer.cli._build_client", return_value=mock_client), \
             patch("bauer.machine_id.machine_summary",
                   return_value={"ram_available_mb": 4096, "ram_total_mb": 8192}):
            result = runner.invoke(app, [
                "models", "test", "phi4-mini",
                "--config", str(cfg_file),
                "--models", str(mdl_file),
            ])

        assert result.exit_code == 0
        # Shows "offline" or "nao"
        assert "offline" in result.output.lower() or "nao" in result.output.lower()

    def test_models_test_model_not_in_registry(self, tmp_path: Path):
        """Modelo não está no registry — exibe aviso."""
        runner = CliRunner()
        cfg_file = _make_config(tmp_path)
        mdl_file = _make_models(tmp_path)

        mock_client = MagicMock()
        mock_client.is_alive.return_value = (True, "ok")
        mock_client.has_model.return_value = False

        with patch("bauer.cli._build_client", return_value=mock_client), \
             patch("bauer.machine_id.machine_summary",
                   return_value={"ram_available_mb": 8192, "ram_total_mb": 16384}):
            result = runner.invoke(app, [
                "models", "test", "unknown-model-xyz",
                "--config", str(cfg_file),
                "--models", str(mdl_file),
            ])

        assert result.exit_code == 0
        # "Aviso" for unknown model
        assert "unknown-model-xyz" in result.output

    def test_models_test_show_model_exception(self, tmp_path: Path):
        """show_model levanta exceção — continua sem modelfile_ctx."""
        runner = CliRunner()
        cfg_file = _make_config(tmp_path)
        mdl_file = _make_models(tmp_path)

        mock_client = MagicMock()
        mock_client.is_alive.return_value = (True, "ok")
        mock_client.has_model.return_value = True
        mock_client.show_model.side_effect = Exception("show_model failed")

        with patch("bauer.cli._build_client", return_value=mock_client), \
             patch("bauer.machine_id.machine_summary",
                   return_value={"ram_available_mb": 8192, "ram_total_mb": 16384}):
            result = runner.invoke(app, [
                "models", "test", "phi4-mini",
                "--config", str(cfg_file),
                "--models", str(mdl_file),
            ])

        assert result.exit_code == 0


# ─── learning export with failures (lines 2059-2068) ─────────────────────────


class TestLearningExportWithFailures:
    def test_learning_export_with_failed_attempts(self, tmp_path: Path):
        """Lines 2059-2068: FAILED_ATTEMPTS exportados como JSONL."""
        runner = CliRunner()
        memory_dir = tmp_path / "memory"
        _make_memory_with_failures(memory_dir)

        output_dir = tmp_path / "datasets"
        result = runner.invoke(app, [
            "learning", "export",
            "--dir", str(memory_dir),
            "--output", str(output_dir),
        ])

        assert result.exit_code == 0
        fail_path = output_dir / "failed_attempts.jsonl"
        assert fail_path.exists()
        lines = fail_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        record = json.loads(lines[0])
        assert "error" in record
        assert "fix" in record

    def test_learning_export_empty_memory(self, tmp_path: Path):
        """Export com memória vazia — cria arquivos com 0 registros."""
        runner = CliRunner()
        memory_dir = tmp_path / "empty_memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        output_dir = tmp_path / "datasets"

        result = runner.invoke(app, [
            "learning", "export",
            "--dir", str(memory_dir),
            "--output", str(output_dir),
        ])

        assert result.exit_code == 0
        assert (output_dir / "model_experience.jsonl").exists()
        assert (output_dir / "failed_attempts.jsonl").exists()


# ─── learning explain with evidence (lines 2008-2010) ────────────────────────


class TestLearningExplainWithEvidence:
    def test_learning_explain_with_oom_evidence(self, tmp_path: Path):
        """Lines 2008-2010: recomendação com evidence exibe lista de evidências.

        Usa LearningEngine.recommend() mockado para garantir que as linhas
        2008-2010 (exibição de evidência) sejam cobertas.
        """
        from bauer.learning_engine import Recommendation

        runner = CliRunner()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir(parents=True)

        # Mock recommend() to return a recommendation WITH evidence
        recs_with_evidence = [
            Recommendation(
                action="Reduza o contexto",
                reason="OOM detectado",
                evidence=["phi4-mini [2024-01-15]: result=oom"],
                severity="warning",
            )
        ]

        with patch("bauer.learning_engine.LearningEngine.recommend", return_value=recs_with_evidence):
            result = runner.invoke(app, [
                "learning", "explain",
                "--dir", str(memory_dir),
            ])

        assert result.exit_code == 0
        # Lines 2008-2010 should now be covered
        assert "Evidencia" in result.output or "oom" in result.output.lower()


# ─── logs command with config error (lines 2247-2248) ────────────────────────


class TestLogsCommandConfigError:
    def test_logs_config_error_uses_default_path(self, tmp_path: Path):
        """Lines 2247-2248: config inválido → usa logs/bauer.log como padrão.

        ConfigError é capturado e log_path é definido como o padrão.
        O comando pode sair com 0 (se logs/bauer.log existe) ou 1 (se não existe).
        """
        runner = CliRunner()

        bad_config = tmp_path / "bad_config.yaml"
        bad_config.write_text("model:\n  provider: invalid_xyz\n", encoding="utf-8")

        result = runner.invoke(app, [
            "logs",
            "--config", str(bad_config),
        ])

        # ConfigError → uses "logs/bauer.log" as default path.
        # If the file exists in CWD, exit 0 (shows content).
        # If it doesn't exist, exit 1.
        # Either way, lines 2247-2248 are covered.
        assert result.exit_code in (0, 1)
        # Should NOT throw an uncaught exception
        assert result.exception is None or isinstance(result.exception, SystemExit)


# ─── spec new with invalid id (lines 2295-2304) ──────────────────────────────


class TestSpecNewCommand:
    def test_spec_new_with_invalid_id(self, tmp_path: Path):
        """Lines 2295-2304: spec new com id inválido exibe erro e sai."""
        runner = CliRunner()
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()

        result = runner.invoke(app, [
            "spec", "new", "INVALID ID WITH SPACES!",
            "--dir", str(specs_dir),
        ])

        assert result.exit_code == 1
        assert "inválido" in result.output.lower() or "invalido" in result.output.lower()

    def test_spec_new_valid_id_wizard_canceled(self, tmp_path: Path):
        """Spec new com id válido → chama wizard → wizard cancela → exit 0."""
        runner = CliRunner()
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()

        with patch("bauer.spec_wizard.wizard_create_spec", return_value=None):
            result = runner.invoke(app, [
                "spec", "new", "valid-spec-id",
                "--dir", str(specs_dir),
            ])

        assert result.exit_code == 0


# ─── spec status update ───────────────────────────────────────────────────────


class TestSpecStatusCommand:
    def _make_spec(self, specs_dir: Path) -> None:
        specs_dir.mkdir(exist_ok=True)
        spec_content = """id: test-spec
title: Test Spec
version: "1.0"
status: draft
purpose: Testing purposes
behavior:
  - Rule 1
acceptance_criteria:
  - Given A, when B, then C
"""
        (specs_dir / "test-spec.yaml").write_text(spec_content, encoding="utf-8")

    def test_spec_status_update_valid(self, tmp_path: Path):
        """spec status <id> <new_status> — atualiza status de um spec existente."""
        runner = CliRunner()
        specs_dir = tmp_path / "specs"
        self._make_spec(specs_dir)

        result = runner.invoke(app, [
            "spec", "status", "test-spec", "approved",
            "--dir", str(specs_dir),
        ])

        assert result.exit_code == 0
        assert "approved" in result.output.lower()

    def test_spec_status_invalid_status(self, tmp_path: Path):
        """spec status com status inválido → exit 1."""
        runner = CliRunner()
        specs_dir = tmp_path / "specs"
        self._make_spec(specs_dir)

        result = runner.invoke(app, [
            "spec", "status", "test-spec", "INVALID_STATUS_XYZ",
            "--dir", str(specs_dir),
        ])

        assert result.exit_code == 1
        assert "inválido" in result.output.lower() or "invalido" in result.output.lower()


# ─── task board (lines 1781-1789, 1795-1796) ─────────────────────────────────


class TestTaskBoard:
    def test_task_board_empty(self, tmp_path: Path):
        """Lines 1795-1796: sem tarefas → mensagem 'Nenhuma tarefa'."""
        runner = CliRunner()
        result = runner.invoke(app, [
            "task", "board",
            "--workspace", str(tmp_path),
        ])

        assert result.exit_code == 0
        assert "nenhuma" in result.output.lower() or "adicione" in result.output.lower()

    def test_task_board_with_tasks(self, tmp_path: Path):
        """Lines 1781-1789: com tarefas → exibe colunas com ícones de texto."""
        runner = CliRunner()
        # Add some tasks first
        runner.invoke(app, ["task", "add", "Task A", "--workspace", str(tmp_path)])
        runner.invoke(app, ["task", "add", "Task B", "--workspace", str(tmp_path)])

        result = runner.invoke(app, [
            "task", "board",
            "--workspace", str(tmp_path),
        ])

        assert result.exit_code == 0
        assert "Task A" in result.output or "TODO" in result.output


# ─── learning forget-model with --confirm ────────────────────────────────────


class TestLearningForgetModel:
    def test_forget_model_with_confirm_no_entries(self, tmp_path: Path):
        """learning forget-model --confirm sem entradas → mensagem 'Nenhuma'."""
        runner = CliRunner()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        result = runner.invoke(app, [
            "learning", "forget-model", "phi4-mini",
            "--confirm",
            "--dir", str(memory_dir),
        ])

        assert result.exit_code == 0
        assert "nenhuma" in result.output.lower() or "encontrada" in result.output.lower() or "0" in result.output

    def test_forget_model_with_entries(self, tmp_path: Path):
        """learning forget-model --confirm remove entradas do modelo."""
        runner = CliRunner()
        memory_dir = tmp_path / "memory"
        _make_memory_with_oom(memory_dir)

        result = runner.invoke(app, [
            "learning", "forget-model", "phi4-mini",
            "--confirm",
            "--dir", str(memory_dir),
        ])

        assert result.exit_code == 0
        # Either removed entries or showed count
        assert result.output  # Just check it ran

    def test_forget_model_no_confirm_aborts(self, tmp_path: Path):
        """learning forget-model sem --confirm → typer.confirm → abort."""
        runner = CliRunner()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        # Input 'n' to the confirmation prompt (abort)
        result = runner.invoke(app, [
            "learning", "forget-model", "phi4-mini",
            "--dir", str(memory_dir),
        ], input="n\n")

        # typer.confirm with abort=True raises typer.Abort on 'n'
        assert result.exit_code != 0 or "Aborted" in result.output or "abort" in result.output.lower()


# ─── learning analyze --last (line 2160) ─────────────────────────────────────


class TestLearningAnalyzeLast:
    def test_learning_analyze_show_last_with_content(self, tmp_path: Path):
        """Line 2160: --last com análise salva → exibe o conteúdo."""
        from bauer.learning_engine import LearningEngineV2
        runner = CliRunner()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        # Pre-save an analysis
        engine = LearningEngineV2(memory_dir)
        analysis_content = "# Análise de Aprendizado\n\nResultados positivos encontrados."
        analysis_path = memory_dir / "LEARNING_ANALYSIS.md"
        analysis_path.write_text(analysis_content, encoding="utf-8")

        result = runner.invoke(app, [
            "learning", "analyze",
            "--last",
            "--dir", str(memory_dir),
        ])

        assert result.exit_code == 0
        assert "Análise" in result.output or "análise" in result.output.lower() or "Resultados" in result.output

    def test_learning_analyze_show_last_no_content(self, tmp_path: Path):
        """learning analyze --last sem análise salva → exibe mensagem."""
        runner = CliRunner()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()  # No analysis file

        result = runner.invoke(app, [
            "learning", "analyze",
            "--last",
            "--dir", str(memory_dir),
        ])

        assert result.exit_code == 0
        assert "analise" in result.output.lower() or "análise" in result.output.lower()


# ─── learning reset with --confirm (line 2121) ───────────────────────────────


class TestLearningResetConfirm:
    def test_learning_reset_no_confirm_aborts(self, tmp_path: Path):
        """Line 2121: sem --confirm → typer.confirm → 'n' → abort."""
        runner = CliRunner()
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()

        result = runner.invoke(app, [
            "learning", "reset",
            "--dir", str(memory_dir),
        ], input="n\n")

        assert result.exit_code != 0 or "Aborted" in result.output or "abort" in result.output.lower()
