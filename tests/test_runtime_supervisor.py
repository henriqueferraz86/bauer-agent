from __future__ import annotations

import json
from pathlib import Path

import pytest
pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer.cli import app
from bauer.supervisor import RuntimeStateStore, RuntimeSupervisor


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
