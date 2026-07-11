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
