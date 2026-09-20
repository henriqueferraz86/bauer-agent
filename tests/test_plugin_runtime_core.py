from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from bauer.cli import app
from bauer.plugin_manager import PluginManager, PluginManagerError
from bauer.plugin_manifest import PluginManifest, PluginManifestError


runner = CliRunner()


def _plugin_source(root: Path, *, plugin_id: str = "demo_plugin", version: str = "1.0.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "id": plugin_id,
                "name": "Demo Plugin",
                "version": version,
                "bauer": {"min_version": "0.9"},
                "capabilities": ["tools", "events"],
                "permissions": {"filesystem": {"read": True}, "runtime": ["events"]},
                "tools": ["demo_tool"],
                "entry_point": "demo_plugin.py",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "demo_plugin.py").write_text("def demo_tool():\n    return 'ok'\n", encoding="utf-8")
    return root


def test_manifest_validates_and_normalizes_permissions(tmp_path: Path):
    manifest = PluginManifest.from_file(_plugin_source(tmp_path / "source") / "plugin.yaml")

    assert manifest.id == "demo_plugin"
    assert manifest.permissions == ("filesystem.read", "runtime.events")
    assert manifest.to_dict()["bauer"]["min_version"] == "0.9"


def test_manifest_rejects_unknown_permission_and_conflicting_tools(tmp_path: Path):
    raw = yaml.safe_load((_plugin_source(tmp_path / "source") / "plugin.yaml").read_text(encoding="utf-8"))
    raw["permissions"] = {"database": True}
    with pytest.raises(PluginManifestError, match="desconhecidos"):
        PluginManifest.from_mapping(raw)

    raw["capabilities"] = ["events"]
    raw["permissions"] = {"runtime": ["tools"]}
    with pytest.raises(PluginManifestError, match="capability 'tools'"):
        PluginManifest.from_mapping(raw)


def test_manager_install_persists_registry_and_state(tmp_path: Path):
    source = _plugin_source(tmp_path / "source")
    manager = PluginManager(root=tmp_path / "plugins")

    installed = manager.install(source)

    assert installed.manifest.id == "demo_plugin"
    assert installed.enabled
    assert (tmp_path / "plugins" / "registry.json").exists()
    assert manager.get("demo_plugin").path == tmp_path / "plugins" / "installed" / "demo_plugin"

    manager.disable("demo_plugin")
    assert not manager.get("demo_plugin").enabled
    manager.enable("demo_plugin")
    assert manager.get("demo_plugin").enabled


def test_manager_detects_id_conflict_without_overwriting(tmp_path: Path):
    manager = PluginManager(root=tmp_path / "plugins")
    manager.install(_plugin_source(tmp_path / "source-a", version="1.0.0"))

    with pytest.raises(PluginManagerError, match="já instalado"):
        manager.install(_plugin_source(tmp_path / "source-b", version="2.0.0"))

    assert manager.get("demo_plugin").manifest.version == "1.0.0"


def test_manager_invalid_candidate_isolated(tmp_path: Path):
    source = _plugin_source(tmp_path / "source")
    (source / "demo_plugin.py").write_text("def (\n", encoding="utf-8")
    manager = PluginManager(root=tmp_path / "plugins")

    with pytest.raises(PluginManagerError, match="entry_point inválido"):
        manager.install(source)

    assert manager.list_plugins() == []
    assert not (tmp_path / "plugins" / "registry.json").exists()


def test_manager_force_install_rolls_back_on_registry_failure(tmp_path: Path, monkeypatch):
    manager = PluginManager(root=tmp_path / "plugins")
    manager.install(_plugin_source(tmp_path / "source-a", version="1.0.0"))

    def fail(*args, **kwargs):
        raise OSError("registry unavailable")

    monkeypatch.setattr(manager, "_write_registry_entry", fail)
    with pytest.raises(PluginManagerError, match="registry unavailable"):
        manager.install(_plugin_source(tmp_path / "source-b", version="2.0.0"), force=True)

    assert manager.get("demo_plugin").manifest.version == "1.0.0"


def test_manager_uninstall_removes_plugin_and_registry(tmp_path: Path):
    manager = PluginManager(root=tmp_path / "plugins")
    manager.install(_plugin_source(tmp_path / "source"))

    manager.uninstall("demo_plugin")

    assert manager.list_plugins() == []
    assert "demo_plugin" not in manager._read_registry()


def test_plugin_cli_lists_searches_and_changes_state(tmp_path: Path, monkeypatch):
    home = tmp_path / "bauer-home"
    monkeypatch.setenv("BAUER_HOME", str(home))
    PluginManager().install(_plugin_source(tmp_path / "source"))

    listed = runner.invoke(app, ["plugin", "list"])
    searched = runner.invoke(app, ["plugin", "search", "demo"])
    disabled = runner.invoke(app, ["plugin", "disable", "demo_plugin"])
    info = runner.invoke(app, ["plugin", "info", "demo_plugin"])

    assert listed.exit_code == 0, listed.output
    assert "demo_plugin" in listed.output
    assert searched.exit_code == 0 and "demo_plugin" in searched.output
    assert disabled.exit_code == 0, disabled.output
    assert info.exit_code == 0 and "enabled" in info.output
    assert not PluginManager().get("demo_plugin").enabled


@pytest.mark.skipif(shutil.which("git") is None, reason="git is required")
def test_manager_installs_from_git_repository(tmp_path: Path):
    source = _plugin_source(tmp_path / "git-source")
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(source), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(source), "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "plugin"],
        check=True,
        capture_output=True,
    )
    manager = PluginManager(root=tmp_path / "plugins")

    installed = manager.install(source)

    assert installed.manifest.id == "demo_plugin"
