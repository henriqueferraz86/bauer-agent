"""Integration tests for Wave 3 CLI: kanban-specify / decompose / swarm."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer import kanban_db as kb
from bauer.cli import app
from bauer.kanban_decompose import DecomposeOutcome
from bauer.kanban_specify import SpecifyOutcome
from bauer.kanban_swarm import SwarmCreated


@pytest.fixture
def bauer_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))
    monkeypatch.delenv("BAUER_KANBAN_BOARD", raising=False)
    return tmp_path / "bauer-home"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# kanban-specify
# ---------------------------------------------------------------------------


def test_specify_happy(bauer_home: Path, runner: CliRunner):
    """Happy path: triage task gets specified, CLI exits 0 with body shown."""
    with kb.connect() as conn:
        kb.init_db(conn)
        tid = kb.create_task(conn, "rough idea", status="triage")

    outcome = SpecifyOutcome(
        task_id=tid, ok=True,
        title="Spec'd title",
        body="**Goal**\n...\n",
    )
    with patch("bauer.kanban_specify.specify_task", return_value=outcome):
        r = runner.invoke(app, ["kanban-specify", tid])
    assert r.exit_code == 0
    assert "Spec'd title" in r.stdout
    assert "**Goal**" in r.stdout


def test_specify_not_triage_warns(bauer_home: Path, runner: CliRunner):
    """Already-specified task: CLI prints a hint, exits 0."""
    outcome = SpecifyOutcome(task_id="042", ok=True, reason="not_triage")
    with patch("bauer.kanban_specify.specify_task", return_value=outcome):
        r = runner.invoke(app, ["kanban-specify", "042"])
    assert r.exit_code == 0
    assert "ja foi specifield" in r.stdout or "anteriormente" in r.stdout


def test_specify_failure_exits_nonzero(bauer_home: Path, runner: CliRunner):
    outcome = SpecifyOutcome(task_id="042", ok=False,
                              reason="auxiliary_unavailable")
    with patch("bauer.kanban_specify.specify_task", return_value=outcome):
        r = runner.invoke(app, ["kanban-specify", "042"])
    assert r.exit_code != 0
    assert "auxiliary_unavailable" in r.stdout


# ---------------------------------------------------------------------------
# kanban-decompose
# ---------------------------------------------------------------------------


def test_decompose_fanout_lists_children(bauer_home: Path, runner: CliRunner):
    """Successful decompose prints a child table."""
    with kb.connect() as conn:
        kb.init_db(conn)
        root = kb.create_task(conn, "parent", status="triage")
        c1 = kb.create_task(conn, "child A", status="todo")
        c2 = kb.create_task(conn, "child B", status="todo")
        c3 = kb.create_task(conn, "child C", status="todo")

    outcome = DecomposeOutcome(
        task_id=root, ok=True, fanout=True,
        child_ids=[c1, c2, c3],
        rationale="logical fan-out",
    )
    with patch("bauer.kanban_decompose.decompose_task", return_value=outcome):
        r = runner.invoke(app, ["kanban-decompose", root])
    assert r.exit_code == 0
    assert "child A" in r.stdout
    assert "child B" in r.stdout
    assert "logical fan-out" in r.stdout


def test_decompose_atomic_promotes(bauer_home: Path, runner: CliRunner):
    """fanout=false: CLI explains the task was promoted in place."""
    outcome = DecomposeOutcome(
        task_id="042", ok=True, fanout=False,
        rationale="single team, no real subtasks",
    )
    with patch("bauer.kanban_decompose.decompose_task", return_value=outcome):
        r = runner.invoke(app, ["kanban-decompose", "042"])
    assert r.exit_code == 0
    assert "atomica" in r.stdout.lower() or "promovida" in r.stdout.lower()


def test_decompose_failure_exits_nonzero(bauer_home: Path, runner: CliRunner):
    outcome = DecomposeOutcome(
        task_id="042", ok=False,
        reason="auxiliary_unavailable",
    )
    with patch("bauer.kanban_decompose.decompose_task", return_value=outcome):
        r = runner.invoke(app, ["kanban-decompose", "042"])
    assert r.exit_code != 0
    assert "auxiliary_unavailable" in r.stdout


# ---------------------------------------------------------------------------
# kanban-swarm
# ---------------------------------------------------------------------------


def test_swarm_create_prints_id_table(bauer_home: Path, runner: CliRunner):
    """Successful swarm prints role / id table for every member."""
    r = runner.invoke(app, [
        "kanban-swarm", "Implement OAuth",
        "--worker", "Auth API",
        "--worker", "Login UI",
        "--worker", "Tests",
    ])
    assert r.exit_code == 0
    # Output includes the role labels
    assert "Worker 1" in r.stdout
    assert "Worker 2" in r.stdout
    assert "Worker 3" in r.stdout
    assert "Verifier" in r.stdout
    assert "Synthesizer" in r.stdout


def test_swarm_requires_at_least_one_worker(bauer_home: Path, runner: CliRunner):
    """No --worker → typer error (missing required option)."""
    r = runner.invoke(app, ["kanban-swarm", "Goal"])
    assert r.exit_code != 0


def test_swarm_too_many_workers_clean_error(bauer_home: Path, runner: CliRunner):
    """create_swarm raises ValueError → CLI prints the message and exits 1."""
    too_many_args = []
    for i in range(15):
        too_many_args.extend(["--worker", f"worker {i}"])
    r = runner.invoke(app, ["kanban-swarm", "Goal", *too_many_args])
    assert r.exit_code != 0
    assert "workers" in r.stdout.lower() or "erro" in r.stdout.lower()


def test_swarm_status_shows_blackboard(bauer_home: Path, runner: CliRunner):
    """kanban-swarm-status prints blackboard entries."""
    from bauer.kanban_swarm import (
        create_swarm,
        post_blackboard_update,
    )
    swarm = create_swarm("g", workers=["a", "b"])
    post_blackboard_update(swarm.root_id, key="api", value="v1")

    r = runner.invoke(app, ["kanban-swarm-status", swarm.root_id])
    assert r.exit_code == 0
    assert "Blackboard" in r.stdout
    assert "api" in r.stdout
    assert "v1" in r.stdout


def test_swarm_status_non_swarm_exits_nonzero(bauer_home: Path, runner: CliRunner):
    """Calling status on a plain task surfaces a clean error."""
    with kb.connect() as conn:
        kb.init_db(conn)
        tid = kb.create_task(conn, "plain", status="todo")
    r = runner.invoke(app, ["kanban-swarm-status", tid])
    assert r.exit_code != 0
