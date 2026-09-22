from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bauer.fleet_supervisor import (
    FleetError,
    FleetStateStore,
    FleetSupervisor,
    discover_projects,
)


def _fleet_config(**overrides):
    values = {
        "poll_interval_s": 1.0,
        "max_projects": 20,
        "max_parallel_projects": 20,
        "max_depth": 1,
        "require_project_marker": True,
        "include": [],
        "exclude": [],
        "auto_restart": True,
        "max_restarts_per_project": 5,
        "start_kanban": False,
        "kanban_base_port": 8765,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_discover_projects_uses_markers_and_skips_control_directories(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "alpha").mkdir()
    (root / "alpha" / "pyproject.toml").write_text("[project]\nname='alpha'\n")
    (root / "beta").mkdir()
    (root / "beta" / "package.json").write_text("{}")
    (root / "notes").mkdir()
    (root / "notes" / "README.md").write_text("not a project")
    (root / ".venv").mkdir()
    (root / ".venv" / "pyproject.toml").write_text("ignored")
    (root / "alpha" / ".bauer_runtime").mkdir()

    projects = discover_projects(root)

    assert [project.path.name for project in projects] == ["alpha", "beta"]
    assert {project.marker for project in projects} == {"pyproject.toml", "package.json"}


def test_discover_projects_supports_allowlist_and_depth(tmp_path):
    root = tmp_path / "workspace"
    nested = root / "group" / "nested"
    nested.mkdir(parents=True)
    (nested / "go.mod").write_text("module nested\n")

    assert discover_projects(root, max_depth=1) == []
    projects = discover_projects(root, max_depth=2, include=["group/nested"])
    assert len(projects) == 1
    assert projects[0].path == nested.resolve()
    assert discover_projects(root, max_depth=2, exclude=["group/*"]) == []


def test_fleet_state_lock_rejects_live_owner(tmp_path):
    store = FleetStateStore(tmp_path / "workspace")
    store.acquire_lock()
    try:
        with pytest.raises(FleetError):
            store.acquire_lock()
    finally:
        store.release_lock()
    store.acquire_lock()
    store.release_lock()


def test_fleet_start_background_is_persistent_and_idempotent(tmp_path, monkeypatch):
    from bauer import fleet_supervisor as module

    class FakeProcess:
        pid = 4312

    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(module, "_pid_alive", lambda pid: pid == 4312)
    fleet = FleetSupervisor(tmp_path / "workspace", fleet_config=_fleet_config())

    first = fleet.start_background()
    second = fleet.start_background()

    assert first["pid"] == 4312
    assert first["already_running"] is False
    assert second["already_running"] is True
    assert fleet.store.read()["fleet_pid"] == 4312


def test_fleet_start_background_hides_windows_console_on_windows(tmp_path, monkeypatch):
    from bauer import fleet_supervisor as module

    class FakeProcess:
        pid = 4312

    captured = {}
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: captured.update(kwargs) or FakeProcess())
    monkeypatch.setattr(module, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(module, "_is_windows_platform", lambda: True)
    monkeypatch.setattr(module, "_no_console_window_kwargs", lambda: {"creationflags": 0x08000000})

    fleet = FleetSupervisor(tmp_path / "workspace", fleet_config=_fleet_config())
    fleet.start_background()

    assert captured["creationflags"] == 0x08000000


def test_fleet_pause_and_kill_switch_are_project_scoped(tmp_path):
    root = tmp_path / "workspace"
    project = root / "alpha"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='alpha'\n")
    fleet = FleetSupervisor(root, fleet_config=_fleet_config())

    fleet.set_paused(True)
    assert (project / ".bauer_runtime" / "AUTOPILOT_PAUSE").read_text() == "fleet_pause"
    fleet.set_kill_switch(True)
    assert fleet.store.kill_switch_file.exists()
    assert (project / ".bauer_runtime" / "AUTOPILOT_PAUSE").read_text() == "fleet_pause"
    fleet.set_paused(False)
    assert not (project / ".bauer_runtime" / "AUTOPILOT_PAUSE").exists()
    fleet.set_kill_switch(False)
    assert not fleet.store.kill_switch_file.exists()


def test_fleet_runtime_args_disable_kanban_by_default(tmp_path):
    root = tmp_path / "workspace"
    project = root / "alpha"
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname='alpha'\n")
    fleet = FleetSupervisor(root, fleet_config=_fleet_config())
    args = fleet._runtime_args(fleet.projects()[0], 0, _fleet_config())

    assert "--autopilot" in args
    assert "--no-kanban" in args
    assert "--kanban" not in args


def test_pid_alive_returns_false_for_a_nonexistent_windows_safe_pid():
    from bauer.fleet_supervisor import _pid_alive

    assert _pid_alive(2_000_000_000) is False


def test_fleet_isolates_a_failed_project_from_other_projects(tmp_path, monkeypatch):
    from bauer import fleet_supervisor as module

    root = tmp_path / "workspace"
    for name in ("good", "bad"):
        project = root / name
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text(f"[project]\nname='{name}'\n")

    class FakeRuntimeSupervisor:
        def __init__(self, workspace, **kwargs):
            self.workspace = workspace

        def status(self):
            return SimpleNamespace(
                to_public_dict=lambda: {
                    "state": "not_started",
                    "supervisor_alive": False,
                    "supervisor_pid": None,
                }
            )

        def start_background(self, args):
            if self.workspace.name == "bad":
                raise OSError("simulated start failure")
            return {"pid": 9911}

    monkeypatch.setattr(module, "RuntimeSupervisor", FakeRuntimeSupervisor)
    monkeypatch.setattr(module, "_pid_alive", lambda pid: False)
    fleet = FleetSupervisor(root, fleet_config=_fleet_config())

    result = fleet.tick().to_dict()
    by_name = {Path(item["path"]).name: item for item in result["projects"]}

    assert by_name["good"]["state"] == "starting"
    assert by_name["bad"]["state"] == "failed"
    assert "simulated start failure" in by_name["bad"]["last_error"]
