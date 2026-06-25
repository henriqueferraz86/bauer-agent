"""Testes do config_loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.config_loader import ConfigError, load_config, validate_config_file


VALID_CONFIG = """
agent:
  name: Bauer Agent
  workspace: ./workspace

model:
  provider: ollama
  name: qwen2.5-coder:3b
  requested_context: 16384
  minimum_context: 8192
  auto_downgrade_context: true

ollama:
  host: http://localhost:11434
  timeout_seconds: 30

runtime:
  profile: low
  ram_limit_mb: 4096
  safety_margin_mb: 1024

logging:
  level: info
  file: ./logs/bauer.log
"""


def test_load_valid_config(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(VALID_CONFIG, encoding="utf-8")
    cfg = load_config(p)
    assert cfg.model.name == "qwen2.5-coder:3b"
    assert cfg.model.requested_context == 16384
    assert cfg.runtime.profile == "low"
    assert cfg.runtime.safety_margin_mb == 1024


def test_missing_file(tmp_path: Path, monkeypatch):
    # Isola BAUER_HOME: load_config faz fallback para ~/.bauer/config.yaml
    # quando o path não existe. Sem isolar, o teste falha em máquinas que têm
    # um config global real (passa em CI por não ter). Aqui garantimos que NEM
    # o path NEM o global existem → deve levantar.
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "empty-home"))
    with pytest.raises(ConfigError, match="não encontrado"):
        load_config(tmp_path / "noexist.yaml")


def test_invalid_yaml(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("agent: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML inválido"):
        load_config(p)


def test_invalid_profile(tmp_path: Path):
    bad = VALID_CONFIG.replace("profile: low", "profile: turbo")
    p = tmp_path / "config.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigError, match="profile"):
        load_config(p)


def test_minimum_above_requested_rejected(tmp_path: Path):
    bad = VALID_CONFIG.replace("minimum_context: 8192", "minimum_context: 99999")
    p = tmp_path / "config.yaml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ConfigError, match="minimum_context"):
        load_config(p)


def test_validate_helper_ok(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(VALID_CONFIG, encoding="utf-8")
    ok, msg = validate_config_file(p)
    assert ok
    assert "qwen2.5-coder:3b" in msg


def test_validate_helper_bad(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text("model: {provider: ollama}", encoding="utf-8")
    ok, msg = validate_config_file(p)
    assert not ok
    assert "Config inválida" in msg
