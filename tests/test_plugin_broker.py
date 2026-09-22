from __future__ import annotations

import textwrap

from bauer.plugin_broker import PluginBroker, PluginBrokerState
from bauer.plugin_manager import ManagedPlugin
from bauer.plugin_manifest import PluginManifest


def _managed(tmp_path, *, permissions=("events",), capabilities=("events",), source=""):
    plugin_path = tmp_path / "demo"
    plugin_path.mkdir()
    (plugin_path / "plugin.py").write_text(textwrap.dedent(source), encoding="utf-8")
    manifest = PluginManifest.from_mapping(
        {
            "id": "demo",
            "name": "Demo",
            "version": "1.0.0",
            "bauer": {"min_version": "0.1.0"},
            "capabilities": list(capabilities),
            "permissions": {"runtime": list(permissions)},
            "entry_point": "plugin.py",
        }
    )
    return ManagedPlugin(manifest=manifest, path=plugin_path, enabled=True)


def test_broker_delivers_only_authorized_events_and_reports_state(tmp_path):
    plugin = _managed(tmp_path, source="from bauer.plugin_hooks import hooks\n")
    broker = PluginBroker()
    broker.register(plugin)
    try:
        delivered = broker.dispatch("session_start", {"session_id": "s1", "prompt": "hidden"})
        assert delivered[0].delivered is True
        assert broker.status()[0]["state"] == PluginBrokerState.READY

        denied = broker.dispatch("pre_tool_call", {"action": "read"})
        assert denied[0].delivered is False
        assert denied[0].reason == "missing_capability:tools"
    finally:
        broker.close()


def test_broker_limits_restart_after_plugin_timeout(tmp_path):
    plugin = _managed(
        tmp_path,
        source="""
        import time
        from bauer.plugin_hooks import hooks

        @hooks.on("session_start")
        def slow(**kwargs):
            time.sleep(0.2)
        """,
    )
    broker = PluginBroker(timeout_s=0.03, max_restarts=1)
    broker.register(plugin)
    try:
        result = broker.dispatch("session_start", {})
        assert result[0].delivered is False
        status = broker.status()[0]
        assert status["state"] == PluginBrokerState.UNHEALTHY
        assert status["restarts"] == 1
    finally:
        broker.close()
