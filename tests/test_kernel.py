"""Bauer Kernel — Sprint 1 (fachada + máquina de estados + flag).

O Kernel COMPÕE RunManager/PolicyEngine/EventBus existentes — estes testes
verificam a ordem do ciclo de vida, a legalidade das transições e que o
caminho legado (queued→running→completed) permanece intocado.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.core.events.bus import EventBus
from bauer.core.kernel import BauerKernel, KernelRequest, KernelStateError, kernel_enabled
from bauer.core.kernel.states import KERNEL_TRANSITIONS, can_transition, ensure_transition
from bauer.core.runtime.run_manager import RUN_STATUSES, RunManager
from bauer.core.runtime.state_store import JsonlStateStore


@pytest.fixture
def kit(tmp_path: Path):
    store = JsonlStateStore(tmp_path / "runtime")
    bus = EventBus(store=store)
    runs = RunManager(store=store, event_bus=bus)
    return store, bus, runs


def _ok_executor(payload):
    return {"status": "completed", "output": f"ok:{payload.get('task', '')}"}


# ─── máquina de estados ───────────────────────────────────────────────────────


def test_legacy_statuses_still_present():
    """Retrocompat: nenhum status legado saiu da tupla (consumidores existentes)."""
    for s in ("queued", "running", "waiting_approval", "completed", "failed", "cancelled"):
        assert s in RUN_STATUSES


def test_terminal_states_have_no_exit():
    for terminal in ("completed", "failed", "cancelled"):
        assert KERNEL_TRANSITIONS[terminal] == set()


def test_can_transition_happy_path():
    for a, b in [("created", "planning"), ("planning", "policy_check"),
                 ("policy_check", "queued"), ("queued", "running"),
                 ("running", "evaluating"), ("evaluating", "completed"),
                 ("running", "completed")]:
        assert can_transition(a, b), f"{a} → {b} deveria ser legal"


def test_illegal_transition_raises():
    with pytest.raises(KernelStateError):
        ensure_transition("created", "running")  # pula planning/policy/queued
    with pytest.raises(KernelStateError):
        ensure_transition("completed", "running")  # terminal não sai


def test_replan_and_recovery_paths_are_legal():
    assert can_transition("evaluating", "planning")      # replan
    assert can_transition("running", "retrying")
    assert can_transition("retrying", "queued")
    assert can_transition("running", "paused")
    assert can_transition("paused", "queued")
    assert can_transition("waiting_approval", "queued")  # aprovação concedida


# ─── ciclo de vida via fachada ───────────────────────────────────────────────


def test_execute_happy_path_traverses_states(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus)
    out = kernel.execute(KernelRequest(task="diga oi", agent_id="a1"),
                         executor=_ok_executor)
    assert out.ok and out.status == "completed"
    assert out.output == "ok:diga oi"
    assert out.trajectory == ["created", "planning", "policy_check", "queued",
                              "running", "completed"]
    # persistido de verdade no RunManager existente
    run = runs.get_run(out.run_id)
    assert run is not None and run.status == "completed"


def test_execute_publishes_state_events(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus)
    out = kernel.execute(KernelRequest(task="x"), executor=_ok_executor)
    types = [e.event_type for e in bus.list_events(run_id=out.run_id)]
    assert "run.created" in types
    assert "run.state.changed" in types   # planning/policy_check auditáveis
    assert "run.started" in types
    assert "run.completed" in types


def test_executor_exception_fails_run(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus)

    def _boom(payload):
        raise RuntimeError("provider caiu")

    out = kernel.execute(KernelRequest(task="x"), executor=_boom)
    assert out.status == "failed" and "provider caiu" in (out.error or "")
    assert runs.get_run(out.run_id).status == "failed"


def test_executor_failed_result_fails_run(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus)
    out = kernel.execute(
        KernelRequest(task="x"),
        executor=lambda p: {"status": "failed", "error": "sem tokens"},
    )
    assert out.status == "failed" and "sem tokens" in (out.error or "")


# ─── governança (policy no ciclo de vida) ────────────────────────────────────


class _StubPolicy:
    def __init__(self, action: str, reason: str = "stub"):
        self._action, self._reason = action, reason
        self.seen: list[str] = []

    def evaluate(self, operation, payload=None):
        from bauer.core.policy.engine import PolicyDecision
        self.seen.append(operation)
        return PolicyDecision(action=self._action, reason=self._reason,
                              risk_level="low", matched_rules=["stub"])


def test_policy_deny_fails_before_execution(kit):
    _, bus, runs = kit
    called = {"n": 0}

    def _exec(payload):
        called["n"] += 1
        return {"status": "completed", "output": "x"}

    kernel = BauerKernel(runs=runs, bus=bus, policy=_StubPolicy("deny", "bloqueado"))
    out = kernel.execute(KernelRequest(task="rm -rf"), executor=_exec)
    assert out.status == "failed" and out.policy_action == "deny"
    assert "bloqueado" in (out.error or "")
    assert called["n"] == 0  # NUNCA executou


def test_policy_ask_parks_in_waiting_approval(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus, policy=_StubPolicy("ask"))
    out = kernel.execute(KernelRequest(task="deploy"), executor=_ok_executor)
    assert out.status == "waiting_approval" and out.policy_action == "ask"
    types = [e.event_type for e in bus.list_events(run_id=out.run_id)]
    assert "approval.requested" in types and "policy.evaluated" in types


def test_policy_allow_executes(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus, policy=_StubPolicy("allow"))
    out = kernel.execute(KernelRequest(task="ls", operation="filesystem.read"),
                         executor=_ok_executor)
    assert out.ok and out.policy_action == "allow"


# ─── evaluator hook (Sprint 5 — aqui só o contrato) ──────────────────────────


class _StubEvaluator:
    def __init__(self, passed: bool):
        self._passed = passed

    def evaluate(self, *, run_id, request, result):
        class _V:
            passed = self._passed
            reason = "gate reprovado"
        return _V()


def test_evaluator_gate_blocks_completion(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus, evaluator=_StubEvaluator(False))
    out = kernel.execute(KernelRequest(task="x"), executor=_ok_executor)
    assert out.status == "failed" and "quality gate" in (out.error or "")
    assert "evaluating" in out.trajectory


def test_evaluator_pass_completes(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus, evaluator=_StubEvaluator(True))
    out = kernel.execute(KernelRequest(task="x"), executor=_ok_executor)
    assert out.ok and "evaluating" in out.trajectory


# ─── Sprint 2: execução via adapter + operações de ciclo de vida ────────────


class _StubAdapter:
    """Adapter mínimo do contrato existente + healthcheck opcional."""

    name = "stub"

    def __init__(self):
        self.paused: list[str] = []
        self.stopped: list[str] = []

    def run_agent(self, request):
        return {"status": "completed", "output": f"adapter:{request.get('task', '')}",
                "run_id": request.get("run_id")}

    def stop_run(self, run_id):
        self.stopped.append(run_id)
        return {"status": "stopped", "run_id": run_id}

    def healthcheck(self):
        return {"status": "healthy", "runtime_adapter": self.name}


def test_execute_via_adapter_contract(kit):
    """Sem executor injetado, o Kernel resolve o adapter e chama run_agent."""
    _, bus, runs = kit
    adapter = _StubAdapter()
    kernel = BauerKernel(runs=runs, bus=bus,
                         adapter_factory=lambda name, config=None: adapter)
    out = kernel.execute(KernelRequest(task="oi", runtime_adapter="stub"))
    assert out.ok and out.output == "adapter:oi"
    assert runs.get_run(out.run_id).runtime_adapter == "stub"


def test_adapter_payload_carries_run_id(kit):
    _, bus, runs = kit
    seen: dict = {}

    class _Cap(_StubAdapter):
        def run_agent(self, request):
            seen.update(request)
            return {"status": "completed", "output": "x"}

    kernel = BauerKernel(runs=runs, bus=bus,
                         adapter_factory=lambda name, config=None: _Cap())
    out = kernel.execute(KernelRequest(task="t"))
    assert seen["run_id"] == out.run_id and seen["task"] == "t"


def test_pause_resume_cycle(kit):
    _, bus, runs = kit
    adapter = _StubAdapter()
    kernel = BauerKernel(runs=runs, bus=bus,
                         adapter_factory=lambda name, config=None: adapter)
    run = runs.create_run(session_id="s1", runtime_adapter="stub", status="queued")
    runs.start_run(run.id)

    paused = kernel.pause(run.id)
    assert paused["status"] == "paused"
    assert runs.get_run(run.id).status == "paused"
    # adapter sem pause_run → helper degrada p/ unsupported, sem quebrar
    assert paused["adapter"]["status"] == "unsupported"

    resumed = kernel.resume(run.id)
    assert resumed["status"] == "queued"
    assert runs.get_run(run.id).status == "queued"


def test_pause_illegal_state_raises(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus,
                         adapter_factory=lambda name, config=None: _StubAdapter())
    run = runs.create_run(session_id="s1", status="queued")  # não está running
    with pytest.raises(KernelStateError):
        kernel.pause(run.id)


def test_cancel_notifies_adapter(kit):
    _, bus, runs = kit
    adapter = _StubAdapter()
    kernel = BauerKernel(runs=runs, bus=bus,
                         adapter_factory=lambda name, config=None: adapter)
    run = runs.create_run(session_id="s1", runtime_adapter="stub", status="queued")
    result = kernel.cancel(run.id)
    assert result["status"] == "cancelled"
    assert adapter.stopped == [run.id]


def test_cancel_idempotent_on_terminal(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus,
                         adapter_factory=lambda name, config=None: _StubAdapter())
    run = runs.create_run(session_id="s1", status="queued")
    runs.start_run(run.id)
    runs.complete_run(run.id)
    result = kernel.cancel(run.id)
    assert result["status"] == "completed"  # terminal não muda


def test_healthcheck_via_kernel(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus,
                         adapter_factory=lambda name, config=None: _StubAdapter())
    assert kernel.healthcheck()["status"] == "healthy"


def test_healthcheck_helper_degrades_gracefully():
    from bauer.core.runtime.adapters.base import (
        adapter_healthcheck, adapter_pause, adapter_resume,
    )

    class _Bare:  # contrato antigo, sem os métodos opcionais
        name = "bare"

    assert adapter_healthcheck(_Bare())["status"] == "unknown"
    assert adapter_pause(_Bare(), "r1")["status"] == "unsupported"
    assert adapter_resume(_Bare(), "r1")["status"] == "unsupported"


def test_native_adapter_healthcheck():
    from bauer.core.runtime.adapters.bauer_native import BauerNativeRuntimeAdapter

    assert BauerNativeRuntimeAdapter().healthcheck()["status"] == "healthy"


# ─── Sprint 3: governança no ciclo de vida ───────────────────────────────────


class _KillSwitch:
    def __init__(self, enabled: bool):
        self._enabled = enabled

    def kill_switch_enabled(self) -> bool:
        return self._enabled


def test_kill_switch_cancels_before_any_work(kit):
    _, bus, runs = kit
    called = {"n": 0}

    def _exec(payload):
        called["n"] += 1
        return {"status": "completed"}

    kernel = BauerKernel(runs=runs, bus=bus, control=_KillSwitch(True))
    out = kernel.execute(KernelRequest(task="x"), executor=_exec)
    assert out.status == "cancelled" and called["n"] == 0
    assert "kill switch" in (out.error or "")
    assert runs.get_run(out.run_id).status == "cancelled"


def test_kill_switch_off_executes_normally(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus, control=_KillSwitch(False))
    out = kernel.execute(KernelRequest(task="x"), executor=_ok_executor)
    assert out.ok


def test_ask_creates_real_approval_and_approve_continues(kit, tmp_path):
    from bauer.core.policy.approvals import ApprovalManager

    _, bus, runs = kit
    approvals = ApprovalManager(root=tmp_path / "runtime", event_bus=bus)
    kernel = BauerKernel(runs=runs, bus=bus, policy=_StubPolicy("ask", "operação sensível"),
                         approvals=approvals)
    out = kernel.execute(KernelRequest(task="deploy"), executor=_ok_executor)
    assert out.status == "waiting_approval" and out.approval_id

    record = approvals.get(out.approval_id)
    assert record is not None and record.status == "pending"
    assert record.run_id == out.run_id

    # aprovação humana → run continua até o fim pelo MESMO trilho
    final = kernel.approve(out.approval_id, executor=_ok_executor)
    assert final.ok and final.run_id == out.run_id
    assert approvals.get(out.approval_id).status == "approved"
    assert runs.get_run(out.run_id).status == "completed"


def test_deny_fails_the_waiting_run(kit, tmp_path):
    from bauer.core.policy.approvals import ApprovalManager

    _, bus, runs = kit
    approvals = ApprovalManager(root=tmp_path / "runtime", event_bus=bus)
    kernel = BauerKernel(runs=runs, bus=bus, policy=_StubPolicy("ask"),
                         approvals=approvals)
    out = kernel.execute(KernelRequest(task="deploy"), executor=_ok_executor)
    result = kernel.deny(out.approval_id)
    assert result["status"] == "denied"
    run = runs.get_run(out.run_id)
    assert run.status == "failed" and "negada" in (run.error or "")


def test_cost_recorded_in_budget_on_completion(kit):
    _, bus, runs = kit
    recorded: list[dict] = []

    class _Budget:
        def record_run_cost(self, **kw):
            recorded.append(kw)

    kernel = BauerKernel(runs=runs, bus=bus, budget=_Budget())
    out = kernel.execute(
        KernelRequest(task="x", agent_id="a9"),
        executor=lambda p: {"status": "completed", "output": "ok", "cost_estimate": 0.042},
    )
    assert out.ok
    assert recorded and recorded[0]["cost_usd"] == 0.042
    assert recorded[0]["run_id"] == out.run_id
    assert runs.get_run(out.run_id).cost_estimate == 0.042


def test_non_serializable_input_does_not_crash_persistence(kit):
    """client (objeto vivo) no input não pode quebrar o JsonlStateStore."""
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus)
    seen: dict = {}

    class _FakeClient:
        pass

    def _exec(payload):
        seen.update(payload)
        return {"status": "completed", "output": "ok"}

    client = _FakeClient()
    out = kernel.execute(KernelRequest(task="x", input={"client": client, "model": "m1"}),
                         executor=_exec)
    assert out.ok
    # o executor recebeu o objeto VIVO...
    assert seen["client"] is client and seen["model"] == "m1"
    # ...mas o persistido foi saneado (marcador em vez do objeto)
    stored = runs.get_run(out.run_id).input
    assert stored["model"] == "m1"
    assert "non-serializable" in str(stored["client"])


def test_build_kernel_composes_governance(tmp_path):
    """build_kernel liga control/approvals/budget por padrão (produção)."""
    from bauer.core.kernel import build_kernel

    kernel = build_kernel(None, root=str(tmp_path / "runtime"),
                          workspace=str(tmp_path / "ws"))
    assert kernel.control is not None
    assert kernel.approvals is not None
    assert kernel.budget is not None
    assert kernel.policy is not None


# ─── Sprint 4: resiliência in-loop ───────────────────────────────────────────


def test_retry_succeeds_on_second_attempt(kit):
    _, bus, runs = kit
    calls = {"n": 0}

    def _flaky(payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("falha transitória")
        return {"status": "completed", "output": "ok"}

    kernel = BauerKernel(runs=runs, bus=bus)
    out = kernel.execute(KernelRequest(task="x", max_retries=2), executor=_flaky)
    assert out.ok and calls["n"] == 2
    # trajetória mostra o ciclo retrying → queued → running
    assert "retrying" in out.trajectory
    assert out.trajectory.count("running") == 2


def test_retry_exhausted_fails(kit):
    _, bus, runs = kit
    calls = {"n": 0}

    def _always_fails(payload):
        calls["n"] += 1
        raise RuntimeError("permanente")

    kernel = BauerKernel(runs=runs, bus=bus)
    out = kernel.execute(KernelRequest(task="x", max_retries=2), executor=_always_fails)
    assert out.status == "failed" and calls["n"] == 3  # 1 + 2 retries
    assert out.trajectory.count("retrying") == 2


def test_no_retry_by_default(kit):
    _, bus, runs = kit
    calls = {"n": 0}

    def _fails(payload):
        calls["n"] += 1
        raise RuntimeError("x")

    kernel = BauerKernel(runs=runs, bus=bus)
    out = kernel.execute(KernelRequest(task="x"), executor=_fails)
    assert out.status == "failed" and calls["n"] == 1
    assert "retrying" not in out.trajectory


def test_fallback_adapter_takes_over(kit):
    _, bus, runs = kit

    class _Broken(_StubAdapter):
        name = "broken"

        def run_agent(self, request):
            raise RuntimeError("adapter primário caiu")

    good = _StubAdapter()
    good.name = "good"
    adapters = {"broken": _Broken(), "good": good}

    kernel = BauerKernel(
        runs=runs, bus=bus,
        adapter_factory=lambda name, config=None: adapters[name or "broken"],
    )
    out = kernel.execute(KernelRequest(task="oi", runtime_adapter="broken",
                                       fallback_adapters=["good"]))
    assert out.ok and out.output == "adapter:oi"
    # o run reflete o adapter que efetivamente executou
    assert runs.get_run(out.run_id).runtime_adapter == "good"
    # evento de fallback auditável
    msgs = [e.message for e in bus.list_events(run_id=out.run_id) if e.message]
    assert any("fallback" in m for m in msgs)


def test_unresolvable_fallback_skipped_to_next(kit):
    _, bus, runs = kit
    good = _StubAdapter()
    good.name = "good"

    def _factory(name, config=None):
        if name == "good":
            return good
        raise RuntimeError(f"adapter {name} não registrado")

    def _fails(payload):
        raise RuntimeError("primário caiu")

    kernel = BauerKernel(runs=runs, bus=bus, adapter_factory=_factory)
    out = kernel.execute(
        KernelRequest(task="oi", fallback_adapters=["fantasma", "good"]),
        executor=_fails,
    )
    assert out.ok and out.output == "adapter:oi"


def test_recover_marks_stuck_runs_failed(kit):
    from datetime import UTC, datetime, timedelta

    _, bus, runs = kit
    # run preso em running com updated_at antigo (simula crash)
    run = runs.create_run(session_id="s1", status="queued")
    runs.start_run(run.id)
    old = (datetime.now(UTC) - timedelta(seconds=3600)).isoformat()
    data = runs.get_run(run.id).__dict__ | {"updated_at": old}
    runs.store.upsert("runs", data)

    kernel = BauerKernel(runs=runs, bus=bus,
                         adapter_factory=lambda name, config=None: _StubAdapter())
    recovered = kernel.recover(max_age_s=900)
    assert any(r["run_id"] == run.id for r in recovered)
    assert runs.get_run(run.id).status == "failed"
    # run recente NÃO é tocado
    fresh = runs.create_run(session_id="s2", status="queued")
    runs.start_run(fresh.id)
    assert kernel.recover(max_age_s=900) == []
    assert runs.get_run(fresh.id).status == "running"


# ─── Sprint 5: Evaluator + quality gates + replan ────────────────────────────


def test_default_gates_pass_good_output():
    from bauer.core.kernel.evaluator import Evaluator

    v = Evaluator().evaluate(run_id="r", request=None,
                             result={"output": "resultado útil"})
    assert v.passed and len(v.gates) == 2


def test_empty_output_reproved():
    from bauer.core.kernel.evaluator import Evaluator

    v = Evaluator().evaluate(run_id="r", request=None, result={"output": "  "})
    assert not v.passed and "non_empty_output" in v.reason


def test_traceback_in_output_reproved():
    from bauer.core.kernel.evaluator import Evaluator

    v = Evaluator().evaluate(run_id="r", request=None, result={
        "output": "fiz tudo!\nTraceback (most recent call last):\n  File ..."
    })
    assert not v.passed and "no_traceback" in v.reason


def test_callable_gate_with_reason():
    from bauer.core.kernel.evaluator import CallableGate, Evaluator

    gate = CallableGate("tem_oi", lambda req, res: "" if "oi" in str(res.get("output"))
                        else "faltou 'oi' no output")
    ev = Evaluator([gate])
    assert ev.evaluate(run_id="r", request=None, result={"output": "oi!"}).passed
    v = ev.evaluate(run_id="r", request=None, result={"output": "tchau"})
    assert not v.passed and "faltou 'oi'" in v.reason


def test_broken_gate_does_not_reprove():
    from bauer.core.kernel.evaluator import CallableGate, Evaluator

    def _boom(req, res):
        raise RuntimeError("gate bugado")

    v = Evaluator([CallableGate("bugado", _boom)]).evaluate(
        run_id="r", request=None, result={"output": "x"})
    assert v.passed  # gate quebrado é problema do gate, não do resultado


def test_replan_fixes_on_second_execution(kit):
    """Gate reprova a 1ª execução → replan re-executa com feedback → passa."""
    from bauer.core.kernel.evaluator import Evaluator

    _, bus, runs = kit
    calls: list[dict] = []

    def _exec(payload):
        calls.append(dict(payload))
        if payload.get("replan_attempt"):
            return {"status": "completed", "output": "agora sim, corrigido"}
        return {"status": "completed", "output": ""}  # 1ª volta: output vazio

    kernel = BauerKernel(runs=runs, bus=bus, evaluator=Evaluator(max_replans=1))
    out = kernel.execute(KernelRequest(task="x"), executor=_exec)
    assert out.ok and out.output == "agora sim, corrigido"
    assert len(calls) == 2
    # a 2ª execução recebeu o motivo da reprovação
    assert "output vazio" in calls[1]["replan_feedback"]
    # trajetória: ... running → evaluating → planning → ... → running → evaluating → completed
    assert out.trajectory.count("evaluating") == 2
    assert out.trajectory.count("planning") == 2  # 1 inicial + 1 replan


def test_replan_budget_exhausted_fails(kit):
    from bauer.core.kernel.evaluator import Evaluator

    _, bus, runs = kit
    calls = {"n": 0}

    def _always_empty(payload):
        calls["n"] += 1
        return {"status": "completed", "output": ""}

    kernel = BauerKernel(runs=runs, bus=bus, evaluator=Evaluator(max_replans=2))
    out = kernel.execute(KernelRequest(task="x"), executor=_always_empty)
    assert out.status == "failed" and "quality gate" in (out.error or "")
    assert calls["n"] == 3  # 1 + 2 replans


def test_build_kernel_wires_evaluator_from_config(tmp_path):
    from bauer.config_loader import BauerConfig, KernelSection, ModelSection
    from bauer.core.kernel import build_kernel

    cfg = BauerConfig(model=ModelSection(provider="ollama", name="x"),
                      kernel=KernelSection(evaluator_enabled=True, max_replans=3))
    kernel = build_kernel(cfg, root=str(tmp_path / "rt"), workspace=str(tmp_path / "ws"))
    assert kernel.evaluator is not None and kernel.evaluator.max_replans == 3

    cfg_off = BauerConfig(model=ModelSection(provider="ollama", name="x"))
    kernel_off = build_kernel(cfg_off, root=str(tmp_path / "rt2"),
                              workspace=str(tmp_path / "ws"))
    assert kernel_off.evaluator is None


# ─── Sprint 6a: kernel.stream() (generator) ──────────────────────────────────


def _consume_stream(gen):
    """Recolhe (deltas concatenados, evento final KernelRun) de kernel.stream()."""
    deltas = []
    final = None
    for evt in gen:
        if evt["event"] == "message.delta":
            deltas.append(evt["content"])
        elif evt["event"] == "final":
            final = evt["run"]
    return "".join(deltas), final


def _stream_ok_executor(payload):
    yield {"event": "message.delta", "content": "olá"}
    yield {"event": "message.delta", "content": ", mundo"}
    yield {"event": "run.completed", "status": "completed", "tool_calls_count": 2}


def test_stream_happy_path_forwards_deltas_and_final(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus)
    text, final = _consume_stream(kernel.stream(KernelRequest(task="oi"),
                                                executor=_stream_ok_executor))
    assert text == "olá, mundo"
    assert final.ok and final.output == "olá, mundo"
    assert final.trajectory == ["created", "planning", "policy_check", "queued", "running", "completed"]
    assert runs.get_run(final.run_id).status == "completed"
    assert runs.get_run(final.run_id).tool_calls_count == 2


def test_stream_forwards_deltas_before_failure(kit):
    """Deltas já emitidos permanecem no stream mesmo se o run falhar depois —
    o caller (SSE) já os mostrou; não há como 'desmostrar'."""
    _, bus, runs = kit

    def _flaky_stream(payload):
        yield {"event": "message.delta", "content": "parcial"}
        yield {"event": "run.failed", "error": "conexão caiu no meio"}

    kernel = BauerKernel(runs=runs, bus=bus)
    text, final = _consume_stream(kernel.stream(KernelRequest(task="x"),
                                                executor=_flaky_stream))
    assert text == "parcial"
    assert final.status == "failed" and "conexão caiu" in (final.error or "")
    assert final.output == "parcial"  # o que já foi mostrado


def test_stream_executor_exception_fails_run(kit):
    _, bus, runs = kit

    def _boom(payload):
        raise RuntimeError("provider indisponível")
        yield  # torna a função um generator (nunca alcançado)

    kernel = BauerKernel(runs=runs, bus=bus)
    _, final = _consume_stream(kernel.stream(KernelRequest(task="x"), executor=_boom))
    assert final.status == "failed" and "indisponível" in (final.error or "")


def test_stream_policy_deny_never_touches_executor(kit):
    _, bus, runs = kit
    called = {"n": 0}

    def _exec(payload):
        called["n"] += 1
        yield {"event": "message.delta", "content": "x"}

    kernel = BauerKernel(runs=runs, bus=bus, policy=_StubPolicy("deny", "bloqueado"))
    _, final = _consume_stream(kernel.stream(KernelRequest(task="x"), executor=_exec))
    assert final.status == "failed" and final.policy_action == "deny"
    assert called["n"] == 0


def test_stream_policy_ask_parks_before_executor(kit):
    _, bus, runs = kit
    called = {"n": 0}

    def _exec(payload):
        called["n"] += 1
        yield {"event": "message.delta", "content": "x"}

    kernel = BauerKernel(runs=runs, bus=bus, policy=_StubPolicy("ask"))
    _, final = _consume_stream(kernel.stream(KernelRequest(task="x"), executor=_exec))
    assert final.status == "waiting_approval" and called["n"] == 0


def test_stream_kill_switch_cancels_before_executor(kit):
    _, bus, runs = kit
    called = {"n": 0}

    def _exec(payload):
        called["n"] += 1
        yield {"event": "message.delta", "content": "x"}

    kernel = BauerKernel(runs=runs, bus=bus, control=_KillSwitch(True))
    _, final = _consume_stream(kernel.stream(KernelRequest(task="x"), executor=_exec))
    assert final.status == "cancelled" and called["n"] == 0


def test_stream_evaluator_gate_blocks_completion(kit):
    _, bus, runs = kit
    kernel = BauerKernel(runs=runs, bus=bus, evaluator=_StubEvaluator(False))
    text, final = _consume_stream(kernel.stream(KernelRequest(task="x"),
                                                executor=_stream_ok_executor))
    assert text == "olá, mundo"  # já emitido
    assert final.status == "failed" and "quality gate" in (final.error or "")
    assert "evaluating" in final.trajectory


def test_stream_via_real_adapter_contract(kit):
    """Sem executor injetado, usa adapter.stream_agent() (contrato existente)."""
    _, bus, runs = kit

    class _StreamAdapter:
        name = "stream-stub"

        def stream_agent(self, request):
            yield {"event": "message.delta", "content": f"eco:{request.get('task', '')}"}
            yield {"event": "run.completed"}

    kernel = BauerKernel(runs=runs, bus=bus,
                         adapter_factory=lambda name, config=None: _StreamAdapter())
    text, final = _consume_stream(kernel.stream(KernelRequest(task="oi")))
    assert text == "eco:oi" and final.ok


# ─── flag de config ──────────────────────────────────────────────────────────


def test_kernel_disabled_by_default():
    from bauer.config_loader import BauerConfig, ModelSection

    cfg = BauerConfig(model=ModelSection(provider="ollama", name="x"))
    assert kernel_enabled(cfg) is False


def test_kernel_flag_opt_in():
    from bauer.config_loader import BauerConfig, KernelSection, ModelSection

    cfg = BauerConfig(model=ModelSection(provider="ollama", name="x"),
                      kernel=KernelSection(enabled=True))
    assert kernel_enabled(cfg) is True


def test_kernel_enabled_tolerates_garbage():
    assert kernel_enabled(None) is False
    assert kernel_enabled(object()) is False
