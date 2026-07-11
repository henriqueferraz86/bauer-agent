"""BauerKernel — fachada de orquestração do ciclo de vida de execução.

CONSOLIDA, não reimplementa: recebe por injeção os componentes que já existem
(RunManager, PolicyEngine, EventBus, Runtime Registry) e coordena a máquina de
estados por cima deles. Nenhuma lógica de persistência/policy/execução vive
aqui — só a ORDEM do ciclo de vida:

    created → planning → policy_check → queued → running → [evaluating] → completed

Opt-in por config (``kernel.enabled``, default False) — os caminhos atuais de
execução permanecem intocados até a migração (Sprint 6 do plano).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from .schemas import KernelRequest, KernelRun
from .states import KERNEL_ONLY_STATES, ensure_transition


class BauerKernel:
    def __init__(
        self,
        *,
        runs: Any,                 # core.runtime.run_manager.RunManager
        bus: Any | None = None,    # core.events.bus.EventBus (default: o do RunManager)
        policy: Any | None = None,  # core.policy.engine.PolicyEngine (None = sem gate)
        adapter_factory: Any | None = None,  # callable(name, config) -> RuntimeAdapter
        config: Any | None = None,
        evaluator: Any | None = None,        # Sprint 5 — None pula o estado evaluating
    ) -> None:
        self.runs = runs
        self.bus = bus or getattr(runs, "event_bus", None)
        self.policy = policy
        self.config = config
        self.evaluator = evaluator
        if adapter_factory is None:
            from ..runtime.adapters import get_runtime_adapter
            adapter_factory = get_runtime_adapter
        self.adapter_factory = adapter_factory

    # ── ciclo de vida ─────────────────────────────────────────────────────────

    def execute(self, request: KernelRequest, *, executor: Any | None = None) -> KernelRun:
        """Roda o ciclo de vida completo de uma execução.

        ``executor`` opcional: callable(payload) -> dict, substitui o runtime
        adapter (útil em testes e para motores in-process). Sem ele, resolve o
        adapter pelo Runtime Registry e chama ``run_agent`` (contrato existente).
        """
        session_id = request.session_id or f"session-{uuid4()}"
        adapter = None
        adapter_name = request.runtime_adapter
        if executor is None:
            adapter = self.adapter_factory(request.runtime_adapter or None, config=self.config)
            adapter_name = getattr(adapter, "name", adapter_name or "bauer_native")

        run = self.runs.create_run(
            session_id=session_id,
            agent_id=request.agent_id,
            runtime_adapter=adapter_name or "bauer_native",
            input={**request.input, "task": request.task} if request.task else dict(request.input),
            status="created",
        )
        trajectory = ["created"]

        # planning — hook do Planner (no-op no Sprint 1; Sprint 5 usa p/ replan)
        self._transition(run, "planning", trajectory)
        # policy_check — governança ANTES de executar (inclui gate de orçamento
        # do BudgetManager via operation runtime.execute)
        self._transition(run, "policy_check", trajectory)
        decision = self._evaluate_policy(request, run)
        if decision is not None and decision.action == "deny":
            self.runs.fail_run(run.id, f"policy deny: {decision.reason}")
            trajectory.append("failed")
            return self._result(run.id, session_id, trajectory, decision=decision)
        if decision is not None and decision.action == "ask":
            self._transition(run, "waiting_approval", trajectory)
            self._publish("approval.requested", run, message=decision.reason,
                          data={"operation": request.operation})
            return self._result(run.id, session_id, trajectory, decision=decision)

        self._transition(run, "queued", trajectory)
        self.runs.start_run(run.id)  # → running (evento run.started existente)
        trajectory.append("running")

        payload = {**request.input, "run_id": run.id}
        if request.task and "task" not in payload:
            payload["task"] = request.task
        try:
            result = executor(payload) if executor is not None else adapter.run_agent(payload)
        except Exception as exc:  # noqa: BLE001 — falha do executor é estado, não crash
            self.runs.fail_run(run.id, str(exc))
            trajectory.append("failed")
            return self._result(run.id, session_id, trajectory, decision=decision)

        result = result or {}
        if result.get("status") == "failed" or result.get("event") == "run.failed":
            self.runs.fail_run(run.id, str(result.get("error") or "executor failed"))
            trajectory.append("failed")
            return self._result(run.id, session_id, trajectory, decision=decision,
                                output=result.get("output"))

        # evaluating — quality gate antes de concluir (Sprint 5; None = pula)
        if self.evaluator is not None:
            self._transition(run, "evaluating", trajectory)
            verdict = self.evaluator.evaluate(run_id=run.id, request=request, result=result)
            if not getattr(verdict, "passed", True):
                self.runs.fail_run(run.id, f"quality gate: {getattr(verdict, 'reason', '')}")
                trajectory.append("failed")
                return self._result(run.id, session_id, trajectory, decision=decision,
                                    output=result.get("output"))

        self.runs.complete_run(run.id, output={"output": result.get("output")})
        trajectory.append("completed")
        return self._result(run.id, session_id, trajectory, decision=decision,
                            output=result.get("output"))

    # ── operações de ciclo de vida (Sprint 2) ────────────────────────────────

    def pause(self, run_id: str) -> dict[str, Any]:
        """running → paused. Notifica o adapter (best-effort; "unsupported" ok)."""
        from ..runtime.adapters.base import adapter_pause
        run = self._require_run(run_id)
        ensure_transition(run.status, "paused")
        self.runs.update_run(run_id, status="paused")
        self._publish("run.state.changed", run, status="paused")
        adapter_result = adapter_pause(self._adapter_for(run), run_id)
        return {"run_id": run_id, "status": "paused", "adapter": adapter_result}

    def resume(self, run_id: str) -> dict[str, Any]:
        """paused|waiting_approval → queued. Notifica o adapter (best-effort)."""
        from ..runtime.adapters.base import adapter_resume
        run = self._require_run(run_id)
        ensure_transition(run.status, "queued")
        self.runs.update_run(run_id, status="queued")
        self._publish("run.state.changed", run, status="queued",
                      message=f"resumed from {run.status}")
        adapter_result = adapter_resume(self._adapter_for(run), run_id)
        return {"run_id": run_id, "status": "queued", "adapter": adapter_result}

    def cancel(self, run_id: str) -> dict[str, Any]:
        """Cancela o run (idempotente em terminais) e avisa o adapter."""
        run = self._require_run(run_id)
        cancelled = self.runs.cancel_run(run_id)
        adapter_result: dict[str, Any] = {}
        try:
            adapter_result = dict(self._adapter_for(run).stop_run(run_id))
        except Exception as exc:  # noqa: BLE001 — stop é best-effort
            adapter_result = {"status": "error", "error": str(exc)}
        return {"run_id": run_id, "status": cancelled.status, "adapter": adapter_result}

    def healthcheck(self, adapter_name: str | None = None) -> dict[str, Any]:
        """Saúde do adapter (ou do default do config)."""
        from ..runtime.adapters.base import adapter_healthcheck
        adapter = self.adapter_factory(adapter_name or None, config=self.config)
        return adapter_healthcheck(adapter)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _require_run(self, run_id: str) -> Any:
        run = self.runs.get_run(run_id)
        if run is None:
            raise KeyError(f"Run not found: {run_id}")
        return run

    def _adapter_for(self, run: Any) -> Any:
        return self.adapter_factory(getattr(run, "runtime_adapter", None) or None,
                                    config=self.config)

    def _transition(self, run: Any, new_status: str, trajectory: list[str]) -> None:
        current = self.runs.get_run(run.id).status
        ensure_transition(current, new_status)
        self.runs.update_run(run.id, status=new_status)
        trajectory.append(new_status)
        # estados novos não têm evento dedicado no run_manager — publica o genérico
        if new_status in KERNEL_ONLY_STATES:
            self._publish("run.state.changed", run, status=new_status)

    def _evaluate_policy(self, request: KernelRequest, run: Any):
        if self.policy is None:
            return None
        payload = {"agent_id": request.agent_id, **request.metadata}
        decision = self.policy.evaluate(request.operation, payload)
        self._publish(
            "policy.evaluated", run, status=decision.action, message=decision.reason,
            data={"operation": request.operation, "risk_level": decision.risk_level,
                  "matched_rules": list(decision.matched_rules)},
        )
        return decision

    def _publish(self, event_type: str, run: Any, *, status: str | None = None,
                 message: str | None = None, data: dict | None = None) -> None:
        if self.bus is None:
            return
        try:
            self.bus.publish(event_type, run_id=run.id, session_id=run.session_id,
                             agent_id=run.agent_id, status=status, message=message,
                             data=data or {})
        except Exception as exc:  # noqa: BLE001 — telemetria nunca derruba o run
            from ...logging_config import log_suppressed
            log_suppressed("kernel.publish", exc)

    def _result(self, run_id: str, session_id: str, trajectory: list[str], *,
                decision: Any = None, output: Any = None) -> KernelRun:
        run = self.runs.get_run(run_id)
        return KernelRun(
            run_id=run_id,
            session_id=session_id,
            status=run.status,
            output=output if output is not None else (run.output or {}).get("output"),
            error=run.error,
            policy_action=getattr(decision, "action", None),
            policy_reason=getattr(decision, "reason", None),
            trajectory=trajectory,
        )


# ── composição padrão + flag ──────────────────────────────────────────────────


def kernel_enabled(cfg: Any) -> bool:
    """True se ``kernel.enabled`` está ligado no config (default: False)."""
    try:
        return bool(getattr(getattr(cfg, "kernel", None), "enabled", False))
    except Exception:  # noqa: BLE001
        return False


def build_kernel(cfg: Any | None = None, *, root: str = "memory/runtime",
                 workspace: str = "workspace", with_policy: bool = True) -> BauerKernel:
    """Composição padrão do Kernel com os componentes existentes (produção)."""
    from ..events.bus import EventBus
    from ..runtime.run_manager import RunManager
    from ..runtime.state_store import JsonlStateStore

    store = JsonlStateStore(root)
    bus = EventBus(store=store)
    runs = RunManager(store=store, event_bus=bus)
    policy = None
    if with_policy:
        from ..policy.engine import PolicyEngine
        policy = PolicyEngine(workspace=workspace, runtime_root=root)
    return BauerKernel(runs=runs, bus=bus, policy=policy, config=cfg)
