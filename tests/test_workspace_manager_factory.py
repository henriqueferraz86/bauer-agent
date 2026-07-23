"""Testes do switch único de backend de task-store (achado #10)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from bauer.workspace_manager import WorkspaceManager
from bauer.workspace_manager_factory import get_workspace_manager, resolve_task_backend
from bauer.workspace_manager_sqlite import WorkspaceManagerSqlite


@pytest.fixture
def bauer_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "bauer-home"))
    monkeypatch.delenv("BAUER_KANBAN_BOARD", raising=False)
    return tmp_path / "bauer-home"


# ── resolve_task_backend ──────────────────────────────────────────────────────


def test_default_is_markdown_conservador():
    # Sem config utilizável, o default nunca vira sqlite silenciosamente.
    with patch("bauer.config_loader.load_config", side_effect=RuntimeError("boom")):
        assert resolve_task_backend() == "markdown"


def test_explicit_override_wins():
    assert resolve_task_backend("sqlite") == "sqlite"
    assert resolve_task_backend("markdown") == "markdown"


def test_unknown_value_falls_back_to_markdown():
    # Valor inesperado não escolhe o backend novo por acidente.
    assert resolve_task_backend("postgres") == "markdown"


def test_reads_config_when_no_override():
    class _Cfg:
        class agent:
            task_backend = "sqlite"

    with patch("bauer.config_loader.load_config", return_value=_Cfg()):
        assert resolve_task_backend() == "sqlite"


# ── get_workspace_manager ─────────────────────────────────────────────────────


def test_returns_markdown_manager_by_default(tmp_path):
    wm = get_workspace_manager(tmp_path / "ws", backend="markdown")
    assert isinstance(wm, WorkspaceManager)


def test_returns_sqlite_manager_when_selected(tmp_path, bauer_home):
    wm = get_workspace_manager(tmp_path / "ws", backend="sqlite")
    assert isinstance(wm, WorkspaceManagerSqlite)


def test_sqlite_kwargs_are_forwarded(tmp_path, bauer_home):
    wm = get_workspace_manager(tmp_path / "ws", backend="sqlite",
                               board="outro", regenerate_view=False)
    assert isinstance(wm, WorkspaceManagerSqlite)
    assert wm._board == "outro"
    assert wm._regenerate_view is False


def test_markdown_ignores_sqlite_only_kwargs(tmp_path):
    # Call sites compartilhados podem passar kwargs do sqlite; o markdown não
    # pode explodir por causa disso.
    wm = get_workspace_manager(tmp_path / "ws", backend="markdown",
                               board="x", regenerate_view=False)
    assert isinstance(wm, WorkspaceManager)


def test_both_backends_expose_same_api(tmp_path, bauer_home):
    md = get_workspace_manager(tmp_path / "a", backend="markdown")
    sql = get_workspace_manager(tmp_path / "b", backend="sqlite")
    for method in ("init_project", "add_task", "list_tasks", "get_task",
                   "update_task_status", "update_task_metadata",
                   "add_task_comment", "get_project_info"):
        assert callable(getattr(md, method)), method
        assert callable(getattr(sql, method)), method
