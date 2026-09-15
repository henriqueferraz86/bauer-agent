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
    assert cfg.memory.semantic_indexing_enabled is True
    assert cfg.memory.semantic_indexing_debounce_s == 0.5
    assert cfg.memory.semantic_indexing_batch_size == 16


def test_autopilot_has_safe_defaults_and_defers_runtime_mission_validation(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(VALID_CONFIG, encoding="utf-8")

    cfg = load_config(p)

    assert cfg.autopilot.enabled is False
    assert cfg.autopilot.workspace == ""
    assert cfg.autopilot.poll_interval_s == 30.0
    assert cfg.autopilot.max_active_goals == 1
    assert cfg.autopilot.max_replans_per_goal == 1
    assert cfg.autopilot.mission == ""
    assert cfg.autopilot.allow_model_proposals is False
    assert cfg.autopilot.approval_mode == "threshold"
    assert cfg.autopilot.max_minutes == 30
    assert cfg.autopilot.max_tool_calls == 500
    assert cfg.autopilot.max_cost_usd == 2.0

    # A config parser cannot know whether persisted goals exist. The controller
    # must decide later whether enabled-without-mission is actionable.
    enabled_without_mission = tmp_path / "enabled-without-mission.yaml"
    enabled_without_mission.write_text(
        VALID_CONFIG + "\nautopilot:\n  enabled: true\n",
        encoding="utf-8",
    )
    assert load_config(enabled_without_mission).autopilot.enabled is True


def test_autopilot_valid_configuration_is_strict_and_bounded(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(
        VALID_CONFIG
        + """
autopilot:
  enabled: true
  workspace: ./workspace
  poll_interval_s: 15
  max_active_goals: 2
  max_replans_per_goal: 3
  mission: "Manter o projeto em estado saudável"
  allow_model_proposals: false
  approval_mode: deny_all
  max_minutes: 20
  max_tool_calls: 120
  max_cost_usd: 0.5
""",
        encoding="utf-8",
    )

    cfg = load_config(p)

    assert cfg.autopilot.enabled is True
    assert cfg.autopilot.workspace == "./workspace"
    assert cfg.autopilot.poll_interval_s == 15.0
    assert cfg.autopilot.max_active_goals == 2
    assert cfg.autopilot.max_replans_per_goal == 3
    assert cfg.autopilot.mission == "Manter o projeto em estado saudável"
    assert cfg.autopilot.approval_mode == "deny_all"
    assert cfg.autopilot.max_minutes == 20
    assert cfg.autopilot.max_tool_calls == 120
    assert cfg.autopilot.max_cost_usd == 0.5


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("poll_interval_s", 0),
        ("max_active_goals", 0),
        ("max_replans_per_goal", -1),
        ("max_minutes", 0),
        ("max_tool_calls", 0),
        ("max_cost_usd", -0.1),
    ],
)
def test_autopilot_rejects_invalid_limits(tmp_path: Path, field: str, value: object):
    p = tmp_path / f"invalid-{field}.yaml"
    p.write_text(
        VALID_CONFIG + f"\nautopilot:\n  {field}: {value}\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=field):
        load_config(p)


def test_autopilot_rejects_unknown_fields_and_approval_modes(tmp_path: Path):
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text(
        VALID_CONFIG + "\nautopilot:\n  auto_approve_everything: true\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="auto_approve_everything"):
        load_config(unknown)

    invalid_approval = tmp_path / "invalid-approval.yaml"
    invalid_approval.write_text(
        VALID_CONFIG + "\nautopilot:\n  approval_mode: always\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="approval_mode"):
        load_config(invalid_approval)


def test_memory_indexing_config_is_strict_and_bounded(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(
        VALID_CONFIG + "\nmemory:\n  semantic_indexing_enabled: false\n"
        "  semantic_indexing_debounce_s: 2\n  semantic_indexing_batch_size: 4\n",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.memory.semantic_indexing_enabled is False
    assert cfg.memory.semantic_indexing_debounce_s == 2
    assert cfg.memory.semantic_indexing_batch_size == 4

    invalid = tmp_path / "invalid-memory.yaml"
    invalid.write_text(VALID_CONFIG + "\nmemory:\n  unknown: true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown"):
        load_config(invalid)


def test_ui_visual_preferences_are_strict_and_have_safe_defaults(tmp_path: Path):
    p = tmp_path / "config.yaml"
    p.write_text(VALID_CONFIG + "\nui:\n  mode: compact\n  emojis: false\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.ui.mode == "compact"
    assert cfg.ui.emojis is False

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text(VALID_CONFIG + "\nui:\n  mode: neon\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mode"):
        load_config(invalid)


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
