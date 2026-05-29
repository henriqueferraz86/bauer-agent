"""Testes do WorkspaceManager (Fase 6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.workspace_manager import Task, WorkspaceError, WorkspaceManager


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def wm(ws: Path) -> WorkspaceManager:
    return WorkspaceManager(ws)


# --- init_project -----------------------------------------------------------


def test_init_creates_project_and_tasks(wm: WorkspaceManager, ws: Path):
    created = wm.init_project("MeuApp", "Descricao do app")
    assert (ws / "PROJECT.md").exists()
    assert (ws / "TASKS.md").exists()
    assert len(created) == 2


def test_init_does_not_overwrite_existing(wm: WorkspaceManager, ws: Path):
    wm.init_project("Primeiro")
    (ws / "PROJECT.md").write_text("conteudo customizado", encoding="utf-8")
    wm.init_project("Segundo")
    content = (ws / "PROJECT.md").read_text(encoding="utf-8")
    assert "conteudo customizado" in content


def test_init_project_name_in_file(wm: WorkspaceManager, ws: Path):
    wm.init_project("BauerTest", "Projeto de teste")
    content = (ws / "PROJECT.md").read_text(encoding="utf-8")
    assert "BauerTest" in content
    assert "Projeto de teste" in content


def test_init_tasks_has_header(wm: WorkspaceManager, ws: Path):
    wm.init_project("X")
    content = (ws / "TASKS.md").read_text(encoding="utf-8")
    assert "TASKS.md" in content
    assert "TODO" in content


def test_init_creates_workspace_dir(tmp_path: Path):
    ws = tmp_path / "new" / "nested" / "workspace"
    wm = WorkspaceManager(ws)
    wm.init_project("Test")
    assert ws.is_dir()


# --- add_task ---------------------------------------------------------------


def test_add_task_creates_entry(wm: WorkspaceManager, ws: Path):
    wm.init_project("P")
    task = wm.add_task("Implementar login")
    assert task.id == "001"
    assert task.status == "TODO"
    assert task.title == "Implementar login"
    content = (ws / "TASKS.md").read_text(encoding="utf-8")
    assert "Implementar login" in content
    assert "id: 001" in content


def test_add_task_increments_id(wm: WorkspaceManager):
    wm.init_project("P")
    t1 = wm.add_task("Tarefa 1")
    t2 = wm.add_task("Tarefa 2")
    t3 = wm.add_task("Tarefa 3")
    assert t1.id == "001"
    assert t2.id == "002"
    assert t3.id == "003"


def test_add_task_with_description(wm: WorkspaceManager, ws: Path):
    wm.init_project("P")
    wm.add_task("Com desc", description="Descricao detalhada aqui")
    content = (ws / "TASKS.md").read_text(encoding="utf-8")
    assert "Descricao detalhada aqui" in content


def test_add_task_initializes_project_if_missing(ws: Path):
    wm = WorkspaceManager(ws)
    task = wm.add_task("Tarefa sem init")
    assert task.id == "001"
    assert (ws / "TASKS.md").exists()


# --- list_tasks -------------------------------------------------------------


def test_list_tasks_empty(wm: WorkspaceManager):
    wm.init_project("P")
    assert wm.list_tasks() == []


def test_list_tasks_returns_all(wm: WorkspaceManager):
    wm.init_project("P")
    wm.add_task("A")
    wm.add_task("B")
    wm.add_task("C")
    tasks = wm.list_tasks()
    assert len(tasks) == 3
    assert tasks[0].title == "A"
    assert tasks[1].title == "B"
    assert tasks[2].title == "C"


def test_list_tasks_ids_are_sequential(wm: WorkspaceManager):
    wm.init_project("P")
    for i in range(5):
        wm.add_task(f"Task {i}")
    tasks = wm.list_tasks()
    ids = [t.id for t in tasks]
    assert ids == ["001", "002", "003", "004", "005"]


def test_list_tasks_no_file_returns_empty(ws: Path):
    wm = WorkspaceManager(ws)
    assert wm.list_tasks() == []


# --- update_task_status -----------------------------------------------------


def test_update_todo_to_in_progress(wm: WorkspaceManager, ws: Path):
    wm.init_project("P")
    wm.add_task("Minha tarefa")
    updated = wm.update_task_status("001", "IN_PROGRESS")
    assert updated.status == "IN_PROGRESS"
    content = (ws / "TASKS.md").read_text(encoding="utf-8")
    assert "[IN_PROGRESS] Minha tarefa" in content
    assert "[TODO] Minha tarefa" not in content


def test_update_to_done(wm: WorkspaceManager):
    wm.init_project("P")
    wm.add_task("Fazer algo")
    updated = wm.update_task_status("1", "DONE")
    assert updated.status == "DONE"
    assert updated.id == "001"


def test_update_to_blocked(wm: WorkspaceManager):
    wm.init_project("P")
    wm.add_task("Tarefa bloqueada")
    updated = wm.update_task_status("001", "BLOCKED")
    assert updated.status == "BLOCKED"


def test_update_invalid_status_raises(wm: WorkspaceManager):
    wm.init_project("P")
    wm.add_task("T")
    with pytest.raises(WorkspaceError, match="invalido"):
        wm.update_task_status("001", "INVALID")


def test_update_nonexistent_task_raises(wm: WorkspaceManager):
    wm.init_project("P")
    with pytest.raises(WorkspaceError, match="nao encontrada"):
        wm.update_task_status("999", "DONE")


def test_update_preserves_title(wm: WorkspaceManager):
    wm.init_project("P")
    wm.add_task("Titulo Original")
    updated = wm.update_task_status("001", "DONE")
    assert updated.title == "Titulo Original"


def test_update_no_file_raises(ws: Path):
    wm = WorkspaceManager(ws)
    with pytest.raises(WorkspaceError, match="nao encontrado"):
        wm.update_task_status("001", "DONE")


def test_update_second_task_does_not_affect_first(wm: WorkspaceManager, ws: Path):
    wm.init_project("P")
    wm.add_task("Task A")
    wm.add_task("Task B")
    wm.update_task_status("002", "DONE")
    tasks = wm.list_tasks()
    assert tasks[0].status == "TODO"  # Task A unchanged
    assert tasks[1].status == "DONE"  # Task B updated


# --- get_project_info -------------------------------------------------------


def test_get_project_info_returns_content(wm: WorkspaceManager):
    wm.init_project("Bauer", "Agente local")
    info = wm.get_project_info()
    assert "Bauer" in info
    assert "Agente local" in info


def test_get_project_info_missing_returns_message(ws: Path):
    wm = WorkspaceManager(ws)
    info = wm.get_project_info()
    assert "nao encontrado" in info
