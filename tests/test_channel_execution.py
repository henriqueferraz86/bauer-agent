"""The transport can stop waiting without abandoning custody or session locks."""

from __future__ import annotations

import threading

import pytest

from bauer.channel_base import AgentBackend, ChannelMessage
from bauer.channel_execution import ChannelExecution, ChannelExecutionRegistry, SessionWorker
from bauer.channel_projects import operator_key
from bauer.core.kernel import Evaluator, GateResult
from bauer.core.runtime.autonomy import BudgetManager
from bauer.core.runtime.run_manager import RunManager
from bauer.cost_meter import cost_sink
from bauer.execution_control import current_control
from bauer.sqlite_session_store import SqliteSessionStore
from bauer.tool_router import ToolRouter


def message(text="work", user="42", chat="42", private=True):
    return ChannelMessage(
        channel="telegram", user_id=user, chat_id=chat, text=text,
        raw={"message": {"chat": {"type": "private" if private else "group"}}},
    )


def backend_at(tmp_path, *, governed=True):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config.yaml"
    if governed:
        config.write_text(f"agent:\n  workspace: {workspace.as_posix()}\n"
                          "model:\n  provider: ollama\n  name: fake\n"
                          "kernel:\n  enabled: true\n", encoding="utf-8")
    backend = AgentBackend(config, tmp_path / "memory" / "sessions")
    backend._client = object()
    backend._model_name = "fake"
    backend._applied_context = 4096
    backend._system_prompt = "Controlled worker"
    backend._router = ToolRouter(workspace=workspace)
    backend._store = SqliteSessionStore(backend.sessions_dir, semantic_indexing_enabled=False)
    backend._maybe_reload = lambda: None
    return backend


# Rede de segurança contra travamento, não asserção de velocidade (ver
# DEADLINE_ANTI_TRAVAMENTO em conftest.py).
_ANTI_HANG = 30.0


def charge_generation_from(entered, monkeypatch):
    """Start the gateway generation clock only once the turn body is running.

    The timeout tests are about what happens when the clock expires *during*
    a turn. Worker setup (Kernel composition, SQLite stores, first imports)
    happens before the turn and is not under test; on the Windows runner it
    alone took longer than the 0.4s budget, so the timeout fired before the
    turn ever started and ``entered`` never got set. The real
    ``wait_for_generation`` still runs with the patched budget.
    """
    from bauer.execution_control import ExecutionControl

    original = ExecutionControl.wait_for_generation

    def gated(self, done, timeout):
        entered.wait(timeout=_ANTI_HANG)
        return original(self, done, timeout)

    monkeypatch.setattr(ExecutionControl, "wait_for_generation", gated)


def start_blocked_turn(backend, monkeypatch, *, cost_before=0, cost_after=0):
    entered, release = threading.Event(), threading.Event()
    charge_generation_from(entered, monkeypatch)
    seen = []
    output = {}

    def turn(ctx, router, *_args, **_kwargs):
        seen.append(current_control())
        if cost_before:
            cost_sink.get()("openai", "controlled", {"prompt_tokens": 1}, cost_before)
        entered.set()
        assert release.wait(timeout=_ANTI_HANG)
        if cost_after:
            cost_sink.get()("openai", "controlled", {"prompt_tokens": 1}, cost_after)
        ctx.add_assistant("partial result")
        return "partial result", [{"tool": "read_file"}]

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", turn)
    caller = threading.Thread(target=lambda: output.update(reply=backend.process(message())))
    caller.start()
    assert entered.wait(timeout=_ANTI_HANG)
    return caller, release, seen, output


