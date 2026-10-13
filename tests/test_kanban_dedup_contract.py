"""A escrita Kanban deve executar novamente e invalidar o cache de leitura."""

from __future__ import annotations

import pytest

from bauer.tool_dedup import ToolCallDeduper
from bauer.tool_router import ToolRouter


@pytest.mark.parametrize("action", ["create", "complete", "block", "unblock", "heartbeat", "comment", "link"])
def test_every_kanban_write_action_is_never_replayed(action):
    deduper = ToolCallDeduper()
    args = {"action": action, "task_id": "T0001"}
    deduper.record("kanban_read", {"mode": "list"}, "old board")
    deduper.record("kanban_write", args, "updated board")
    assert deduper.check("kanban_write", args) is None
    assert deduper.check("kanban_read", {"mode": "list"}) is None


def test_real_create_refreshes_read_and_repeated_create_is_executed(tmp_path):
    router = ToolRouter(workspace=tmp_path, audit_enabled=False)
    deduper = ToolCallDeduper()
    read_args = {"mode": "list"}
    deduper.record("kanban_read", read_args, router.execute_native_call("kanban_read", read_args))
    create_args = {"action": "create", "title": "task"}
    for _ in range(2):
        assert deduper.check("kanban_write", create_args) is None
        result = router.execute_native_call("kanban_write", create_args)
        deduper.record("kanban_write", create_args, result)
        assert deduper.check("kanban_read", read_args) is None
        deduper.record("kanban_read", read_args, router.execute_native_call("kanban_read", read_args))
    board = router.execute_native_call("kanban_read", read_args)
    assert "2 tarefa(s)" in board


def test_tests_gate_recognizes_kanban_write_without_git(tmp_path):
    from bauer.core.kernel.gates.tests import houve_mudanca

    assert houve_mudanca(tmp_path, {"tools_usadas": ["kanban_write"]})
