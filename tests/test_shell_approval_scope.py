"""A consumed human decision grants only exact argv in the executing context."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from bauer.shell_runner import BlockedCommandError, SafeModeError, ShellRunner


def test_approved_call_is_exact_thread_local_and_always_cleared(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
    runner = ShellRunner(workspace=tmp_path)
    command = "playwright --version"
    with pytest.raises(ValueError):
        with runner.approved_call(command):
            assert runner.validate(command) == ["playwright", "--version"]
            with pytest.raises(BlockedCommandError):
                runner.validate("playwright install")
            with ThreadPoolExecutor() as pool:
                with pytest.raises(BlockedCommandError):
                    pool.submit(runner.validate, command).result()
            raise ValueError("tool failed")
    with pytest.raises(BlockedCommandError):
        runner.validate(command)
    assert "playwright" not in runner._runtime_allowed


def test_approved_call_cannot_override_denylist(tmp_path):
    runner = ShellRunner(workspace=tmp_path)
    with runner.approved_call("shutdown now"):
        with pytest.raises(BlockedCommandError, match="denylist"):
            runner.validate("shutdown now", confirm=True)
    with runner.approved_call("git push origin main"):
        with pytest.raises(SafeModeError):
            runner.validate("git push origin main")


@pytest.mark.parametrize("action", ["allow", "deny"])
def test_allowlist_requires_human_even_with_allow_policy_and_never_overrides_deny(tmp_path, monkeypatch, action):
    from bauer.core.policy import ApprovalManager
    from bauer.tool_router import ToolError, ToolRouter

    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
    rules = tmp_path / "policy.yaml"
    rules.write_text(
        f"rules:\n  - id: shell.rule\n    operation: shell.execute\n    action: {action}\n",
        encoding="utf-8",
    )
    root = tmp_path / "runtime"
    router = ToolRouter(
        workspace=tmp_path, shell_runner=ShellRunner(workspace=tmp_path),
        policy_enabled=True, policy_root=root,
    )
    router._policy_mode = "audit"
    router._policy_rules_path = rules
    expected = "policy denied" if action == "deny" else "waiting_approval"
    with pytest.raises(ToolError, match=expected):
        router.execute({"action": "run_command", "args": {"command": "playwright --version"}})
    assert len(ApprovalManager(root=root).list("pending")) == (0 if action == "deny" else 1)
