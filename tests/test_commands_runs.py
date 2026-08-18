"""Testes para `bauer runs events`, apos absorver `bauer events tail` (2026-08-18).

`bauer events tail` era um comando de topo quase identico a
`bauer runs events` (mesma EventBus, mesmo state_dir) — --follow sem
filtro por run de um lado, filtro por run sem --follow do outro. Fundidos
num so: `runs events` ganhou run_id opcional + --limit/--follow/--interval.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer.cli import app
from bauer.core.events import EventBus

runner = CliRunner()


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "runtime"


def test_events_nao_e_mais_comando_de_topo():
    result = runner.invoke(app, ["events", "--help"])
    assert result.exit_code != 0


def test_runs_events_sem_run_id_mostra_todas(state_dir: Path):
    bus = EventBus(root=state_dir)
    bus.publish("run.started", run_id="run_a")
    bus.publish("run.started", run_id="run_b")

    result = runner.invoke(app, ["runs", "events", "--state-dir", str(state_dir)])

    assert result.exit_code == 0
    # console.print(json.dumps(...)) do Rich quebra a linha pelo width do
    # terminal — testa por substring, não por linha inteira.
    assert '"run_id": "run_a"' in result.stdout
    assert '"run_id": "run_b"' in result.stdout


def test_runs_events_com_run_id_filtra(state_dir: Path):
    bus = EventBus(root=state_dir)
    bus.publish("run.started", run_id="run_a")
    bus.publish("run.started", run_id="run_b")

    result = runner.invoke(app, ["runs", "events", "run_a", "--state-dir", str(state_dir)])

    assert result.exit_code == 0
    assert '"run_id": "run_a"' in result.stdout
    assert '"run_id": "run_b"' not in result.stdout


def test_runs_events_run_id_inexistente_mostra_aviso(state_dir: Path):
    EventBus(root=state_dir).publish("run.started", run_id="run_a")

    result = runner.invoke(app, ["runs", "events", "run_z", "--state-dir", str(state_dir)])

    assert result.exit_code == 0
    assert "Nenhum evento" in result.stdout


def test_runs_events_sem_eventos_mostra_aviso(state_dir: Path):
    result = runner.invoke(app, ["runs", "events", "--state-dir", str(state_dir)])

    assert result.exit_code == 0
    assert "Nenhum evento" in result.stdout
