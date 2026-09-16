from __future__ import annotations

from pathlib import Path

import yaml

from bauer.fleet_bootstrap import (
    DEFAULT_AUTOPILOT_MISSION,
    ensure_fleet_defaults,
    fleet_config_path,
    fleet_models_path,
    summarize_fleet_blockers,
)


def _config(path: Path, extra: str = "") -> None:
    path.write_text(
        "model:\n  provider: ollama\n  name: qwen2.5:7b\n" + extra,
        encoding="utf-8",
    )


def test_fleet_defaults_use_bauer_home_not_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))

    assert fleet_config_path() == (tmp_path / "bauer-home" / "config.yaml").resolve()
    assert fleet_models_path() == (tmp_path / "bauer-home" / "models.yaml").resolve()


def test_ensure_fleet_defaults_is_idempotent_and_validates(tmp_path):
    path = tmp_path / "config.yaml"
    _config(path)

    effective, changed = ensure_fleet_defaults(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert effective == path.resolve()
    assert changed == [
        "autopilot.enabled",
        "autopilot.mission",
        "autopilot.approval_mode",
        "fleet.enabled",
    ]
    assert raw["autopilot"] == {
        "enabled": True,
        "mission": DEFAULT_AUTOPILOT_MISSION,
        "approval_mode": "threshold",
    }
    assert raw["fleet"] == {"enabled": True}
    assert ensure_fleet_defaults(path)[1] == []


def test_ensure_fleet_defaults_preserves_explicit_choices(tmp_path):
    path = tmp_path / "config.yaml"
    _config(
        path,
        "autopilot:\n  enabled: false\n  mission: Minha missão\n  approval_mode: deny_all\n"
        "fleet:\n  enabled: false\n",
    )

    _, changed = ensure_fleet_defaults(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert changed == []
    assert raw["autopilot"]["enabled"] is False
    assert raw["autopilot"]["mission"] == "Minha missão"
    assert raw["autopilot"]["approval_mode"] == "deny_all"
    assert raw["fleet"]["enabled"] is False


def test_explicit_mission_overrides_existing_mission(tmp_path):
    path = tmp_path / "config.yaml"
    _config(path, "autopilot:\n  mission: antiga\n")

    ensure_fleet_defaults(path, mission="nova missão")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert raw["autopilot"]["mission"] == "nova missão"


def test_summarize_fleet_blockers_is_concise():
    status = {
        "projects": [
            {"path": r"C:\workspace\alpha", "autopilot": {"state": "blocked", "reason": "mission_required"}},
            {"path": r"C:\workspace\beta", "autopilot": {"state": "planning", "reason": ""}},
        ]
    }

    assert summarize_fleet_blockers(status) == ["alpha: mission_required"]


def test_fleet_up_bootstraps_from_any_cwd(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from bauer.cli import app
    import bauer.commands.runtime_cmd as runtime_cmd

    bauer_home = tmp_path / "bauer-home"
    bauer_home.mkdir()
    config = bauer_home / "config.yaml"
    _config(config)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("BAUER_HOME", str(bauer_home))
    monkeypatch.chdir(elsewhere)

    class FakeStatus:
        fleet_alive = False
        fleet_pid = None

        def to_dict(self):
            return {"projects": []}

    class FakeFleet:
        def projects(self):
            return []

        def status(self):
            return FakeStatus()

        def start_background(self):
            return {"pid": 1234}

    seen: dict[str, Path] = {}

    def fake_runtime(root, config_path, models):
        seen["config"] = config_path
        return FakeFleet()

    monkeypatch.setattr(runtime_cmd, "_fleet_runtime", fake_runtime)
    result = CliRunner().invoke(app, ["runtime", "fleet", "up"])

    assert result.exit_code == 0, result.stdout
    assert seen["config"] == config.resolve()
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert raw["autopilot"]["mission"] == DEFAULT_AUTOPILOT_MISSION
