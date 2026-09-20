from __future__ import annotations

import textwrap
import time

import pytest

from bauer.plugin_process import (
    PluginProcess,
    PluginProcessConfig,
    PluginProcessError,
    PluginProcessState,
    PluginProcessTimeout,
)


def _plugin(tmp_path, source: str):
    path = tmp_path / "plugin.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def test_plugin_process_handshake_health_event_and_shutdown(tmp_path):
    path = _plugin(
        tmp_path,
        """
        from bauer.plugin_hooks import hooks

        @hooks.on("session_start")
        def observe(**kwargs):
            print(kwargs)
        """,
    )
    process = PluginProcess(PluginProcessConfig("demo", path))
    hello = process.start()
    assert hello == {"plugin_id": "demo", "protocol": 1}
    assert process.state is PluginProcessState.READY
    assert process.health()["state"] == "ready"
    assert process.event("session_start", {"session_id": "s1"})["dispatched"] is True
    process.close()
    assert process.state is PluginProcessState.STOPPED
    assert process.pid is None


def test_plugin_process_rejects_oversized_outgoing_message(tmp_path):
    path = _plugin(tmp_path, "")
    process = PluginProcess(PluginProcessConfig("demo", path, max_message_bytes=1024))
    process.start()
    with pytest.raises(PluginProcessError, match="exceeds configured limit"):
        process.event("session_start", {"value": "x" * 2000})
    process.close()


def test_plugin_process_timeout_does_not_leave_child_running(tmp_path):
    path = _plugin(
        tmp_path,
        """
        import time
        time.sleep(0.2)
        """,
    )
    process = PluginProcess(PluginProcessConfig("slow", path, timeout_s=0.03))
    started = time.monotonic()
    with pytest.raises(PluginProcessTimeout):
        process.start()
    assert time.monotonic() - started < 1.0
    process.close()
    assert process.state is PluginProcessState.STOPPED