def test_cancel_waits_for_real_worker_and_preserves_all_cost(tmp_path, monkeypatch):
    backend = backend_at(tmp_path)
    caller, release, seen, output = start_blocked_turn(
        backend, monkeypatch, cost_before=0.1, cost_after=0.15,
    )
    try:
        job = backend._live_session_execution("tg:42")
        manager = RunManager(root=backend.sessions_dir.parent / "runtime")
        run_id = job.control.run_id
        reply = backend.cancel_channel_execution(message())
        assert "solicitado" in reply
        assert seen[0].cancel_event.is_set()
        assert manager.get_run(run_id).status == "running"
        assert backend._sessions["tg:42"][1].locked()
        assert not job.done.is_set()
        assert backend._clear_session("tg:42") is False
        assert "não foi executada" in backend.process(message("another"))
    finally:
        release.set()
        caller.join(timeout=_ANTI_HANG)
    assert not caller.is_alive()
    assert "cancelada" in output["reply"].lower()
    run = manager.get_run(run_id)
    assert run.status == "cancelled"
    assert run.cost_estimate == pytest.approx(0.25)
    assert run.llm_calls_count == 2
    assert run.tool_calls_count == 1
    assert not backend._sessions["tg:42"][1].locked()
    assert BudgetManager(root=manager.store.root).status()["daily"]["used_usd"] == pytest.approx(0.25)
    assert any(m.get("content") == "partial result" for m in backend._store.load("tg:42"))


def test_timeout_keeps_same_context_and_kernel_custody(tmp_path, monkeypatch):
    import bauer.channel_base as cb

    backend = backend_at(tmp_path)
    monkeypatch.setattr(cb, "_TURN_TIMEOUT_SECONDS", 0.4)
    caller, release, seen, output = start_blocked_turn(
        backend, monkeypatch, cost_after=0.4,
    )
    try:
        caller.join(timeout=_ANTI_HANG)
        assert not caller.is_alive()
        assert "interrupção solicitada" in output["reply"]
        job = backend._live_session_execution("tg:42")
        original = backend._sessions["tg:42"][0]
        assert seen[0].cancel_event.is_set()
        assert backend._get_session("tg:42")[0] is original
        assert backend._clear_session("tg:42") is False
        backend._evict_session("tg:42")
        assert backend._get_session("tg:42")[0] is original
        manager = RunManager(root=backend.sessions_dir.parent / "runtime")
        assert manager.get_run(job.control.run_id).status == "running"
        assert "ainda está trabalhando" in backend.process(message("/new"))
    finally:
        release.set()
        assert job.batch.done.wait(timeout=_ANTI_HANG)
    run = manager.get_run(job.control.run_id)
    assert run.status == "cancelled" and run.cost_estimate == pytest.approx(0.4)
    assert backend._get_session("tg:42")[0] is original


def test_human_approval_wait_pauses_gateway_generation_timeout(tmp_path, monkeypatch):
    import time
    import bauer.channel_base as cb

    backend = backend_at(tmp_path)
    monkeypatch.setattr(cb, "_TURN_TIMEOUT_SECONDS", 1.0)
    entered, release = threading.Event(), threading.Event()
    # The clock starts once the turn is inside the approval wait; worker
    # setup before that is not charged (see charge_generation_from).
    charge_generation_from(entered, monkeypatch)
    output = {}
    controls = []

    def waiting_turn(_ctx, *_args, **_kwargs):
        control = current_control()
        controls.append(control)
        assert control is not None
        assert control.begin_approval_wait("approval-1", "digest-1")
        entered.set()
        assert release.wait(timeout=_ANTI_HANG)
        return "done", []

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", waiting_turn)
    caller = threading.Thread(target=lambda: output.update(reply=backend.process(message())))
    caller.start()
    assert entered.wait(timeout=_ANTI_HANG)
    try:
        time.sleep(1.3)
        assert caller.is_alive()
        assert controls[0] is not None and not controls[0].cancel_event.is_set()
        assert controls[0].end_approval_wait("approval-1")
        caller.join(timeout=_ANTI_HANG)
        assert not caller.is_alive()
        assert "interrupção solicitada" in output["reply"]
    finally:
        release.set()
        caller.join(timeout=5)


