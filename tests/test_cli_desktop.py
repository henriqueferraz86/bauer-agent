"""Tests for `bauer desktop` (DESK-D) — sidecar serve + abertura do browser."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer import desktop_api as da


# ---------------------------------------------------------------------------
# wait_for_health
# ---------------------------------------------------------------------------

class TestWaitForHealth:
    def test_returns_true_when_probe_ok(self):
        assert da.wait_for_health("http://x/health", timeout=1, _probe=lambda _u: True) is True

    def test_returns_false_on_timeout(self):
        assert da.wait_for_health(
            "http://x/health", timeout=0.5, interval=0.1, _probe=lambda _u: False
        ) is False

    def test_succeeds_after_retries(self):
        calls = {"n": 0}

        def probe(_u):
            calls["n"] += 1
            return calls["n"] >= 3

        assert da.wait_for_health("http://x/health", timeout=2, interval=0.05, _probe=probe) is True
        assert calls["n"] >= 3


# ---------------------------------------------------------------------------
# _desktop_serve_cmd
# ---------------------------------------------------------------------------

class TestDesktopServeCmd:
    def test_basic_command(self):
        from bauer.cli import _desktop_serve_cmd

        cmd = _desktop_serve_cmd(Path("config.yaml"), "127.0.0.1", 8799, "")
        assert "serve" in cmd
        assert "--host" in cmd and "127.0.0.1" in cmd
        assert "--port" in cmd and "8799" in cmd
        assert "--api-key" not in cmd

    def test_includes_api_key(self):
        from bauer.cli import _desktop_serve_cmd

        cmd = _desktop_serve_cmd(Path("config.yaml"), "0.0.0.0", 9000, "secret")
        assert "--api-key" in cmd
        assert "secret" in cmd


# ---------------------------------------------------------------------------
# Comando `bauer desktop` via CliRunner
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    from typer.testing import CliRunner
    return CliRunner()


class TestDesktopCommand:
    def test_dev_mode_opens_vite_no_serve(self, runner):
        from bauer.cli import app

        with patch("webbrowser.open") as wopen, patch("subprocess.Popen") as popen:
            r = runner.invoke(app, ["desktop", "--dev"])
        assert r.exit_code == 0
        wopen.assert_called_once()
        assert "5173" in wopen.call_args[0][0]
        popen.assert_not_called()

    def test_dev_no_open_skips_browser(self, runner):
        from bauer.cli import app

        with patch("webbrowser.open") as wopen, patch("subprocess.Popen"):
            r = runner.invoke(app, ["desktop", "--dev", "--no-open"])
        assert r.exit_code == 0
        wopen.assert_not_called()

    def test_starts_sidecar_and_opens(self, runner):
        from bauer.cli import app

        fake_proc = MagicMock()
        fake_proc.poll.return_value = 0
        fake_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=fake_proc) as popen, \
             patch("webbrowser.open") as wopen, \
             patch("bauer.desktop_api.wait_for_health", return_value=True):
            r = runner.invoke(app, ["desktop", "--port", "8799"])

        assert r.exit_code == 0
        popen.assert_called_once()
        wopen.assert_called_once()
        assert "8799" in wopen.call_args[0][0]
        fake_proc.wait.assert_called_once()

    def test_no_open_does_not_launch_browser(self, runner):
        from bauer.cli import app

        fake_proc = MagicMock()
        fake_proc.poll.return_value = 0
        fake_proc.wait.return_value = 0

        with patch("subprocess.Popen", return_value=fake_proc), \
             patch("webbrowser.open") as wopen, \
             patch("bauer.desktop_api.wait_for_health", return_value=True):
            r = runner.invoke(app, ["desktop", "--no-open"])

        assert r.exit_code == 0
        wopen.assert_not_called()

    def test_health_timeout_exits_error(self, runner):
        from bauer.cli import app

        fake_proc = MagicMock()
        fake_proc.poll.return_value = None  # ainda vivo → finally chama terminate

        with patch("subprocess.Popen", return_value=fake_proc), \
             patch("webbrowser.open") as wopen, \
             patch("bauer.desktop_api.wait_for_health", return_value=False):
            r = runner.invoke(app, ["desktop", "--no-open", "--timeout", "1"])

        assert r.exit_code == 1
        wopen.assert_not_called()
        fake_proc.terminate.assert_called()
