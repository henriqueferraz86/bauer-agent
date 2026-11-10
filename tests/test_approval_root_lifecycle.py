"""Approval root fixes: persisted decisions, exact claims and terminal races."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest

from bauer.core.events import EventBus
from bauer.core.kernel import BauerKernel
from bauer.core.policy import ApprovalManager
from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.state_store import SqliteStateStore
from bauer.tool_router import ToolError, ToolRouter
import bauer.core.policy.approvals as approvals_module
from tests._deadlines import ANTI_HANG  # noqa: E402


@pytest.fixture
def kit(tmp_path):
    root = tmp_path / "runtime"
    bus = EventBus(root=root)
    runs = RunManager(root=root, event_bus=bus)
    approvals = ApprovalManager(root=root, event_bus=bus)
    kernel = BauerKernel(runs=runs, bus=bus, approvals=approvals)
    run = runs.create_run(session_id="session", status="running")
    return root, runs, approvals, kernel, run


def request(kit, payload=None, **kwargs):
    _, _, approvals, _, run = kit
    return approvals.request(
        operation="shell.execute", tool_name="run_command", reason="human decision",
        risk_level="high", payload=payload or {"command": "echo first"},
        run_id=run.id, session_id=run.session_id, suspend_run=True, **kwargs,
    )


def authorize(kit, approval_id, payload=None, **kwargs):
    _, _, approvals, _, run = kit
    context = dict(operation="shell.execute", tool_name="run_command",
                   payload=payload or {"command": "echo first"},
                   run_id=run.id, session_id=run.session_id, resume_run=True)
    context.update(kwargs)
    return approvals.authorize(approval_id, **context)


def router(kit, tmp_path):
    root, _, _, _, run = kit
    return ToolRouter(workspace=tmp_path, policy_root=root, policy_enabled=True,
                      run_id=run.id, session_id=run.session_id)


def test_once_default_consumes_exactly_one_call_and_survives_reload(kit):
    root, runs, approvals, kernel, run = kit
    record = request(kit)
    kernel.approve(record.id)
    assert approvals.get(record.id).scope == "once"
    assert runs.get_run(run.id).status == "queued"
    assert authorize(kit, record.id)
    assert runs.get_run(run.id).status == "running"
    assert not authorize(kit, record.id)
    assert not authorize(kit, None)
    assert ApprovalManager(root=root).get(record.id).consumed_at


def test_always_learns_command_for_existing_and_new_executors(kit, tmp_path, monkeypatch):
    from bauer.shell_runner import ShellRunner, BlockedCommandError, load_learned_commands

    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))
    existing = ShellRunner(workspace=tmp_path)
    with pytest.raises(BlockedCommandError):
        existing.validate("curl --version")
    record = request(kit, {"command": "curl --version"})
    kit[3].approve(record.id, scope="always")
    assert authorize(kit, record.id, {"command": "curl --version"})
    assert "curl" in load_learned_commands()
    assert existing.validate("curl --version")
    assert ShellRunner(workspace=tmp_path).validate("curl --version")
    kit[1].complete_run(kit[4].id, output={})
    next_run = kit[1].create_run(session_id="next-session", status="running")
    next_router = ToolRouter(workspace=tmp_path, policy_root=kit[0], policy_enabled=True,
                             run_id=next_run.id, session_id=next_run.session_id)
    next_router._evaluate_runtime_policy("run_command", {"command": "curl --version"})
    assert len(kit[2].list()) == 1
    rules = tmp_path / ".bauer" / "policy.yaml"
    rules.parent.mkdir(exist_ok=True)
    rules.write_text("rules:\n  - id: block-shell\n    operation: shell.execute\n    action: deny\n", encoding="utf-8")
    with pytest.raises(ToolError, match="policy denied"):
        next_router._evaluate_runtime_policy("run_command", {"command": "curl --version"})


@pytest.mark.parametrize("different", [
    {"payload": {"command": "echo other"}},
    {"run_id": "different-run"},
    {"session_id": "different-session"},
    {"operation": "filesystem.delete", "tool_name": "delete_file"},
])
def test_id_never_replays_other_arguments_or_context(kit, different):
    record = request(kit)
    kit[3].approve(record.id, scope="run")
    assert not authorize(kit, record.id, **different)
    assert authorize(kit, record.id)


def test_fingerprint_distinguishes_secrets_after_redaction(kit):
    first = {"command": "echo first", "token": "first-secret"}
    record = request(kit, first)
    kit[3].approve(record.id)
    stored = kit[2].get(record.id)
    assert stored.payload["token"] != "first-secret"
    assert not authorize(kit, record.id, {**first, "token": "other-secret"})
    assert authorize(kit, record.id, first)


def test_run_grant_is_explicit_and_only_for_shell_same_run_session(kit):
    record = request(kit)
    kit[3].approve(record.id, scope="run")
    assert authorize(kit, record.id)
    assert authorize(kit, None, {"command": "echo next"})
    assert not authorize(kit, record.id)  # explicit ID remains a one-call token
    assert not authorize(kit, None, run_id="other")
    assert not authorize(kit, None, session_id="other")
    assert not authorize(kit, None, tool_name="execute_code")
    assert not authorize(kit, None, operation="filesystem.delete", tool_name="delete_file")


@pytest.mark.parametrize("operation,tool", [
    ("runtime.execute", "runtime"), ("filesystem.delete", "delete_file"),
    ("shell.execute", "execute_code"),
])
def test_scope_run_rejects_other_operations(kit, operation, tool):
    _, runs, approvals, kernel, run = kit
    runs.update_run(run.id, status="waiting_approval")
    record = approvals.request(operation=operation, tool_name=tool, reason="test",
                               risk_level="high", run_id=run.id, session_id=run.session_id)
    with pytest.raises(ValueError, match="Run scope requires"):
        kernel.approve(record.id, scope="run")
    assert approvals.get(record.id).status == "pending"


def test_identical_decision_is_idempotent_even_after_terminal(kit):
    _, runs, approvals, kernel, run = kit
    record = request(kit)
    first = kernel.approve(record.id)
    resolved_at = approvals.get(record.id).resolved_at
    assert kernel.approve(record.id) == first
    runs.cancel_run(run.id)
    assert kernel.approve(record.id)["status"] == "cancelled"
    assert approvals.get(record.id).resolved_at == resolved_at
    assert len([event for event in approvals.event_bus.list_events()
                if event.event_type == "approval.accepted"]) == 1
    with pytest.raises(ValueError):
        kernel.approve(record.id, scope="run")
    with pytest.raises(ValueError):
        kernel.deny(record.id)


def test_repeat_old_decision_does_not_resume_next_pending_call(kit):
    record = request(kit)
    kernel = kit[3]
    kernel.approve(record.id)
    assert authorize(kit, record.id)
    second = request(kit, {"command": "echo second"})
    assert kernel.approve(record.id)["status"] == "waiting_approval"
    assert kit[2].get(second.id).status == "pending"


def test_competing_decisions_are_serialized_across_manager_instances(kit):
    root, runs, approvals, _, run = kit
    record = request(kit)
    barrier = Barrier(2)

    def decide(status):
        other = ApprovalManager(root=root)
        barrier.wait(timeout=5)
        try:
            return other.resolve_for_run(
                record.id, status, run_status="queued" if status == "approved" else "failed",
            )[0].status
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(decide, status) for status in ("approved", "denied")]
        results = [future.result(timeout=ANTI_HANG) for future in futures]
    assert results.count("conflict") == 1
    assert approvals.get(record.id).status in {"approved", "denied"}
    assert runs.get_run(run.id).status == (
        "queued" if approvals.get(record.id).status == "approved" else "failed"
    )


def test_competing_claims_only_consume_once(kit):
    root, _, _, kernel, run = kit
    record = request(kit)
    kernel.approve(record.id)
    barrier = Barrier(4)

    def claim():
        other = ApprovalManager(root=root)
        barrier.wait(timeout=5)
        return other.authorize(record.id, operation=record.operation, tool_name=record.tool_name,
                               payload=record.payload, run_id=run.id, session_id=run.session_id,
                               resume_run=True)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(claim) for _ in range(4)]
        assert sum(future.result(timeout=ANTI_HANG) for future in futures) == 1


def test_approved_event_never_exposes_waiting_run(kit, monkeypatch):
    _, runs, approvals, kernel, run = kit
    record = request(kit)
    original = approvals.event_bus.publish

    def publish(event_type, **kwargs):
        if event_type == "approval.accepted":
            assert runs.get_run(run.id).status == "queued"
            assert authorize(kit, record.id)
        return original(event_type, **kwargs)

    monkeypatch.setattr(approvals.event_bus, "publish", publish)
    assert kernel.approve(record.id)["status"] == "running"


def test_policy_deny_wins_over_explicit_id_and_implicit_run_grant(kit, tmp_path):
    record = request(kit)
    kit[3].approve(record.id, scope="run")
    assert authorize(kit, record.id)
    rules = tmp_path / "deny.yaml"
    rules.write_text("rules:\n  - operation: shell.execute\n    action: deny\n", encoding="utf-8")
    tool_router = router(kit, tmp_path)
    tool_router._policy_rules_path = rules
    for args in ({"command": "echo next"}, {"command": "echo first", "approval_id": record.id}):
        with pytest.raises(ToolError, match="policy denied"):
            tool_router._evaluate_runtime_policy("run_command", args)


@pytest.mark.parametrize("tool,args", [
    ("run_command", {"command": "rm -rf /"}),
    ("execute_code", {"code": "rm -rf /"}),
    ("process", {"action": "start", "command": "rm -rf /"}),
])
def test_hardline_rejects_before_persisting_approval(tmp_path, tool, args):
    from bauer.shell_runner import ShellRunner

    tool_router = ToolRouter(
        workspace=tmp_path, shell_runner=ShellRunner(workspace=tmp_path),
        policy_root=tmp_path / "runtime", policy_enabled=True,
    )
    tool_router._policy_mode = "enforce"
    tool_router._wait_for_policy_approval = True

    with pytest.raises(ToolError, match="HARDLINE"):
        tool_router.execute({"action": tool, "args": args})

    assert not ApprovalManager(root=tmp_path / "runtime").list()


def test_execute_code_denylist_rejects_before_persisting_approval(tmp_path):
    tool_router = ToolRouter(
        workspace=tmp_path, policy_root=tmp_path / "runtime", policy_enabled=True,
    )
    tool_router._policy_mode = "enforce"
    tool_router._wait_for_policy_approval = True

    with pytest.raises(ToolError, match="execute_code denylist"):
        tool_router.execute({
            "action": "execute_code",
            "args": {"code": "import os; os.system('echo harmless')"},
        })

    assert not ApprovalManager(root=tmp_path / "runtime").list()


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
def test_terminal_run_rejects_pending_approval_and_tool_entry(kit, tmp_path, terminal):
    _, runs, approvals, kernel, run = kit
    record = request(kit)
    runs.update_run(run.id, status=terminal)
    with pytest.raises(ValueError, match="terminal"):
        kernel.approve(record.id)
    assert approvals.get(record.id).status == "pending"
    with pytest.raises(ToolError, match="terminal"):
        router(kit, tmp_path)._evaluate_runtime_policy("run_command", {"command": "echo next"})
    assert runs.get_run(run.id).status == terminal


def test_stale_writer_cannot_resurrect_cancelled_or_denied_run(kit):
    root, runs, _, kernel, run = kit
    record = request(kit)
    stale = asdict(runs.get_run(run.id))
    kernel.deny(record.id)
    with pytest.raises(ValueError, match="terminal"):
        SqliteStateStore(root).upsert("runs", {**stale, "status": "running"})
    # RunManager preserves the existing terminal outcome and returns it.
    assert runs.complete_run(run.id).status == "failed"
    assert runs.get_run(run.id).status == "failed"


class _MutableClock(datetime):
    current = datetime(2026, 10, 30, 12, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current.astimezone(tz)


def _freeze_approval_clock(monkeypatch, at=None):
    _MutableClock.current = at or datetime(2026, 10, 30, 12, tzinfo=UTC)
    monkeypatch.setattr(approvals_module, "datetime", _MutableClock)


def test_deadline_is_persisted_and_approval_after_expiry_fails_run_across_managers(
    kit, monkeypatch,
):
    root, runs, approvals, _, run = kit
    _freeze_approval_clock(monkeypatch)
    record = request(kit, expires_in_seconds=30)
    assert record.expires_at == "2026-10-30T12:00:30+00:00"
    assert ApprovalManager(root=root).get(record.id).expires_at == record.expires_at

    _MutableClock.current += timedelta(seconds=30)
    other_manager = ApprovalManager(root=root)
    resolved, changed = other_manager.resolve_for_run(
        record.id, "approved", run_status="queued",
    )
    assert changed
    assert resolved.status == "expired"
    assert runs.get_run(run.id).status == "failed"


def test_authorize_expires_previously_approved_but_unconsumed_request(kit, monkeypatch):
    _, runs, approvals, kernel, run = kit
    _freeze_approval_clock(monkeypatch)
    record = request(kit, expires_in_seconds=30)
    kernel.approve(record.id)
    assert runs.get_run(run.id).status == "queued"

    _MutableClock.current += timedelta(seconds=31)
    assert not authorize(kit, record.id)
    assert approvals.get(record.id).status == "expired"
    assert runs.get_run(run.id).status == "failed"


@pytest.mark.parametrize("mismatch", [
    {"payload": {"command": "echo attacker"}},
    {"run_id": "other-run"},
    {"session_id": "other-session"},
    {"operation": "filesystem.delete", "tool_name": "delete_file"},
])
def test_expired_approval_claim_mismatch_cannot_expire_owner_run(kit, monkeypatch, mismatch):
    _, runs, approvals, kernel, run = kit
    _freeze_approval_clock(monkeypatch)
    payload = {"command": "echo first"}
    record = request(kit, payload, expires_in_seconds=30)
    kernel.approve(record.id)
    assert runs.get_run(run.id).status == "queued"

    _MutableClock.current += timedelta(seconds=31)
    claim = dict(
        operation=record.operation, tool_name=record.tool_name,
        payload=payload, run_id=run.id, session_id=run.session_id, resume_run=True,
    )
    claim.update(mismatch)
    assert not approvals.authorize(record.id, **claim)
    assert approvals.get(record.id).status == "approved"
    assert runs.get_run(run.id).status == "queued"

    # The correctly bound waiter can still perform the deadline transition;
    # the unauthorized attempt above must not consume or expire its approval.
    assert not authorize(kit, record.id, payload)
    assert approvals.get(record.id).status == "expired"
    assert runs.get_run(run.id).status == "failed"


def test_before_publish_hook_runs_after_persist_and_before_request_event(kit, monkeypatch):
    _, _, approvals, _, _ = kit
    events = []
    original_publish = approvals.event_bus.publish
    monkeypatch.setattr(
        approvals.event_bus, "publish",
        lambda event_type, **kwargs: (events.append(event_type), original_publish(event_type, **kwargs)),
    )

    def register_waiter(record):
        assert approvals.get(record.id) == record
        assert not events

    record = request(kit, expires_in_seconds=60, before_publish=register_waiter)
    assert events == ["approval.requested"]
    assert approvals.get(record.id).status == "pending"


def test_before_publish_failure_expires_without_actionable_request_event(kit):
    _, runs, approvals, _, run = kit

    def fail_registration(_record):
        raise RuntimeError("waiter registration failed")

    with pytest.raises(RuntimeError, match="waiter registration failed"):
        request(kit, before_publish=fail_registration)

    records = approvals.list()
    assert len(records) == 1
    assert records[0].status == "expired"
    assert runs.get_run(run.id).status == "failed"
    assert not any(event.event_type == "approval.requested"
                   for event in approvals.event_bus.list_events())


def test_requests_without_deadline_keep_existing_approval_semantics(kit, monkeypatch):
    _freeze_approval_clock(monkeypatch)
    record = request(kit)
    assert record.expires_at is None
    _MutableClock.current += timedelta(days=365)
    assert kit[3].approve(record.id)["status"] == "queued"


def test_legacy_telegram_approval_without_deadline_fails_closed_by_run_provenance(tmp_path):
    root = tmp_path / "runtime"
    bus = EventBus(root=root)
    runs = RunManager(root=root, event_bus=bus)
    approvals = ApprovalManager(root=root, event_bus=bus)
    kernel = BauerKernel(runs=runs, bus=bus, approvals=approvals)
    run = runs.create_run(
        session_id="tg-project-legacy-session", agent_id="channel.telegram",
        input={"endpoint": "canal:telegram", "operator": "telegram:42:42"},
        status="waiting_approval",
    )
    record = approvals.request(
        operation="shell.execute", tool_name="run_command", reason="legacy Telegram",
        risk_level="high", payload={"command": "git status"},
        run_id=run.id, session_id=run.session_id,
    )
    # Simulate a row written before ApprovalRecord.expires_at existed.
    legacy = approvals.store.latest("approvals", record.id)
    legacy.pop("expires_at", None)
    approvals.store.upsert("approvals", legacy)

    result = kernel.approve(record.id)

    assert result["status"] == "failed"
    assert approvals.get(record.id).status == "expired"
    assert runs.get_run(run.id).status == "failed"


def test_legacy_web_approval_without_deadline_is_preserved_despite_telegram_like_session(
    tmp_path,
):
    root = tmp_path / "runtime"
    bus = EventBus(root=root)
    runs = RunManager(root=root, event_bus=bus)
    approvals = ApprovalManager(root=root, event_bus=bus)
    kernel = BauerKernel(runs=runs, bus=bus, approvals=approvals)
    run = runs.create_run(
        session_id="tg-project-looks-like-telegram", agent_id="serve.stream",
        input={"endpoint": "/stream"}, status="waiting_approval",
    )
    record = approvals.request(
        operation="shell.execute", tool_name="run_command", reason="legacy web",
        risk_level="high", payload={"command": "git status"},
        run_id=run.id, session_id=run.session_id,
    )
    legacy = approvals.store.latest("approvals", record.id)
    legacy.pop("expires_at", None)
    approvals.store.upsert("approvals", legacy)

    result = kernel.approve(record.id)

    assert result["status"] == "queued"
    assert approvals.get(record.id).status == "approved"
    assert runs.get_run(run.id).status == "queued"


def test_cancel_expires_pending_without_resurrection(kit):
    _, runs, approvals, kernel, run = kit
    record = request(kit)
    kernel.cancel(run.id)
    assert approvals.get(record.id).status == "expired"
    assert runs.get_run(run.id).status == "cancelled"
    with pytest.raises(ValueError):
        kernel.approve(record.id)


def test_legacy_approved_before_resume_keeps_polling(kit, tmp_path, monkeypatch):
    _, runs, approvals, _, run = kit
    tool_router = router(kit, tmp_path)
    tool_router._wait_for_policy_approval = True
    sleeps = []

    def advance(_seconds):
        record = approvals.list()[0]
        if not sleeps:
            approvals.approve(record.id)
        else:
            assert record.status == "approved"
            assert runs.get_run(run.id).status == "waiting_approval"
            runs.update_run(run.id, status="queued")
        sleeps.append(1)

    monkeypatch.setattr("bauer.tool_router.time.sleep", advance)
    tool_router._evaluate_runtime_policy("run_command", {"command": "echo first"})
    assert len(sleeps) == 2
    assert runs.get_run(run.id).status == "running"
    assert approvals.list()[0].consumed_at


def test_polling_stops_on_external_cancel_and_closes_request(kit, tmp_path, monkeypatch):
    _, runs, approvals, _, run = kit
    tool_router = router(kit, tmp_path)
    tool_router._wait_for_policy_approval = True
    monkeypatch.setattr("bauer.tool_router.time.sleep", lambda _: runs.cancel_run(run.id))
    with pytest.raises(ToolError, match="cancelled"):
        tool_router._evaluate_runtime_policy("run_command", {"command": "echo first"})
    assert runs.get_run(run.id).status == "cancelled"
    assert approvals.list()[0].status == "expired"


def test_timeout_is_kernel_failure_and_leaves_no_pending(kit, tmp_path, monkeypatch):
    _, runs, approvals, _, run = kit
    tool_router = router(kit, tmp_path)
    tool_router._wait_for_policy_approval = True
    monkeypatch.setenv("BAUER_APPROVAL_WAIT_SECONDS", "1")
    # Immediate timeout after request creation; no real-time sleep needed.
    ticks = iter((0, 2))
    monkeypatch.setattr("bauer.tool_router.time.monotonic", lambda: next(ticks))
    with pytest.raises(ToolError, match="timed out"):
        tool_router._evaluate_runtime_policy("run_command", {"command": "echo first"})
    assert runs.get_run(run.id).status == "failed"
    assert approvals.list()[0].status == "expired"
    assert not approvals.list("pending")


def test_standalone_exact_once_compatibility_never_becomes_run_grant(tmp_path):
    approvals = ApprovalManager(root=tmp_path)
    record = approvals.request(operation="shell.execute", tool_name="run_command",
                               reason="standalone", risk_level="high", run_id="run-1",
                               session_id="session", payload={"command": "echo first"})
    with pytest.raises(KeyError):
        approvals.approve(record.id, scope="run")
    approvals.approve(record.id)
    context = dict(operation=record.operation, tool_name=record.tool_name, payload=record.payload,
                   run_id=record.run_id, session_id=record.session_id)
    assert approvals.authorize(record.id, **context)
    assert not approvals.authorize(record.id, **context)


def test_legacy_records_default_once_and_cannot_replay_without_fingerprint(kit):
    _, _, approvals, _, _ = kit
    record = request(kit)
    data = asdict(record)
    for key in ("scope", "payload_digest", "consumed_at", "governed"):
        data.pop(key)
    data["status"] = "approved"
    approvals.store.append("approvals", data)
    assert approvals.get(record.id).scope == "once"
    assert not authorize(kit, record.id)
    assert not authorize(kit, None)
