from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bauer.cli import app
from bauer.core.events import EventBus
from bauer.core.policy import ApprovalManager, PolicyEngine
from bauer.shell_runner import ShellRunner
from bauer.tool_router import ToolError, ToolRouter


def _router(workspace: Path, *, policy_root: Path, rules_path: Path | None = None) -> ToolRouter:
    return ToolRouter(
        workspace=workspace,
        shell_runner=ShellRunner(workspace=workspace, safe_mode=True, timeout=10),
        policy_enabled=True,
        policy_rules_path=rules_path,
        policy_root=policy_root,
        session_id="sess-1",
        run_id="run-1",
    )


def test_policy_engine_initial_rules():
    decision = PolicyEngine(workspace="workspace").evaluate("shell.execute", {"command": "git status"})

    assert decision.action == "ask"
    assert decision.risk_level == "high"
    assert "shell.execute.ask" in decision.matched_rules


def test_run_command_waits_for_approval_and_continues_after_approve(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy_root = tmp_path / "runtime"
    router = _router(workspace, policy_root=policy_root)
    command = f'"{sys.executable}" -c "print(123)"'

    with pytest.raises(ToolError, match="waiting_approval") as exc_info:
        router.execute({"action": "run_command", "args": {"command": command}})

    match = re.search(r"approval_id=(appr-[0-9a-f-]+)", str(exc_info.value))
    assert match is not None
    approval_id = match.group(1)
    pending = ApprovalManager(root=policy_root).get(approval_id)
    assert pending is not None
    assert pending.status == "pending"

    ApprovalManager(root=policy_root).approve(approval_id)
    result = router.execute({"action": "run_command", "args": {"command": command, "approval_id": approval_id}})

    assert "123" in result


def test_policy_denied_action_generates_event(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("secret", encoding="utf-8")
    policy_root = tmp_path / "runtime"
    rules = tmp_path / "policy.yaml"
    rules.write_text(
        "rules:\n"
        "  - id: filesystem.read.deny\n"
        "    operation: filesystem.read\n"
        "    action: deny\n"
        "    reason: no reads in this test\n",
        encoding="utf-8",
    )
    router = _router(workspace, policy_root=policy_root, rules_path=rules)

    with pytest.raises(ToolError, match="policy denied"):
        router.execute({"action": "read_file", "args": {"path": "file.txt"}})

    events = EventBus(root=workspace.parent / "runtime").list_events(run_id="run-1")
    event_types = [event.event_type for event in events]
    assert "policy.evaluated" in event_types
    assert "approval.denied" in event_types


def test_approvals_cli_approve_and_deny(tmp_path):
    manager = ApprovalManager(root=tmp_path)
    first = manager.request(operation="shell.execute", tool_name="run_command", reason="test", risk_level="high")
    second = manager.request(operation="filesystem.delete", tool_name="delete_file", reason="test", risk_level="high")
    runner = CliRunner()

    result = runner.invoke(app, ["approvals", "list", "--state-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "pending" in result.output

    approved = runner.invoke(app, ["approvals", "approve", first.id, "--state-dir", str(tmp_path)])
    denied = runner.invoke(app, ["approvals", "deny", second.id, "--state-dir", str(tmp_path)])

    assert approved.exit_code == 0
    assert denied.exit_code == 0
    assert ApprovalManager(root=tmp_path).get(first.id).status == "approved"  # type: ignore[union-attr]
    assert ApprovalManager(root=tmp_path).get(second.id).status == "denied"  # type: ignore[union-attr]
