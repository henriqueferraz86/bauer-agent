from __future__ import annotations

from pathlib import Path

import yaml

from bauer.plugin_hooks import HookRegistry
from bauer.plugin_manager import PluginManager


def test_managed_plugins_are_loaded_only_through_the_broker(tmp_path: Path):
    marker = tmp_path / "marker.txt"
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "managed_demo",
                "name": "Managed Demo",
                "version": "1.0.0",
                "bauer": {"min_version": "0.9"},
                "capabilities": ["events"],
                "permissions": {"runtime": ["events"]},
                "entry_point": "managed_demo.py",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (source / "managed_demo.py").write_text(
        "from pathlib import Path\n"
        "from bauer.plugin_hooks import hooks\n"
        f"marker = Path({str(marker)!r})\n"
        "@hooks.on('session_start')\n"
        "def observe(session_id=None, **kwargs):\n"
        "    marker.write_text(str(session_id), encoding='utf-8')\n",
        encoding="utf-8",
    )
    manager = PluginManager(root=tmp_path / "managed")
    manager.install(source)

    registry = HookRegistry()
    assert registry.load_managed_plugins(tmp_path / "managed") == ["managed_demo"]
    registry.emit("session_start", session_id="session-1", model="test")

    assert marker.read_text(encoding="utf-8") == "session-1"
    registry.close_managed_plugins()
