from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer import supervisor as supervisor_module
from bauer.cli import app
from bauer.supervisor import RuntimeStateStore, RuntimeSupervisor, ServiceRuntime, ServiceSpec


def test_runtime_supervisor_builds_default_services(tmp_path: Path):
    workspace = tmp_path / "workspace"
    supervisor = RuntimeSupervisor(workspace, config="config.yaml", models="models.yaml", python="python")

    specs = supervisor.build_service_specs(kanban_port=9999)
    names = {spec.name: spec for spec in specs}

    assert set(names) == {"dispatcher", "cron", "outbox", "kanban"}
    assert names["dispatcher"].enabled is True
    assert names["dispatcher"].restart is True
    assert "dispatch" in names["dispatcher"].command
    assert "daemon" in names["dispatcher"].command
    assert names["outbox"].command[-3:] == ["--watch", "--interval", "30"]
    assert names["kanban"].restart is False
    assert "--no-browser" in names["kanban"].command


def test_runtime_state_store_roundtrip_and_stop_file(tmp_path: Path):
    store = RuntimeStateStore(tmp_path / "workspace")

    store.write({"state": "running", "supervisor_pid": 123, "services": []})
    assert store.read()["state"] == "running"

    store.request_stop()
    assert store.stop_file.exists()

    store.clear_stop()
    assert not store.stop_file.exists()


def test_runtime_status_not_started(tmp_path: Path):
    status = RuntimeSupervisor(tmp_path / "workspace").status().to_public_dict()

    assert status["state"] == "not_started"
    assert status["supervisor_alive"] is False
    assert status["services"] == []


def test_runtime_start_dry_run_cli(tmp_path: Path):
    runner = CliRunner()
    workspace = tmp_path / "workspace"

    result = runner.invoke(
        app,
        [
            "runtime",
            "start",
            "--workspace",
            str(workspace),
            "--dry-run",
            "--no-kanban",
            "--dispatch-interval",
            "7",
        ],
    )

    assert result.exit_code == 0
    assert "Bauer Runtime Services" in result.output
    assert "dispatcher" in result.output
    assert "runtime" in result.output
    assert "--dispatch-interval" in result.output


def _fake_popen(pid: int = 4242) -> MagicMock:
    proc = MagicMock()
    proc.pid = pid
    proc.poll.return_value = None
    return proc


# ─── _no_console_window_kwargs ─────────────────────────────────────────────
# Testa a logica pura isolada de os.name, sem tocar em Path — instanciar
# WindowsPath/PosixPath fora do SO real levanta NotImplementedError, entao
# essa funcao foi extraida especificamente pra ser testavel nos dois sentidos
# a partir de qualquer SO.


def test_no_console_window_kwargs_on_windows():
    """Regressao: `bauer runtime start` enchia a tela de prompts porque
    _start_service nao suprimia a janela de console dos servicos filhos
    (dispatcher/cron/outbox/kanban) no Windows — so start_background (o
    processo supervisor em si) tinha esse tratamento."""
    with patch.object(supervisor_module, "subprocess") as mock_subprocess, \
         patch.object(supervisor_module.os, "name", "nt"):
        mock_subprocess.CREATE_NO_WINDOW = 0x08000000
        kwargs = supervisor_module._no_console_window_kwargs()
    assert kwargs == {"creationflags": 0x08000000}


def test_no_console_window_kwargs_on_posix():
    """Fora do Windows, creationflags nem existe em subprocess — sem efeito."""
    with patch.object(supervisor_module.os, "name", "posix"):
        kwargs = supervisor_module._no_console_window_kwargs()
    assert kwargs == {}


def test_start_service_applies_no_console_window_kwargs(tmp_path: Path):
    """_start_service de fato repassa _no_console_window_kwargs() pro Popen."""
    supervisor = RuntimeSupervisor(tmp_path / "workspace", python="python")
    spec = ServiceSpec(name="dispatcher", command=["python", "-m", "bauer.cli", "dispatch", "daemon"])
    service = ServiceRuntime.from_spec(spec, tmp_path / "workspace" / ".bauer_runtime" / "logs" / "dispatcher.log")

    with patch.object(supervisor_module, "subprocess") as mock_subprocess, \
         patch.object(supervisor_module, "_no_console_window_kwargs", return_value={"creationflags": 999}):
        mock_subprocess.STDOUT = subprocess.STDOUT
        mock_subprocess.DEVNULL = subprocess.DEVNULL
        mock_subprocess.Popen.return_value = _fake_popen()

        supervisor._start_service(service)

        _, kwargs = mock_subprocess.Popen.call_args
        assert kwargs.get("creationflags") == 999


def test_runtime_status_json_cli_reads_state(tmp_path: Path):
    runner = CliRunner()
    workspace = tmp_path / "workspace"
    RuntimeStateStore(workspace).write(
        {
            "state": "running",
            "supervisor_pid": 0,
            "heartbeat_at": "2026-06-01T00:00:00+00:00",
            "services": [{"name": "dispatcher", "state": "running", "pid": 0, "alive": False}],
        }
    )

    result = runner.invoke(app, ["runtime", "status", "--workspace", str(workspace), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["state"] == "running"
    assert payload["services"][0]["name"] == "dispatcher"
