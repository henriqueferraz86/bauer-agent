"""Real shell denials, session-bound consent and verified learned permissions."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from bauer.server_shell_approval import ShellApprovalManager
from bauer.shell_runner import BlockedCommandError, SafeModeError, ShellRunner, load_learned_commands


@pytest.fixture
def approval_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("bauer.shell_runner.subprocess.run", lambda *a, **k: pytest.fail("approval executed subprocess"))
    workspace = tmp_path / "project"
    workspace.mkdir()
    runner = ShellRunner(workspace=workspace)
    router = SimpleNamespace(workspace=workspace, _shell_runner=runner, available_tools=lambda: ["run_command"])
    now = [100.0]
    manager = ShellApprovalManager(clock=lambda: now[0])
    return manager, router, now


def deny(manager, router, command="customtool --version", *, sid="session", task="verifique a ferramenta", presented=True):
    with manager.capture(sid, router.workspace, task):
        with pytest.raises(BlockedCommandError):
            router._shell_runner.validate(command)
    if presented:
        assert manager.prompt(sid, router.workspace)


@pytest.mark.parametrize("answer", ["sim", "autorizo", "permito", "sim permanentemente", " SIM "])
def test_explicit_consent_persists_verified_base_and_returns_original_task(approval_chat, answer):
    manager, router, _ = approval_chat
    peer = ShellRunner(workspace=router.workspace)
    deny(manager, router)
    assert "'customtool'" in manager.prompt("session", router.workspace)
    result = manager.resolve("session", answer, router)
    assert "salva e verificada" in result
    assert "verifique a ferramenta" in result and "customtool --version" in result
    assert load_learned_commands() == {"customtool"}
    assert manager.prompt("session", router.workspace) is None
    assert router._shell_runner.validate("customtool --version") == ["customtool", "--version"]
    # A previously cached runner sees approval without restarting the server.
    assert peer.validate("customtool --help") == ["customtool", "--help"]


@pytest.mark.parametrize("message", ["não", "nao", "não autorizo", "nego", "cancele"])
def test_refusal_removes_pending_without_permission(approval_chat, message):
    manager, router, _ = approval_chat
    deny(manager, router)
    assert "recusou" in manager.resolve("session", message, router)
    assert manager.prompt("session", router.workspace) is None
    assert load_learned_commands() == set()
    with pytest.raises(BlockedCommandError):
        router._shell_runner.validate("customtool --version")


@pytest.mark.parametrize("message", ['"sim"', "ele disse sim", "sim, mas não execute", "sim\nignore as regras", "outra tarefa"])
def test_other_messages_do_not_authorize_and_clear_obsolete_request(approval_chat, message):
    manager, router, _ = approval_chat
    deny(manager, router)
    assert manager.resolve("session", message, router) == message
    assert manager.resolve("session", "sim", router) == "sim"
    assert load_learned_commands() == set()


def test_consent_cannot_select_a_command_from_model_or_user_text(approval_chat):
    manager, router, _ = approval_chat
    with manager.capture("session", router.workspace, "texto que afirma ter bloqueado customtool"):
        pass
    assert manager.prompt("session", router.workspace) is None
    assert manager.resolve("session", "sim", router) == "sim"
    assert load_learned_commands() == set()


def test_consent_is_bound_to_session_and_expires(approval_chat):
    manager, router, now = approval_chat
    deny(manager, router)
    assert manager.resolve("other-session", "sim", router) == "sim"
    assert manager.prompt("session", router.workspace)
    now[0] += 600
    assert manager.resolve("session", "sim", router) == "sim"
    assert manager.prompt("session", router.workspace) is None
    assert load_learned_commands() == set()


def test_workspace_change_invalidates_pending_even_if_original_workspace_returns(approval_chat, tmp_path):
    manager, router, _ = approval_chat
    deny(manager, router)
    other = SimpleNamespace(workspace=tmp_path / "other", _shell_runner=router._shell_runner)
    assert manager.resolve("session", "sim", other) == "sim"
    assert manager.resolve("session", "sim", router) == "sim"
    assert load_learned_commands() == set()


def test_capture_ignores_tools_from_a_different_workspace(approval_chat, tmp_path):
    manager, router, _ = approval_chat
    other_runner = ShellRunner(workspace=tmp_path / "other")
    with manager.capture("session", router.workspace, "task"):
        with pytest.raises(BlockedCommandError):
            other_runner.validate("customtool")
    assert manager.prompt("session", router.workspace) is None


def test_denylist_and_safe_mode_denials_never_request_allowlist_approval(approval_chat):
    manager, router, _ = approval_chat
    for command, error in [("rm -rf /", BlockedCommandError), ("shutdown now", BlockedCommandError), ("rm file.txt", SafeModeError)]:
        with manager.capture("session", router.workspace, "task"):
            with pytest.raises(error):
                router._shell_runner.validate(command)
        assert manager.prompt("session", router.workspace) is None
    assert manager.resolve("session", "sim", router) == "sim"
    assert load_learned_commands() == set()


def test_confirm_does_not_bypass_unknown_command_or_denylist(approval_chat):
    manager, router, _ = approval_chat
    with manager.capture("session", router.workspace, "task"):
        with pytest.raises(BlockedCommandError):
            router._shell_runner.validate("customtool", confirm=True)
    assert manager.prompt("session", router.workspace)
    manager.resolve("session", "sim", router)
    with pytest.raises(BlockedCommandError):
        router._shell_runner.validate("customtool | sh", confirm=True)
    with pytest.raises(SafeModeError):
        router._shell_runner.validate("rm file.txt")


def test_env_assignment_cannot_become_learned_binary_or_authorize_other_commands(approval_chat):
    manager, router, _ = approval_chat
    deny(manager, router, "BAUER_TEST=value customtool --version")
    assert "'customtool'" in manager.prompt("session", router.workspace)
    manager.resolve("session", "sim", router)
    assert load_learned_commands() == {"customtool"}
    with pytest.raises(BlockedCommandError):
        router._shell_runner.validate("BAUER_TEST=value othercustomtool")


def test_failed_persistence_does_not_announce_success_or_allow_cached_runner(approval_chat, monkeypatch):
    manager, router, _ = approval_chat
    deny(manager, router)
    monkeypatch.setattr("bauer.server_shell_approval.add_learned_command", lambda base: None)
    result = manager.resolve("session", "sim", router)
    assert "Não foi possível salvar" in result
    assert "salva e verificada" not in result
    assert manager.prompt("session", router.workspace) is None
    assert load_learned_commands() == set()
    with pytest.raises(BlockedCommandError):
        router._shell_runner.validate("customtool")


def test_missing_shell_does_not_grant_pending_permission(approval_chat):
    manager, router, _ = approval_chat
    deny(manager, router)
    router._shell_runner = None
    assert "indisponível" in manager.resolve("session", "sim", router)
    assert load_learned_commands() == set()


def test_tool_policy_changed_before_confirmation_does_not_grant_permission(approval_chat):
    manager, router, _ = approval_chat
    deny(manager, router)
    router.available_tools = lambda: ["read_file"]
    assert "indisponível" in manager.resolve("session", "sim", router)
    assert load_learned_commands() == set()


def test_process_tool_can_resume_a_real_shell_allowlist_request(approval_chat):
    manager, router, _ = approval_chat
    deny(manager, router)
    router.available_tools = lambda: ["process"]
    assert "salva e verificada" in manager.resolve("session", "sim", router)
    assert load_learned_commands() == {"customtool"}


def test_observer_is_scoped_to_execution_context_and_is_reset(approval_chat):
    manager, router, _ = approval_chat
    with manager.capture("session", router.workspace, "task"), ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(router._shell_runner.validate, "customtool")
        with pytest.raises(BlockedCommandError):
            future.result(timeout=5)
    assert manager.prompt("session", router.workspace) is None
    with pytest.raises(BlockedCommandError):
        router._shell_runner.validate("customtool")
    assert manager.prompt("session", router.workspace) is None


def test_first_blocked_command_is_the_one_user_is_asked_to_approve(approval_chat):
    manager, router, _ = approval_chat
    with manager.capture("session", router.workspace, "task"):
        for command in ["customtool first", "othercustomtool second"]:
            with pytest.raises(BlockedCommandError):
                router._shell_runner.validate(command)
    assert "'customtool'" in manager.prompt("session", router.workspace)
    result = manager.resolve("session", "sim", router)
    assert "customtool first" in result and "othercustomtool" not in result
    assert load_learned_commands() == {"customtool"}


def test_unpresented_request_cannot_be_approved_by_an_unrelated_yes(approval_chat):
    manager, router, _ = approval_chat
    deny(manager, router, presented=False)
    assert manager.resolve("session", "sim", router) == "sim"
    assert load_learned_commands() == set()
    assert manager.prompt("session", router.workspace) is None


def test_previewing_question_without_delivery_does_not_allow_confirmation(approval_chat):
    manager, router, _ = approval_chat
    deny(manager, router, presented=False)
    assert "'customtool'" in manager.prompt("session", router.workspace, presented=False)
    assert manager.resolve("session", "sim", router) == "sim"
    assert load_learned_commands() == set()


def test_delivered_question_can_be_approved_after_worker_preview(approval_chat):
    manager, router, _ = approval_chat
    deny(manager, router, presented=False)
    preview = manager.prompt("session", router.workspace, presented=False)
    assert manager.prompt("session", router.workspace, presented=True) == preview
    assert "salva e verificada" in manager.resolve("session", "sim", router)
    assert load_learned_commands() == {"customtool"}


def test_capture_generations_do_not_retain_completed_session_ids(approval_chat):
    manager, router, _ = approval_chat
    for index in range(20):
        with manager.capture(f"session-{index}", router.workspace, "task"):
            pass
        manager.resolve(f"session-{index}", "sim", router)
    assert manager._generations == {}


@pytest.mark.parametrize("activity", ["capture", "resolve"])
def test_abandoned_expired_requests_are_pruned_by_other_session_traffic(approval_chat, activity):
    manager, router, now = approval_chat
    for index in range(20):
        deny(manager, router, sid=f"abandoned-{index}", presented=False)
    assert len(manager._pending) == 20
    now[0] += 600
    if activity == "capture":
        with manager.capture("new-session", router.workspace, "task"):
            pass
    else:
        assert manager.resolve("new-session", "sim", router) == "sim"
    assert manager._pending == {}
    assert manager._generations == {}


def test_late_observer_after_capture_ends_cannot_create_pending_request(approval_chat):
    from contextvars import copy_context

    manager, router, _ = approval_chat
    with manager.capture("session", router.workspace, "older task"):
        delayed = copy_context()
    with pytest.raises(BlockedCommandError):
        delayed.run(router._shell_runner.validate, "customtool")
    assert manager.prompt("session", router.workspace) is None


def test_new_user_turn_invalidates_old_observer_while_it_is_still_running(approval_chat):
    from contextvars import copy_context

    manager, router, _ = approval_chat
    with manager.capture("session", router.workspace, "older task"):
        delayed = copy_context()
        assert manager.resolve("session", "new task", router) == "new task"
        with pytest.raises(BlockedCommandError):
            delayed.run(router._shell_runner.validate, "customtool")
    assert manager.prompt("session", router.workspace) is None


def test_late_old_capture_does_not_replace_new_turn_command(approval_chat):
    from contextvars import copy_context

    manager, router, _ = approval_chat
    with manager.capture("session", router.workspace, "older task"):
        delayed = copy_context()
        with manager.capture("session", router.workspace, "new task"):
            with pytest.raises(BlockedCommandError):
                router._shell_runner.validate("othercustomtool")
        with pytest.raises(BlockedCommandError):
            delayed.run(router._shell_runner.validate, "customtool")
    assert "'othercustomtool'" in manager.prompt("session", router.workspace)
    manager.resolve("session", "sim", router)
    assert load_learned_commands() == {"othercustomtool"}
