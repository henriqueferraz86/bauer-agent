"""Integration tests for `bauer boards *` and `bauer kanban-migrate` CLI commands.

These hit the real Typer app via the CliRunner and exercise the kanban_db
backend end-to-end. They confirm the wiring between cli.py, kanban_db.py,
and kanban_migration.py is correct — unit tests for each module are in
their own files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer import kanban_db as kb
from bauer.cli import app


@pytest.fixture
def bauer_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated BAUER_HOME so tests don't touch the user's real boards."""
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))
    monkeypatch.delenv("BAUER_KANBAN_BOARD", raising=False)
    return tmp_path / "bauer-home"


@pytest.fixture
def runner() -> CliRunner:
    """Typer CliRunner — combined stdout+stderr in result.stdout in 0.17+."""
    return CliRunner()


# ---------------------------------------------------------------------------
# boards create / list / switch / show / rm
# ---------------------------------------------------------------------------


def test_boards_create_and_list(bauer_home: Path, runner: CliRunner):
    r1 = runner.invoke(app, ["boards", "create", "alpha"])
    assert r1.exit_code == 0
    assert "alpha" in r1.stdout

    r2 = runner.invoke(app, ["boards", "create", "beta"])
    assert r2.exit_code == 0

    r3 = runner.invoke(app, ["boards", "list"])
    assert r3.exit_code == 0
    assert "alpha" in r3.stdout
    assert "beta" in r3.stdout


def test_boards_create_with_activate_flips_pointer(bauer_home: Path, runner: CliRunner):
    r = runner.invoke(app, ["boards", "create", "alpha", "--activate"])
    assert r.exit_code == 0
    assert kb.get_active_board() == "alpha"


def test_boards_create_empty_name_rejected(bauer_home: Path, runner: CliRunner):
    """Empty name should produce a non-zero exit and helpful message."""
    r = runner.invoke(app, ["boards", "create", "   "])
    assert r.exit_code != 0


def test_boards_switch_activates_existing(bauer_home: Path, runner: CliRunner):
    runner.invoke(app, ["boards", "create", "alpha"])
    runner.invoke(app, ["boards", "create", "beta"])
    r = runner.invoke(app, ["boards", "switch", "beta"])
    assert r.exit_code == 0
    assert kb.get_active_board() == "beta"


def test_boards_switch_missing_board_fails(bauer_home: Path, runner: CliRunner):
    r = runner.invoke(app, ["boards", "switch", "ghost"])
    assert r.exit_code != 0
    assert "ghost" in r.stdout or "nao existe" in r.stdout


def test_boards_show_empty_board(bauer_home: Path, runner: CliRunner):
    runner.invoke(app, ["boards", "create", "alpha"])
    r = runner.invoke(app, ["boards", "show", "alpha"])
    assert r.exit_code == 0
    assert "alpha" in r.stdout


def test_boards_show_with_tasks(bauer_home: Path, runner: CliRunner):
    """Tasks created via kanban_db show up in `boards show`."""
    runner.invoke(app, ["boards", "create", "alpha"])
    with kb.connect("alpha") as conn:
        kb.create_task(conn, "First task", priority="high")
        kb.create_task(conn, "Second task")

    r = runner.invoke(app, ["boards", "show", "alpha"])
    assert r.exit_code == 0
    assert "First task" in r.stdout
    assert "Second task" in r.stdout


def test_boards_show_uses_active_by_default(bauer_home: Path, runner: CliRunner):
    """`boards show` (no arg) shows the active board."""
    runner.invoke(app, ["boards", "create", "alpha", "--activate"])
    with kb.connect("alpha") as conn:
        kb.create_task(conn, "Active task")

    r = runner.invoke(app, ["boards", "show"])
    assert r.exit_code == 0
    assert "Active task" in r.stdout


def test_boards_rm_removes_db_file(bauer_home: Path, runner: CliRunner):
    runner.invoke(app, ["boards", "create", "alpha"])
    db_path = kb.board_path("alpha")
    assert db_path.exists()

    r = runner.invoke(app, ["boards", "rm", "alpha", "--force"])
    assert r.exit_code == 0
    assert not db_path.exists()


def test_boards_rm_active_board_clears_pointer(bauer_home: Path, runner: CliRunner):
    runner.invoke(app, ["boards", "create", "alpha", "--activate"])
    assert kb.get_active_board() == "alpha"

    r = runner.invoke(app, ["boards", "rm", "alpha", "--force"])
    assert r.exit_code == 0
    # Pointer marker is removed → next call falls back to 'default'.
    assert kb.get_active_board() == "default"


def test_boards_rm_missing_board(bauer_home: Path, runner: CliRunner):
    r = runner.invoke(app, ["boards", "rm", "ghost", "--force"])
    assert r.exit_code != 0


# ---------------------------------------------------------------------------
# kanban-migrate
# ---------------------------------------------------------------------------


def _write_sample_tasks_md(ws: Path) -> Path:
    """Drop a TASKS.md with two simple entries inside the workspace dir."""
    ws.mkdir(parents=True, exist_ok=True)
    md = ws / "TASKS.md"
    md.write_text(
        "## [TODO] First\n"
        "id: 001\n"
        "priority: high\n"
        "---\n"
        "## [DONE] Second\n"
        "id: 002\n"
        "priority: medium\n"
        "---\n",
        encoding="utf-8",
    )
    return md