def test_execution_control_has_explicit_telegram_identity_and_registry_lookup(tmp_path):
    job = ChannelExecution("owner-1", "telegram", "chat-1", "project-1",
                           "session-1", tmp_path, SessionWorker())
    job.control.run_id = "run-1"
    registry = ChannelExecutionRegistry()
    registry.add(job)

    assert job.control.channel == "telegram"
    assert job.control.owner == "owner-1"
    assert job.control.chat_id == "chat-1"
    assert job.control.project_id == "project-1"
    with pytest.raises(AttributeError):
        job.control.channel = "discord"
    assert registry.active_by_run("run-1") is job
    assert registry.active_for_channel("telegram") == [job]
    assert registry.active_for_channel("discord") == []
    job.done.set()
    assert registry.active_by_run("run-1") is None
    assert registry.active_for_channel("telegram") == []


@pytest.mark.parametrize(("channel", "private", "expected"), [
    ("telegram", True, True),
    ("telegram", False, False),
    ("discord", True, False),
])
def test_backend_enables_toolrouter_approval_wait_only_for_private_telegram(
    tmp_path, monkeypatch, channel, private, expected,
):
    from types import SimpleNamespace

    import bauer.tool_router as tool_router_module

    backend = backend_at(tmp_path)
    bus = object()
    kernel = SimpleNamespace(
        bus=bus, budget=None, control=None,
        runs=SimpleNamespace(get_run=lambda _run_id: None),
    )
    backend._resolver_kernel = lambda: kernel
    backend._configure_channel_router = lambda _router: bus
    subscribed = []
    backend._telegram_approval_subscriber = lambda received: subscribed.append(received)
    job = ChannelExecution(
        owner="owner", channel=channel, chat_id="chat", project_id="project",
        session_key="session", workspace=backend._router.workspace,
        batch=SessionWorker(), private_chat=private,
    )
    job.control.run_id = "run-1"
    router = backend._effective_router()
    assert router._policy_enabled is False
    observed = []
    monkeypatch.setattr(
        router, "_evaluate_runtime_policy",
        lambda *_args, **_kwargs: observed.append(tool_router_module._POLICY_APPROVAL_WAIT.get()),
    )

    def native_turn(_ctx, *_args, **_kwargs):
        router.execute({"action": "calculate", "args": {"expression": "1 + 1"}})
        return "ok"

    monkeypatch.setattr(backend, "_turno_cru", native_turn)

    def governed(_kernel, execute, **_kwargs):
        result = execute({"run_id": "run-1"})
        return SimpleNamespace(
            output=result["output"], result=result, ok=True, governed=True,
            blocked_before_start=False,
        )

    monkeypatch.setattr("bauer.core.kernel.run_governed", governed)
    response = backend._execute_turn(
        object(), "session", object(), "model", "task", execution=job,
    )

    assert response == "ok"
    assert observed == ([True] if expected else [])
    assert (subscribed == [bus]) is (channel == "telegram")
    assert tool_router_module._POLICY_APPROVAL_WAIT.get() is False
    assert router._policy_enabled is False


def test_cancel_discards_queue_explicitly_and_does_not_replay_it(tmp_path, monkeypatch):
    backend = backend_at(tmp_path)
    caller, release, _, output = start_blocked_turn(backend, monkeypatch)
    try:
        assert "fila" in backend.process(message("queued A"))
        assert "fila" in backend.process(message("queued B"))
        reply = backend.cancel_channel_execution(message())
        assert "2 mensagem(ns) pendente(s) não foram executadas" in reply
        assert "tg:42" not in backend._pending
    finally:
        release.set()
        caller.join(timeout=5)
    assert "2 mensagem(ns)" in output["reply"]
    assert not any("queued" in m.get("content", "") for m in backend._store.load("tg:42"))


@pytest.mark.parametrize("other", [
    message(user="99"), message(chat="99"), message(private=False),
])
def test_other_operator_chat_or_group_cannot_cancel(tmp_path, monkeypatch, other):
    backend = backend_at(tmp_path)
    caller, release, seen, _ = start_blocked_turn(backend, monkeypatch)
    try:
        backend.cancel_channel_execution(other)
        assert not seen[0].cancel_event.is_set()
    finally:
        release.set()
        caller.join(timeout=5)


