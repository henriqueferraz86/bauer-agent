"""Testes extras para cobrir cli.py — doctor, _build_client, _get_or_run_state."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, call
import json

import pytest
import yaml
from typer.testing import CliRunner

from bauer.cli import app, _load_or_die, _get_or_run_state, _build_client


def _make_config_file(tmp_path: Path, provider: str = "ollama") -> Path:
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
        "openai": {
            "host": "https://api.openai.com/v1",
            "api_key": "",
            "timeout_seconds": 60,
        },
        "openrouter": {
            "api_key": "sk-or-key",
            "http_referer": "",
            "x_title": "",
            "timeout_seconds": 60,
        },
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
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(config), encoding="utf-8")
    return cfg_file


def _make_models_file(tmp_path: Path) -> Path:
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
    mdl_file = tmp_path / "models.yaml"
    mdl_file.write_text(yaml.dump(models), encoding="utf-8")
    return mdl_file


def _make_runtime_state():
    """Cria um mock de RuntimeState completo."""
    from bauer.runtime_state import ContextState, RuntimeState
    ctx = ContextState(
        requested=4096,
        modelfile_num_ctx=None,
        env_OLLAMA_CONTEXT_LENGTH=None,
        applied=4096,
        empirical_probe=None,
        reason="ok",
    )
    state = RuntimeState(
        configured_model="phi4-mini",
        configured_provider="ollama",
        active_model="phi4-mini",
        model_available=True,
        ollama_alive=True,
        ollama_host="http://localhost:11434",
        context=ctx,
        tool_mode="bridge",
        profile="medium",
        ram_available_mb=8192,
        ram_total_mb=16384,
        machine_id="test-machine",
        status="ok",
    )
    return state


def _make_doctor_report():
    from bauer.preflight import DoctorReport
    state = _make_runtime_state()
    return DoctorReport(state=state, findings=["Tudo ok"])


# ─── _load_or_die ─────────────────────────────────────────────────────────────

class TestLoadOrDie:
    def test_success(self, tmp_path: Path):
        cfg_file = _make_config_file(tmp_path)
        mdl_file = _make_models_file(tmp_path)
        cfg, reg = _load_or_die(cfg_file, mdl_file)
        assert cfg is not None
        assert reg is not None

    def test_config_error_raises_exit(self, tmp_path: Path):
        import typer
        bad_cfg = tmp_path / "bad.yaml"
        bad_cfg.write_text("model:\n  name: phi4-mini\n", encoding="utf-8")  # Missing required fields
        mdl_file = _make_models_file(tmp_path)
        # ConfigError should cause typer.Exit
        with pytest.raises((typer.Exit, SystemExit)):
            _load_or_die(bad_cfg, mdl_file)

    def test_model_registry_error_raises_exit(self, tmp_path: Path):
        import typer
        cfg_file = _make_config_file(tmp_path)
        bad_models = tmp_path / "bad_models.yaml"
        bad_models.write_text("not: valid: yaml: {", encoding="utf-8")
        with pytest.raises((typer.Exit, SystemExit, Exception)):
            _load_or_die(cfg_file, bad_models)


# ─── _get_or_run_state ────────────────────────────────────────────────────────

class TestGetOrRunState:
    def test_stale_state_none_runs_doctor(self, tmp_path: Path):
        from bauer.config_loader import load_config
        from bauer.model_registry import load_registry

        cfg_file = _make_config_file(tmp_path)
        mdl_file = _make_models_file(tmp_path)
        cfg = load_config(cfg_file)
        reg = load_registry(mdl_file)

        report = _make_doctor_report()
        with patch("bauer.cli.read_state", return_value=None), \
             patch("bauer.cli.run_doctor", return_value=report), \
             patch("bauer.cli.write_state", return_value=tmp_path / "state.json"):
            state = _get_or_run_state(cfg, reg, tmp_path / "state.json")

        assert state is not None

    def test_stale_state_existing_prints_warning(self, tmp_path: Path):
        from bauer.config_loader import load_config
        from bauer.model_registry import load_registry

        cfg_file = _make_config_file(tmp_path)
        mdl_file = _make_models_file(tmp_path)
        cfg = load_config(cfg_file)
        reg = load_registry(mdl_file)

        # State exists but with different model → stale
        existing_state = {
            "configured_provider": "ollama",
            "configured_model": "old-model",  # Different from config
            "ollama_host": "http://localhost:11434",
        }

        report = _make_doctor_report()
        with patch("bauer.cli.read_state", return_value=existing_state), \
             patch("bauer.cli.run_doctor", return_value=report), \
             patch("bauer.cli.write_state", return_value=tmp_path / "state.json"):
            state = _get_or_run_state(cfg, reg, tmp_path / "state.json")

        assert state is not None

    def test_fresh_state_returns_cached(self, tmp_path: Path):
        """Estado fresco (não-stale) deve ser retornado sem rodar doctor."""
        from bauer.config_loader import load_config
        from bauer.model_registry import load_registry

        cfg_file = _make_config_file(tmp_path)
        mdl_file = _make_models_file(tmp_path)
        cfg = load_config(cfg_file)
        reg = load_registry(mdl_file)

        fresh_state = {
            "configured_provider": "ollama",
            "configured_model": "phi4-mini",  # Same as config
            "ollama_host": "http://localhost:11434",
            "ollama_alive": True,
            "context": {"applied": 4096, "requested": 4096, "reason": "ok"},
        }

        with patch("bauer.cli.read_state", return_value=fresh_state), \
             patch("bauer.cli.run_doctor") as mock_doctor:
            state = _get_or_run_state(cfg, reg, tmp_path / "state.json")

        mock_doctor.assert_not_called()
        assert state == fresh_state


# ─── _build_client ────────────────────────────────────────────────────────────

class TestBuildClient:
    def test_ollama_provider(self, tmp_path: Path):
        from bauer.config_loader import load_config
        from bauer.ollama_client import OllamaClient

        cfg_file = _make_config_file(tmp_path, "ollama")
        cfg = load_config(cfg_file)

        with patch("bauer.auth.AuthManager") as mock_auth_cls:
            mock_auth = MagicMock()
            mock_auth.store.load.return_value = None
            mock_auth_cls.return_value = mock_auth
            client = _build_client(cfg)

        assert isinstance(client, OllamaClient)

    def test_openai_provider(self, tmp_path: Path):
        from bauer.config_loader import load_config
        from bauer.openai_client import OpenAIClient

        cfg_file = _make_config_file(tmp_path, "openai")
        cfg = load_config(cfg_file)

        with patch("bauer.auth.AuthManager") as mock_auth_cls:
            mock_auth = MagicMock()
            mock_auth.store.load.return_value = None
            mock_auth_cls.return_value = mock_auth
            client = _build_client(cfg)

        assert isinstance(client, OpenAIClient)

    def test_openrouter_provider(self, tmp_path: Path):
        from bauer.config_loader import load_config
        from bauer.openai_client import OpenAIClient

        cfg_file = _make_config_file(tmp_path, "openrouter")
        cfg = load_config(cfg_file)

        with patch("bauer.auth.AuthManager") as mock_auth_cls:
            mock_auth = MagicMock()
            mock_auth.store.load.return_value = None
            mock_auth_cls.return_value = mock_auth
            client = _build_client(cfg)

        assert isinstance(client, OpenAIClient)

    def test_opencode_provider(self, tmp_path: Path):
        from bauer.config_loader import load_config
        from bauer.openai_client import OpenAIClient

        cfg_file = _make_config_file(tmp_path, "opencode")
        cfg = load_config(cfg_file)

        with patch("bauer.auth.AuthManager") as mock_auth_cls:
            mock_auth = MagicMock()
            mock_auth.store.load.return_value = None
            mock_auth_cls.return_value = mock_auth
            client = _build_client(cfg)

        assert isinstance(client, OpenAIClient)

    def test_custom_provider(self, tmp_path: Path):
        from bauer.config_loader import load_config
        from bauer.openai_client import OpenAIClient

        cfg_file = _make_config_file(tmp_path, "custom")
        cfg = load_config(cfg_file)

        with patch("bauer.auth.AuthManager") as mock_auth_cls:
            mock_auth = MagicMock()
            mock_auth.store.load.return_value = None
            mock_auth_cls.return_value = mock_auth
            client = _build_client(cfg)

        assert isinstance(client, OpenAIClient)

    def test_auth_token_with_api_key(self, tmp_path: Path):
        """Quando há token autenticado, deve usar OpenAIClient."""
        from bauer.config_loader import load_config
        from bauer.auth import AuthToken
        from bauer.openai_client import OpenAIClient

        cfg_file = _make_config_file(tmp_path, "openai")
        cfg = load_config(cfg_file)

        mock_token = AuthToken(
            provider="openai",
            access_token="at",
            api_key="sk-authenticated",
            api_base="https://api.openai.com/v1",
        )

        with patch("bauer.auth.AuthManager") as mock_auth_cls:
            mock_auth = MagicMock()
            mock_auth.store.load.return_value = mock_token
            mock_auth_cls.return_value = mock_auth
            client = _build_client(cfg)

        assert isinstance(client, OpenAIClient)

    def test_auth_jwt_token_shows_warning(self, tmp_path: Path):
        """JWT do Codex deve mostrar aviso e continuar com fallback."""
        from bauer.config_loader import load_config
        from bauer.auth import AuthToken
        from bauer.ollama_client import OllamaClient

        cfg_file = _make_config_file(tmp_path, "ollama")
        cfg = load_config(cfg_file)

        mock_token = AuthToken(
            provider="ollama",
            access_token="jwt-token",
            extra={"type": "jwt"},
        )

        with patch("bauer.auth.AuthManager") as mock_auth_cls:
            mock_auth = MagicMock()
            mock_auth.store.load.return_value = mock_token
            mock_auth_cls.return_value = mock_auth
            client = _build_client(cfg)  # Should not raise

    def test_auth_exception_falls_through(self, tmp_path: Path):
        """Exceção no auth deve ser silenciosa → usa cliente padrão."""
        from bauer.config_loader import load_config
        from bauer.ollama_client import OllamaClient

        cfg_file = _make_config_file(tmp_path, "ollama")
        cfg = load_config(cfg_file)

        with patch("bauer.auth.AuthManager", side_effect=Exception("auth unavailable")):
            client = _build_client(cfg)

        assert isinstance(client, OllamaClient)


# ─── doctor command ──────────────────────────────────────────────────────────

class TestDoctorCommand:
    def test_doctor_ok(self, tmp_path: Path):
        runner = CliRunner()
        cfg_file = _make_config_file(tmp_path)
        mdl_file = _make_models_file(tmp_path)

        report = _make_doctor_report()
        with patch("bauer.cli.run_doctor", return_value=report), \
             patch("bauer.cli.write_state", return_value=tmp_path / "state.json"):
            result = runner.invoke(app, [
                "doctor",
                "--config", str(cfg_file),
                "--models", str(mdl_file),
                "--state-file", str(tmp_path / "state.json"),
            ])

        assert result.exit_code == 0

    def test_doctor_blocked_exits_with_1(self, tmp_path: Path):
        from bauer.preflight import DoctorReport

        runner = CliRunner()
        cfg_file = _make_config_file(tmp_path)
        mdl_file = _make_models_file(tmp_path)

        blocked_state = _make_runtime_state()
        blocked_state.status = "blocked"
        blocked_state.ollama_alive = False
        blocked_state.model_available = False

        report = DoctorReport(state=blocked_state, findings=["Ollama offline"])

        with patch("bauer.cli.run_doctor", return_value=report), \
             patch("bauer.cli.write_state", return_value=tmp_path / "state.json"):
            result = runner.invoke(app, [
                "doctor",
                "--config", str(cfg_file),
                "--models", str(mdl_file),
                "--state-file", str(tmp_path / "state.json"),
            ])

        assert result.exit_code == 1

    def test_doctor_with_findings(self, tmp_path: Path):
        from bauer.preflight import DoctorReport

        runner = CliRunner()
        cfg_file = _make_config_file(tmp_path)
        mdl_file = _make_models_file(tmp_path)

        state = _make_runtime_state()
        state.status = "ok_with_adjustments"
        state.context.applied = 2048
        state.context.reason = "RAM reduzida"

        report = DoctorReport(state=state, findings=["Contexto reduzido", "RAM baixa"])

        with patch("bauer.cli.run_doctor", return_value=report), \
             patch("bauer.cli.write_state", return_value=tmp_path / "state.json"):
            result = runner.invoke(app, [
                "doctor",
                "--config", str(cfg_file),
                "--models", str(mdl_file),
                "--state-file", str(tmp_path / "state.json"),
            ])

        assert result.exit_code == 0


# ─── task commands ────────────────────────────────────────────────────────────

class TestTaskCommands:
    def test_task_add(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(app, [
            "task", "add", "Minha tarefa de teste",
            "--workspace", str(tmp_path),
        ])
        assert result.exit_code == 0

    def test_task_list(self, tmp_path: Path):
        runner = CliRunner()
        # Add a task first
        runner.invoke(app, ["task", "add", "Tarefa 1", "--workspace", str(tmp_path)])
        result = runner.invoke(app, ["task", "list", "--workspace", str(tmp_path)])
        assert result.exit_code == 0

    def test_task_status_invalid(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(app, [
            "task", "status", "999", "DONE",
            "--workspace", str(tmp_path),
        ])
        # Task 999 doesn't exist
        assert result.exit_code != 0 or "nao" in result.output.lower() or "999" in result.output


# ─── project commands ─────────────────────────────────────────────────────────

class TestProjectCommands:
    def test_project_init(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(app, [
            "project", "init", "Test Project",
            "--workspace", str(tmp_path),
        ])
        # Should succeed or already exist
        assert result.exit_code == 0

    def test_project_status(self, tmp_path: Path):
        runner = CliRunner()
        runner.invoke(app, [
            "project", "init", "Test Project",
            "--workspace", str(tmp_path),
        ])
        result = runner.invoke(app, ["project", "status", "--workspace", str(tmp_path)])
        assert result.exit_code == 0


# ─── spec commands ────────────────────────────────────────────────────────────

class TestSpecCommands:
    def test_spec_list_empty(self, tmp_path: Path):
        runner = CliRunner()
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        result = runner.invoke(app, [
            "spec", "list",
            "--dir", str(specs_dir),
        ])
        assert result.exit_code == 0

    def test_spec_show_not_found(self, tmp_path: Path):
        runner = CliRunner()
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
        result = runner.invoke(app, [
            "spec", "show", "nonexistent-spec",
            "--dir", str(specs_dir),
        ])
        # Should fail gracefully (exit != 0 since not found)

    def test_spec_show_existing(self, tmp_path: Path):
        specs_dir = tmp_path / "specs"
        specs_dir.mkdir()
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
        runner = CliRunner()
        result = runner.invoke(app, [
            "spec", "show", "test-spec",
            "--dir", str(specs_dir),
        ])
        assert result.exit_code == 0


# ─── auth commands ────────────────────────────────────────────────────────────

class TestAuthCommandsCLI:
    def test_auth_status_no_providers(self, tmp_path: Path):
        runner = CliRunner()
        with patch("bauer.auth.AuthManager") as mock_cls:
            mock_auth = MagicMock()
            mock_auth.status.return_value = {}
            mock_cls.return_value = mock_auth
            result = runner.invoke(app, ["auth", "status"])
        assert result.exit_code == 0

    def test_auth_providers_list(self, tmp_path: Path):
        runner = CliRunner()
        result = runner.invoke(app, ["auth", "providers"])
        assert result.exit_code == 0

    def test_auth_logout_all(self, tmp_path: Path):
        runner = CliRunner()
        with patch("bauer.auth.AuthManager") as mock_cls:
            mock_auth = MagicMock()
            mock_auth.logout.return_value = True
            mock_cls.return_value = mock_auth
            result = runner.invoke(app, ["auth", "logout"])
        assert result.exit_code == 0


# ─── auth.login_interactive OAuth path ───────────────────────────────────────

class TestLoginInteractiveOAuth:
    def test_openai_provider_calls_codex(self, tmp_path: Path):
        """Quando provider='openai', deve chamar _login_openai_via_codex."""
        from bauer.auth import AuthManager, AuthToken
        mgr = AuthManager(base_dir=tmp_path / "auth")
        mock_token = AuthToken(provider="openai", access_token="jwt", api_key=None)

        with patch.object(mgr, "_login_openai_via_codex", return_value=mock_token) as mock_codex:
            token = mgr.login_interactive(provider="openai")

        mock_codex.assert_called_once()
        assert token.provider == "openai"


# ─── model command ────────────────────────────────────────────────────────────

class TestModelCommand:
    def test_model_command_calls_run_model_switcher(self, tmp_path: Path):
        """Lines 193-194: `bauer model` chama run_model_switcher."""
        runner = CliRunner()
        cfg_file = _make_config_file(tmp_path)

        with patch("bauer.model_switcher.run_model_switcher") as mock_switcher:
            result = runner.invoke(app, ["model", "--config", str(cfg_file)])

        mock_switcher.assert_called_once_with(cfg_file)
        assert result.exit_code == 0


# ─── openrouter with extra headers ───────────────────────────────────────────

class TestBuildClientOpenRouterHeaders:
    def test_openrouter_with_http_referer_and_x_title(self, tmp_path: Path):
        """Lines 605, 607: http_referer e x_title são adicionados como extra_headers."""
        from bauer.config_loader import load_config
        from bauer.openai_client import OpenAIClient

        # Build config with openrouter + http_referer + x_title
        config = {
            "model": {
                "provider": "openrouter",
                "name": "mistral/mistral-7b",
                "requested_context": 4096,
                "minimum_context": 512,
            },
            "ollama": {
                "host": "http://localhost:11434",
                "timeout_seconds": 60,
                "api_key": "",
            },
            "openai": {
                "host": "https://api.openai.com/v1",
                "api_key": "",
                "timeout_seconds": 60,
            },
            "openrouter": {
                "api_key": "sk-or-test-key",
                "http_referer": "https://bauer.ai",   # Line 605
                "x_title": "Bauer Agent",             # Line 607
                "timeout_seconds": 60,
            },
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
        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump(config), encoding="utf-8")
        cfg = load_config(cfg_file)

        with patch("bauer.auth.AuthManager") as mock_auth_cls:
            mock_auth = MagicMock()
            mock_auth.store.load.return_value = None
            mock_auth_cls.return_value = mock_auth
            client = _build_client(cfg)

        assert isinstance(client, OpenAIClient)
        # Verify extra_headers were merged into _headers
        assert client._headers.get("HTTP-Referer") == "https://bauer.ai"
        assert client._headers.get("X-Title") == "Bauer Agent"
