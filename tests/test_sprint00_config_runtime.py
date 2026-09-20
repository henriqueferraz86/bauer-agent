from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from bauer.cli import app
from bauer.config_admin import safe_update_config
from bauer.config_loader import ConfigError


runner = CliRunner()


def _write_config(path: Path, *, adapter: str = "bauer_native") -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "model": {
                    "provider": "ollama",
                    "name": "qwen3:1b",
                    "requested_context": 8192,
                },
                "runtime": {
                    "default_adapter": adapter,
                    "adapters": {"bauer_native": {"enabled": True}},
                },
                "logging": {"level": "debug", "file": None},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


class _HealthyAdapter:
    name = "fake"

    def healthcheck(self):
        return {"status": "healthy", "runtime_adapter": self.name}


class _UnhealthyAdapter:
    name = "fake"

    def healthcheck(self):
        return {"status": "unhealthy", "runtime_adapter": self.name, "error": "missing SDK"}


def test_safe_update_config_preserves_model_and_creates_backup(tmp_path: Path):
    config = _write_config(tmp_path / "config.yaml")
    before = config.read_bytes()

    backup = safe_update_config(
        config,
        patch={"runtime": {"default_adapter": "agno"}},
    )

    saved = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert saved["model"]["name"] == "qwen3:1b"
    assert saved["runtime"]["default_adapter"] == "agno"
    assert backup == config.with_name("config.yaml.bak")
    assert backup.read_bytes() == before


def test_safe_update_config_rolls_back_when_post_write_validation_fails(tmp_path: Path, monkeypatch):
    config = _write_config(tmp_path / "config.yaml")
    before = config.read_bytes()
    import bauer.config_admin as config_admin

    calls = 0

    def fail_after_replace(path):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConfigError("simulated post-write failure")
        return object()

    monkeypatch.setattr(config_admin, "load_config", fail_after_replace)
    with pytest.raises(ConfigError, match="simulated post-write failure"):
        safe_update_config(config, patch={"runtime": {"default_adapter": "agno"}})

    assert config.read_bytes() == before
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["runtime"]["default_adapter"] == "bauer_native"


def test_runtime_use_defaults_to_canonical_and_preserves_local_shadow(tmp_path: Path, monkeypatch):
    home = tmp_path / "bauer-home"
    home.mkdir()
    canonical = _write_config(home / "config.yaml")
    project = tmp_path / "project"
    project.mkdir()
    local = project / "config.yaml"
    local.write_text("model:\n  provider: invalid\n", encoding="utf-8")
    monkeypatch.setenv("BAUER_HOME", str(home))
    monkeypatch.chdir(project)

    result = runner.invoke(app, ["runtime", "use", "bauer_native"])

    assert result.exit_code == 0, result.output
    saved = yaml.safe_load(canonical.read_text(encoding="utf-8"))
    assert saved["model"]["name"] == "qwen3:1b"
    assert saved["runtime"]["default_adapter"] == "bauer_native"
    assert local.read_text(encoding="utf-8") == "model:\n  provider: invalid\n"
    assert "Healthcheck" in result.output


def test_runtime_use_explicit_config_wins(tmp_path: Path):
    canonical = _write_config(tmp_path / "canonical.yaml", adapter="bauer_native")
    explicit = _write_config(tmp_path / "explicit.yaml", adapter="bauer_native")

    with patch(
        "bauer.core.runtime.adapters.get_runtime_adapter",
        return_value=_HealthyAdapter(),
    ):
        result = runner.invoke(app, ["runtime", "use", "agno", "--config", str(explicit)])

    assert result.exit_code == 0, result.output
    assert yaml.safe_load(explicit.read_text(encoding="utf-8"))["runtime"]["default_adapter"] == "agno"
    assert yaml.safe_load(canonical.read_text(encoding="utf-8"))["runtime"]["default_adapter"] == "bauer_native"


def test_runtime_use_rejects_unhealthy_adapter_without_mutating_config(tmp_path: Path):
    config = _write_config(tmp_path / "config.yaml")

    with patch(
        "bauer.core.runtime.adapters.get_runtime_adapter",
        return_value=_UnhealthyAdapter(),
    ):
        result = runner.invoke(app, ["runtime", "use", "agno", "--config", str(config)])

    assert result.exit_code == 1
    assert "Healthcheck falhou" in result.output
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["runtime"]["default_adapter"] == "bauer_native"
    assert not config.with_name("config.yaml.bak").exists()


def test_runtime_use_dry_run_does_not_write(tmp_path: Path):
    config = _write_config(tmp_path / "config.yaml")
    before = config.read_bytes()

    with patch(
        "bauer.core.runtime.adapters.get_runtime_adapter",
        return_value=_HealthyAdapter(),
    ):
        result = runner.invoke(app, ["runtime", "use", "agno", "--config", str(config), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert config.read_bytes() == before
    assert not config.with_name("config.yaml.bak").exists()


def test_config_path_and_validate_use_canonical_default(tmp_path: Path, monkeypatch):
    home = tmp_path / "bauer-home"
    home.mkdir()
    config = _write_config(home / "config.yaml")
    project = tmp_path / "project"
    project.mkdir()
    (project / "config.yaml").write_text("model:\n  provider: invalid\n", encoding="utf-8")
    monkeypatch.setenv("BAUER_HOME", str(home))
    monkeypatch.chdir(project)

    path_result = runner.invoke(app, ["config", "path"])
    validate_result = runner.invoke(app, ["config", "validate"])

    assert path_result.exit_code == 0
    flattened = path_result.output.replace("\n", "")
    assert f"Canonical : {config}" in flattened
    assert f"Effective : {config}" in flattened
    assert "Source    : canonical" in path_result.output
    assert validate_result.exit_code == 0, validate_result.output
