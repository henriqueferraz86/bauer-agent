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


def test_plugin_process_reports_child_exit_during_handshake(tmp_path):
    path = _plugin(
        tmp_path,
        """
        import os
        os._exit(0)
        """,
    )
    process = PluginProcess(PluginProcessConfig("dead", path))
    with pytest.raises(PluginProcessError, match="stdout closed"):
        process.start()
    process.close()


def test_plugin_process_rejects_protocol_output_from_plugin(tmp_path):
    path = _plugin(
        tmp_path,
        """
        import sys
        from bauer.plugin_hooks import hooks

        @hooks.on("session_start")
        def corrupt(**kwargs):
            sys.__stdout__.write("this is not JSON\\n")
            sys.__stdout__.flush()
        """,
    )
    process = PluginProcess(PluginProcessConfig("corrupt", path))
    process.start()
    with pytest.raises(PluginProcessError, match="invalid JSON"):
        process.event("session_start", {})
    process.close()


def test_plugin_process_shutdown_is_idempotent(tmp_path):
    path = _plugin(tmp_path, "")
    process = PluginProcess(PluginProcessConfig("demo", path))
    process.start()
    process.close()
    process.close()
    assert process.state is PluginProcessState.STOPPED
