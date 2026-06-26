"""Testes do task ledger (P2.2) — _ledger_block injeta TASKS.md pendentes no contexto."""

from __future__ import annotations

from bauer.agent import _ledger_block
from bauer.workspace_manager import WorkspaceManager


def test_ledger_none_workspace():
    """Sem workspace -> retorna string vazia."""
    assert _ledger_block(None) == ""


def test_ledger_no_tasks_file(tmp_path):
    """Sem TASKS.md -> retorna string vazia."""
    assert _ledger_block(str(tmp_path)) == ""


def test_ledger_all_done(tmp_path):
    """Só tarefas DONE -> sem pendentes -> string vazia."""
    wm = WorkspaceManager(str(tmp_path))
    wm.init_project("Projeto")
    wm.add_task("Tarefa concluida", status="READY")
    wm.update_task_status("001", "DONE")
    block = _ledger_block(str(tmp_path))
    assert block == ""


def test_ledger_pending_tasks_aparecem(tmp_path):
    """Tarefas TODO/READY/IN_PROGRESS aparecem no bloco."""
    wm = WorkspaceManager(str(tmp_path))
    wm.init_project("Projeto")
    wm.add_task("Implementar login", status="READY")
    wm.add_task("Escrever testes", status="TODO")

    block = _ledger_block(str(tmp_path))
    assert "Task Ledger" in block
    assert "Implementar login" in block
    assert "Escrever testes" in block


def test_ledger_excludes_done_tasks(tmp_path):
    """Tarefas DONE não aparecem no bloco."""
    wm = WorkspaceManager(str(tmp_path))
    wm.init_project("Projeto")
    wm.add_task("Tarefa ativa", status="READY")
    wm.add_task("Tarefa antiga", status="READY")
    wm.update_task_status("002", "DONE")

    block = _ledger_block(str(tmp_path))
    assert "Tarefa ativa" in block
    assert "Tarefa antiga" not in block


def test_ledger_in_progress_incluido(tmp_path):
    """Tarefas IN_PROGRESS aparecem no bloco."""
    wm = WorkspaceManager(str(tmp_path))
    wm.init_project("Projeto")
    wm.add_task("Em andamento", status="READY")
    wm.update_task_status("001", "IN_PROGRESS")

    block = _ledger_block(str(tmp_path))
    assert "IN_PROGRESS" in block
    assert "Em andamento" in block


def test_ledger_blocked_incluido(tmp_path):
    """Tarefas BLOCKED também aparecem (ainda pendentes)."""
    wm = WorkspaceManager(str(tmp_path))
    wm.init_project("Projeto")
    wm.add_task("Bloqueada", status="READY")
    wm.update_task_status("001", "BLOCKED")

    block = _ledger_block(str(tmp_path))
    assert "BLOCKED" in block
    assert "Bloqueada" in block


def test_ledger_includes_update_hint(tmp_path):
    """Bloco inclui dica para atualizar via tools kanban."""
    wm = WorkspaceManager(str(tmp_path))
    wm.init_project("Projeto")
    wm.add_task("Tarefa X", status="READY")

    block = _ledger_block(str(tmp_path))
    assert "kanban" in block.lower() or "update_task" in block
