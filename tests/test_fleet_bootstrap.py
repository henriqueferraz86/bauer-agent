from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from bauer.cli import app
from bauer.fleet_bootstrap import DEFAULT_MISSION, prepare_fleet_config
from bauer.fleet_supervisor import fleet_root_from_config


CONFIG = {
    "model": {"provider": "ollama", "name": "qwen2.5:7b"},
}


def test_bootstrap_creates_canonical_safe_defaults_without_secrets(tmp_path, monkeypatch):
    home = tmp_path / "bauer-home"
    monkeypatch.setenv("BAUER_HOME", str(home))

    result = prepare_fleet_config(home / "config.yaml")

    data = yaml.safe_load(result.config_path.read_text(encoding="utf-8"))
    assert result.created is True
    assert result.changed is True
    assert data["autopilot"] == {
        "enabled": True,
        "mission": DEFAULT_MISSION,
        "approval_mode": "threshold",
    }
    assert data["fleet"]["enabled"] is True
    assert Path(data["fleet"]["root"]) == home / "workspace"
    assert "api_key" not in result.config_path.read_text(encoding="utf-8")


def test_bootstrap_is_idempotent_and_preserves_user_values(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                **CONFIG,
                "autopilot": {
                    "enabled": False,
                    "mission": "Minha missão",
                    "approval_mode": "deny_all",
                },
                "fleet": {"enabled": False, "root": str(tmp_path / "existing")},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    before = config.read_text(encoding="utf-8")

    first = prepare_fleet_config(config)
    second = prepare_fleet_config(config)

    assert first.changed is False
    assert second.changed is False
    assert config.read_text(encoding="utf-8") == before
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["fleet"]["enabled"] is False


def test_bootstrap_explicit_root_and_mission_are_operator_overrides(tmp_path):
    config = tmp_path / "config.yaml"
    root = tmp_path / "projects"

    result = prepare_fleet_config(config, root=root, mission="Revisar qualidade")

    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert result.root == root.resolve()
    assert data["fleet"]["root"] == str(root.resolve())
    assert data["autopilot"]["mission"] == "Revisar qualidade"


def test_fleet_root_default_honors_bauer_home(tmp_path, monkeypatch):
    home = tmp_path / "bauer-home"
    monkeypatch.setenv("BAUER_HOME", str(home))

    assert fleet_root_from_config(type("Cfg", (), {"root": ""})()) == home / "workspace"
    explicit = tmp_path / "explicit"
    assert fleet_root_from_config(type("Cfg", (), {"root": ""})(), explicit) == explicit.resolve()


def test_fleet_discover_uses_canonical_config_from_different_cwd(tmp_path, monkeypatch):
    home = tmp_path / "bauer-home"
    home.mkdir()
    root = home / "workspace"
    project = root / "alpha"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='alpha'\n", encoding="utf-8")
    (home / "config.yaml").write_text(
        yaml.safe_dump({**CONFIG, "fleet": {"root": str(root)}}, sort_keys=False),
        encoding="utf-8",
    )
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    (cwd / "config.yaml").write_text("this: is-not-a-valid-bauer-config\n", encoding="utf-8")
    monkeypatch.setenv("BAUER_HOME", str(home))
    monkeypatch.chdir(cwd)

    result = CliRunner().invoke(app, ["runtime", "fleet", "discover", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["root"] == str(root.resolve())
    assert payload["count"] == 1
    assert not (cwd / "config.yaml").samefile(home / "config.yaml")


def test_fleet_status_diagnoses_mission_required_and_no_projects(tmp_path, monkeypatch):
    home = tmp_path / "bauer-home"
    root = home / "workspace"
    project = root / "alpha"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='alpha'\n", encoding="utf-8")
    runtime = project / ".bauer_runtime"
    runtime.mkdir()
    (runtime / "autopilot.json").write_text(
        json.dumps({"state": "blocked", "reason": "mission_required"}),
        encoding="utf-8",
    )
    (home / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                **CONFIG,
                "autopilot": {"enabled": True, "mission": ""},
                "fleet": {"root": str(root)},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("BAUER_HOME", str(home))

    result = CliRunner().invoke(app, ["runtime", "fleet", "status"])

    assert result.exit_code == 0, result.output
    assert "mission_required" in result.output
    assert "--mission" in result.output

    empty = tmp_path / "empty"
    empty.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump({**CONFIG, "fleet": {"root": str(empty)}}, sort_keys=False),
        encoding="utf-8",
    )
    result = CliRunner().invoke(app, ["runtime", "fleet", "status"])
    assert result.exit_code == 0, result.output
    assert "Nenhum projeto descoberto" in result.output


def test_fleet_up_is_idempotent_and_starts_without_discover_step(tmp_path, monkeypatch):
    home = tmp_path / "bauer-home"
    root = home / "workspace"
    project = root / "alpha"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='alpha'\n", encoding="utf-8")
    monkeypatch.setenv("BAUER_HOME", str(home))
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)

    from bauer.fleet_supervisor import FleetSupervisor

    monkeypatch.setattr(
        FleetSupervisor,
        "start_background",
        lambda self: {"pid": 4321, "already_running": False, "log_path": str(self.store.log_file)},
    )

    runner = CliRunner()
    first = runner.invoke(app, ["runtime", "fleet", "up"])
    config = home / "config.yaml"
    saved = config.read_text(encoding="utf-8")
    second = runner.invoke(app, ["runtime", "fleet", "up"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert config.read_text(encoding="utf-8") == saved
    assert "Fleet pronto" in first.output
    assert "projetos_descobertos=1" in first.output
    assert not (caller / "config.yaml").exists()