def test_changed_project_binding_cannot_cancel_old_project_run(tmp_path, monkeypatch):
    backend = backend_at(tmp_path)
    caller, release, seen, _ = start_blocked_turn(backend, monkeypatch)
    try:
        backend._project_store.bind(operator_key(message()), "different-project", tmp_path)
        reply = backend.cancel_channel_execution(message(), seen[0].run_id)
        assert "Nenhuma execução" in reply
        assert not seen[0].cancel_event.is_set()
    finally:
        release.set()
        caller.join(timeout=5)


def test_multiple_runs_require_an_explicit_choice(tmp_path):
    backend = backend_at(tmp_path)
    for number in (1, 2):
        job = ChannelExecution(owner=operator_key(message()), channel="telegram", chat_id="42",
                               project_id=None, session_key=f"session-{number}",
                               workspace=tmp_path, batch=SessionWorker())
        job.control.run_id = f"run-{number}"
        backend._executions.add(job)
    reply = backend.cancel_channel_execution(message())
    assert "/cancelar run-1" in reply and "/cancelar run-2" in reply
    assert all(not j.control.cancel_event.is_set() for j in backend._executions.owned(
        owner=operator_key(message()), channel="telegram", chat_id="42", project_id=None,
    ))
    assert "solicitado" in backend.cancel_channel_execution(message(), "run-2")


def test_registry_never_evicts_live_executions(tmp_path):
    registry = ChannelExecutionRegistry(history_limit=1)
    live = ChannelExecution("owner", "telegram", "42", None, "live", tmp_path, SessionWorker())
    registry.add(live)
    for number in range(4):
        old = ChannelExecution("owner", "telegram", "42", None, f"old-{number}", tmp_path,
                               SessionWorker())
        old.done.set()
        registry.add(old)
    assert registry.active_for_session("live") is live
    assert len(registry._entries) == 2


def test_cancel_during_quality_gate_reports_validation_without_false_stop(tmp_path, monkeypatch):
    backend = backend_at(tmp_path)
    entered, release = threading.Event(), threading.Event()
    kernel = backend._resolver_kernel()

    class BlockingGate:
        name = "blocked-validation"
        critical = True

        def check(self, *, request, result):
            entered.set()
            assert release.wait(timeout=5)
            return GateResult(self.name, True)

    kernel.evaluator = Evaluator([BlockingGate()], max_replans=0)

    def turn(ctx, *args, **kwargs):
        ctx.add_assistant("done")
        return "done", []

    monkeypatch.setattr("bauer.agent.run_one_turn_with_fallback", turn)
    result = {}
    caller = threading.Thread(target=lambda: result.update(reply=backend.process(message())))
    caller.start()
    assert entered.wait(timeout=_ANTI_HANG)
    try:
        job = backend._live_session_execution("tg:42")
        assert job.phase == "evaluating"
        reply = backend.cancel_channel_execution(message())
        assert "validação está em andamento" in reply
        assert "solicitado" not in reply
        assert not job.control.cancel_event.is_set()
        assert kernel.runs.get_run(job.control.run_id).status == "evaluating"
    finally:
        release.set()
        caller.join(timeout=5)
    assert result["reply"] == "done"
    assert kernel.runs.get_run(job.control.run_id).status == "completed"


def test_kernel_disabled_still_retains_cancellable_worker(tmp_path, monkeypatch):
    backend = backend_at(tmp_path, governed=False)
    caller, release, seen, output = start_blocked_turn(backend, monkeypatch)
    try:
        assert seen[0].run_id is None
        assert "solicitado" in backend.cancel_channel_execution(message())
        assert backend._sessions["tg:42"][1].locked()
    finally:
        release.set()
        caller.join(timeout=5)
    assert "cancelada" in output["reply"].lower()
    assert not backend._sessions["tg:42"][1].locked()
