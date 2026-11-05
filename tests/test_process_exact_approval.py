"""A process start consumes one exact approval without learning its binary."""

from types import SimpleNamespace

import pytest

from bauer.core.events import EventBus
from bauer.core.kernel import BauerKernel
from bauer.core.policy import ApprovalManager
from bauer.core.runtime.run_manager import RunManager
from bauer.shell_runner import BlockedCommandError, ShellRunner, load_learned_commands
from bauer.tool_router import ToolError, ToolRouter


@pytest.fixture
def kit(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("bauer.shell_runner.shutil.which", lambda executable: executable)
    root = tmp_path / "runtime"
    bus = EventBus(root=root)
    runs = RunManager(root=root, event_bus=bus)
    approvals = ApprovalManager(root=root, event_bus=bus)
    kernel = BauerKernel(runs=runs, bus=bus, approvals=approvals)
    run = runs.create_run(session_id="session", status="running")
    runner = ShellRunner(workspace=tmp_path, safe_mode=False)
    router = ToolRouter(
        workspace=tmp_path, shell_runner=runner, policy_enabled=True,
        policy_root=root, run_id=run.id, session_id=run.session_id,
    )
    starts = []

    def popen(argv, **kwargs):
        starts.append(argv)
        return SimpleNamespace(pid=123, stdout=iter(()), stderr=iter(()))

    monkeypatch.setattr("bauer.tools.agent_misc.controlled_popen", popen)
    return router, runner, approvals, kernel, run, starts


def test_approved_process_start_is_single_use_and_does_not_learn_allowlist(kit):
    router, runner, approvals, kernel, run, starts = kit
    args = {"action": "start", "command": "curl --version"}
    with pytest.raises(BlockedCommandError):
        runner.validate(args["command"])
    record = approvals.request(
        operation="shell.execute", tool_name="process", reason="human decision",
        risk_level="high", payload=args, run_id=run.id, session_id=run.session_id,
        suspend_run=True,
    )
    kernel.approve(record.id)
    approved_args = {**args, "approval_id": record.id}
    result = router.execute({"action": "process", "args": approved_args})
    assert "Iniciado" in result
    assert starts == [["curl", "--version"]]
    assert approvals.get(record.id).consumed_at
    assert "curl" not in load_learned_commands()
    with pytest.raises(BlockedCommandError):
        runner.validate(args["command"])
    with pytest.raises(ToolError, match="already consumed"):
        router.execute({"action": "process", "args": approved_args})
    with pytest.raises(ToolError, match="waiting_approval"):
        router.execute({"action": "process", "args": args})
    pending = approvals.list("pending")
    assert len(pending) == 1 and pending[0].id != record.id
    assert len(starts) == 1


@pytest.mark.parametrize("action", ["start", " START "])
def test_process_denylist_blocks_before_request_even_with_approval_id(kit, action):
    router, _, approvals, _, _, starts = kit
    with pytest.raises(ToolError, match="BLOCKED"):
        router.execute({"action": "process", "args": {
            "action": action, "command": "rm -rf /", "approval_id": "untrusted-id",
        }})
    assert approvals.list() == []
    assert starts == []
