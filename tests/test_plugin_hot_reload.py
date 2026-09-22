from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bauer.plugin_manager import PluginManager, PluginManagerError


def _plugin_source(root: Path, *, version: str = "1.0.0") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "demo_plugin",
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


def test_update_keeps_previous_version_and_reload_validates(tmp_path: Path):
    manager = PluginManager(root=tmp_path / "plugins")
    manager.install(_plugin_source(tmp_path / "v1", version="1.0.0"))

    updated = manager.update(_plugin_source(tmp_path / "v2", version="2.0.0"))

    assert updated.manifest.version == "2.0.0"
    assert [item.manifest.version for item in manager.history("demo_plugin")] == ["1.0.0"]
    assert manager.reload("demo_plugin").manifest.version == "2.0.0"


def test_rollback_restores_previous_and_preserves_current_in_history(tmp_path: Path):
    manager = PluginManager(root=tmp_path / "plugins")
    manager.install(_plugin_source(tmp_path / "v1", version="1.0.0"))
    manager.update(_plugin_source(tmp_path / "v2", version="2.0.0"))

    restored = manager.rollback("demo_plugin")

    assert restored.manifest.version == "1.0.0"
    assert [item.manifest.version for item in manager.history("demo_plugin")] == ["2.0.0"]


def test_invalid_update_does_not_change_active_or_history(tmp_path: Path):
    manager = PluginManager(root=tmp_path / "plugins")
    manager.install(_plugin_source(tmp_path / "v1", version="1.0.0"))
    invalid = _plugin_source(tmp_path / "invalid", version="3.0.0")
    (invalid / "demo_plugin.py").write_text("def (\n", encoding="utf-8")

    with pytest.raises(PluginManagerError, match="entry_point inválido"):
        manager.update(invalid)

    assert manager.get("demo_plugin").manifest.version == "1.0.0"
    assert manager.history("demo_plugin") == []


def test_reload_rejects_disabled_plugin(tmp_path: Path):
    manager = PluginManager(root=tmp_path / "plugins")
    manager.install(_plugin_source(tmp_path / "v1"))
    manager.disable("demo_plugin")

    with pytest.raises(PluginManagerError, match="desabilitado"):
        manager.reload("demo_plugin")