def test_kanban_migrate_dry_run(bauer_home: Path, runner: CliRunner, tmp_path: Path):
    """Dry-run shows the table but doesn't touch the SQLite store."""
    ws = tmp_path / "ws-dry"
    _write_sample_tasks_md(ws)
    r = runner.invoke(app, [
        "kanban-migrate",
        "--workspace", str(ws),
        "--board", "alpha",
        "--dry-run",
    ])
    assert r.exit_code == 0
    assert "001" in r.stdout
    assert "002" in r.stdout
    # No SQLite writes happened.
    with kb.connect("alpha") as conn:
        kb.init_db(conn)
        assert kb.get_task_or_none(conn, "001") is None


def test_kanban_migrate_writes_to_sqlite(bauer_home: Path, runner: CliRunner,
                                          tmp_path: Path):
    ws = tmp_path / "ws-real"
    _write_sample_tasks_md(ws)
    r = runner.invoke(app, [
        "kanban-migrate",
        "--workspace", str(ws),
        "--board", "alpha",
    ])
    assert r.exit_code == 0
    assert "Migracao" in r.stdout or "inserted" in r.stdout

    with kb.connect("alpha") as conn:
        t1 = kb.get_task(conn, "001")
        t2 = kb.get_task(conn, "002")
        assert t1.title == "First"
        assert t2.title == "Second"


def test_kanban_migrate_missing_file_fails(bauer_home: Path, runner: CliRunner,
                                            tmp_path: Path):
    ws = tmp_path / "ws-empty"
    ws.mkdir(parents=True, exist_ok=True)
    r = runner.invoke(app, [
        "kanban-migrate",
        "--workspace", str(ws),
    ])
    assert r.exit_code != 0
    assert "nao encontrado" in r.stdout or "TASKS.md" in r.stdout


def test_kanban_migrate_idempotent(bauer_home: Path, runner: CliRunner,
                                    tmp_path: Path):
    """Running migrate twice doesn't duplicate tasks."""
    ws = tmp_path / "ws"
    _write_sample_tasks_md(ws)
    runner.invoke(app, ["kanban-migrate", "--workspace", str(ws), "--board", "alpha"])
    runner.invoke(app, ["kanban-migrate", "--workspace", str(ws), "--board", "alpha"])

    with kb.connect("alpha") as conn:
        tasks = kb.list_tasks(conn)
        ids = sorted(t.id for t in tasks)
        assert ids == ["001", "002"]   # exactly 2, not 4


def test_kanban_migrate_dry_run_activate_has_no_side_effects(
    bauer_home: Path, runner: CliRunner, tmp_path: Path,
):
    """Previewing activation neither creates the workspace board nor a backup."""
    from bauer.workspace_manager_factory import board_for_workspace

    ws = tmp_path / "ws-dry-activate"
    _write_sample_tasks_md(ws)
    config = ws / "config.yaml"
    config.write_text("agent:\n  task_backend: markdown\n", encoding="utf-8")
    board = board_for_workspace(ws)
    assert not kb.board_path(board).exists()

    result = runner.invoke(app, [
        "kanban-migrate", "--workspace", str(ws), "--activate", "--dry-run",
        "--config", str(config),
    ])

    assert result.exit_code == 0
    assert "ativaria" in result.stdout
    assert not kb.board_path(board).exists()
    assert config.read_text(encoding="utf-8") == "agent:\n  task_backend: markdown\n"
    assert not list(ws.glob("*.before-sqlite.bak*"))


def test_kanban_migrate_activate_backs_up_and_sets_explicit_config(
    bauer_home: Path, runner: CliRunner, tmp_path: Path,
):
    from bauer.workspace_manager_factory import board_for_workspace

    ws = tmp_path / "ws-activate"
    _write_sample_tasks_md(ws)
    config = ws / "chosen-config.yaml"
    original = "model:\n  provider: ollama\nagent:\n  task_backend: markdown\n"
    config.write_text(original, encoding="utf-8")
    board = board_for_workspace(ws)

    result = runner.invoke(app, [
        "kanban-migrate", "--workspace", str(ws), "--activate", "--config", str(config),
    ])

    assert result.exit_code == 0, result.stdout
    assert "SQLite ativado" in result.stdout
    assert config.read_text(encoding="utf-8").find("task_backend: sqlite") >= 0
    assert (ws / "TASKS.md.before-sqlite.bak").read_text(encoding="utf-8") == (
        ws / "TASKS.md"
    ).read_text(encoding="utf-8")
    assert config.with_name("chosen-config.yaml.before-sqlite.bak").read_text(
        encoding="utf-8"
    ) == original
    with kb.connect(board) as conn:
        assert {task.id for task in kb.list_tasks(conn)} == {"001", "002"}

    repeat = runner.invoke(app, [
        "kanban-migrate", "--workspace", str(ws), "--activate", "--config", str(config),
    ])
    assert repeat.exit_code == 0, repeat.stdout
    assert config.with_name("chosen-config.yaml.before-sqlite.bak").read_text(
        encoding="utf-8"
    ) == original
    assert config.with_name("chosen-config.yaml.before-sqlite.bak.1").exists()


def test_kanban_migrate_activate_fails_without_config_and_preserves_markdown(
    bauer_home: Path, runner: CliRunner, tmp_path: Path,
):
    ws = tmp_path / "ws-no-config"
    source = _write_sample_tasks_md(ws)
    config = ws / "missing.yaml"

    result = runner.invoke(app, [
        "kanban-migrate", "--workspace", str(ws), "--activate", "--config", str(config),
    ])

    assert result.exit_code != 0
    assert "Nada foi alterado" in result.stdout
    assert source.exists()
    assert not config.exists()
    assert not list(ws.glob("*.before-sqlite.bak*"))
