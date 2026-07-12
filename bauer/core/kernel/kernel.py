"""BauerKernel — fachada de orquestração do ciclo de vida de execução.

CONSOLIDA, não reimplementa: recebe por injeção os componentes que já existem
(RunManager, PolicyEngine, EventBus, Runtime Registry, RuntimeControl,
ApprovalManager, BudgetManager) e coordena a máquina de estados por cima
deles. Nenhuma lógica de persistência/policy/execução vive aqui — só a ORDEM
do ciclo de vida:

    created → planning → policy_check → queued → running → [evaluating] → completed

Governança no ciclo (Sprint 3): kill-switch antes de tudo; policy_check com
gate de orçamento (operation runtime.execute); ask → waiting_approval com
ApprovalRecord real; custo registrado no BudgetManager ao concluir.

Opt-in por config (``kernel.enabled``, default False) — os caminhos atuais de
execução permanecem intocados até a migração (Sprint 6 do plano).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from .schemas import KernelRequest, KernelRun
from .states import KERNEL_ONLY_STATES, ensure_transition


#: sentinela p/ _open_run não resolver adapter (admissão sem custódia — admit())
_NO_EXECUTION = object()


def _persistable(data: dict[str, Any]) -> dict[str, Any]:
    """Cópia JSON-serializável do payload — objetos vivos (client, callables)
    viram marcador. O payload ORIGINAL segue intacto para o adapter; só o que
    vai para o JsonlStateStore é saneado."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        try:
            json.dumps(value)
            out[key] = value
        except (TypeError, ValueError):
            out[key] = f"<non-serializable: {type(value).__name__}>"
    return out


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
        control: Any | None = None,          # core.runtime.resilience.RuntimeControl
        approvals: Any | None = None,        # core.policy.approvals.ApprovalManager
        budget: Any | None = None,           # core.runtime.autonomy.BudgetManager
        recovery: Any | None = None,         # core.runtime.resilience.RuntimeRecovery
    ) -> None:
        self.runs = runs
        self.bus = bus or getattr(runs, "event_bus", None)
        self.policy = policy
        self.config = config
        self.evaluator = evaluator
        self.control = control
        self.approvals = approvals
        self.budget = budget
        self.recovery = recovery
        if adapter_factory is None:
            from ..runtime.adapters import get_runtime_adapter
            adapter_factory = get_runtime_adapter
        self.adapter_factory = adapter_factory

    # ── ciclo de vida ─────────────────────────────────────────────────────────

    def _open_run(self, request: KernelRequest, executor: Any | None):
        """Cria o Run persistido + payload de execução. Compartilhado por
        ``execute`` e ``stream`` (mesmo preflight, um só lugar p/ divergir)."""
        session_id = request.session_id or f"session-{uuid4()}"
        adapter = None
        adapter_name = request.runtime_adapter
        if executor is None:
            adapter = self.adapter_factory(request.runtime_adapter or None, config=self.config)
            adapter_name = getattr(adapter, "name", adapter_name or "bauer_native")

        stored_input = _persistable(
            {**request.input, "task": request.task} if request.task else dict(request.input)
        )
        run = self.runs.create_run(
            session_id=session_id,
            agent_id=request.agent_id,
            runtime_adapter=adapter_name or "bauer_native",
            input=stored_input,
            status="created",
        )
        payload = {**request.input, "run_id": run.id}
        if request.task and "task" not in payload:
            payload["task"] = request.task
        return run, session_id, ["created"], adapter, payload

    def _preflight(self, request: KernelRequest, run: Any, session_id: str,
                   trajectory: list[str]):
        """Kill-switch → planning → policy_check → queued. Retorna
        ``(decision, early)`` — ``early`` é o KernelRun terminal quando a
        governança impediu a execução (cancelled/deny/ask); None = prosseguir."""
        # kill-switch central ANTES de qualquer trabalho (RuntimeControl existente)
        if self.control is not None and self.control.kill_switch_enabled():
            self.runs.update_run(run.id, status="cancelled",
                                 error="runtime kill switch ativo")
            trajectory.append("cancelled")
            return None, self._result(run.id, session_id, trajectory)

        # planning — hook do Planner (no-op no Sprint 1; Sprint 5 usa p/ replan)
        self._transition(run, "planning", trajectory)
        # policy_check — governança ANTES de executar (inclui gate de orçamento
        # do BudgetManager via operation runtime.execute)
        self._transition(run, "policy_check", trajectory)
        decision = self._evaluate_policy(request, run)
        if decision is not None and decision.action == "deny":
            self.runs.fail_run(run.id, f"policy deny: {decision.reason}")
            trajectory.append("failed")
            return decision, self._result(run.id, session_id, trajectory, decision=decision)
        if decision is not None and decision.action == "ask":
            self._transition(run, "waiting_approval", trajectory)
            approval_id = self._request_approval(request, run, decision)
            return decision, self._result(run.id, session_id, trajectory, decision=decision,
                                          approval_id=approval_id)

        self._transition(run, "queued", trajectory)
        return decision, None

    def execute(self, request: KernelRequest, *, executor: Any | None = None) -> KernelRun:
        """Roda o ciclo de vida completo de uma execução.

        ``executor`` opcional: callable(payload) -> dict, substitui o runtime
        adapter (útil em testes e para motores in-process). Sem ele, resolve o
        adapter pelo Runtime Registry e chama ``run_agent`` (contrato existente).
        """
        run, session_id, trajectory, adapter, payload = self._open_run(request, executor)
        decision, early = self._preflight(request, run, session_id, trajectory)
        if early is not None:
            return early
        return self._run_to_completion(run, payload, session_id, trajectory,
                                       executor=executor, adapter=adapter,
                                       decision=decision, request=request)

    def admit(self, request: KernelRequest) -> "tuple[Any, KernelRun | None]":
        """Controle de admissão SEM custódia da execução (Sprint 6c).

        Para front-ends cujo motor não pode ser envolvido pelo Kernel — ex.:
        o /stream SSE, que roda o turno numa thread órfã com persistência
        própria após timeout/desconexão. Roda o MESMO preflight de execute():
        run criado (created → planning → policy_check → queued), kill-switch e
        policy/budget. O CALLER assume dali em diante (start_run → complete/
        fail, como já faz hoje) — evaluator/retry/replan NÃO se aplicam a runs
        admitidos; quem quiser isso usa execute()/stream().

        Retorna ``(run, early)``: ``early`` é o KernelRun terminal quando a
        governança barrou (cancelled/deny/ask) — o caller NÃO deve executar.
        Com ``early is None``, o run está em ``queued``, pronto p/ start_run.
        """
        run, session_id, trajectory, _adapter, _payload = self._open_run(
            request, _NO_EXECUTION,  # sentinela: sem resolução de adapter
        )
        _decision, early = self._preflight(request, run, session_id, trajectory)
        return run, early

    def stream(self, request: KernelRequest, *, executor: Any | None = None):
        """Generator: mesma máquina de estados de ``execute``, mas re-emite os
        deltas do adapter/executor conforme chegam — para front-ends de
        streaming (SSE do serve, chat interativo).

        ``executor`` opcional: callable(payload) -> Iterator[dict] (contrato
        de ``stream_agent``: eventos ``message.delta``/``run.completed``/
        ``run.failed``). Sem ele, resolve o adapter e chama ``stream_agent``.

        Cada item gerado é ``{"event": ...}``. O ÚLTIMO item sempre tem
        ``event: "final"`` com o ``KernelRun`` completo (mesmo em falha).

        ESCOPO REDUZIDO (v1, Sprint 6a): sem retry/fallback de executor no
        laço de streaming — tokens já entregues ao caller não podem ser
        "desmostrados"; reexecutar transparentemente duplicaria a saída
        parcial já exibida. Retry/fallback continuam completos em
        ``execute()``. O gate do Evaluator roda no final, sobre o texto
        agregado (mesma semântica de ``execute``, sem replan em streaming —
        replan reabriria running e re-emitiria do zero, confuso em UI).
        """
        run, session_id, trajectory, adapter, payload = self._open_run(request, executor)
        decision, early = self._preflight(request, run, session_id, trajectory)
        if early is not None:
            yield {"event": "final", "run": early}
            return

        self.runs.start_run(run.id)
        trajectory.append("running")

        chunks: list[str] = []
        last_meta: dict[str, Any] = {}
        error: str | None = None
        try:
            source = executor(payload) if executor is not None else adapter.stream_agent(payload)
            for evt in source:
                evt = evt or {}
                kind = evt.get("event")
                if kind == "message.delta":
                    content = str(evt.get("content", ""))
                    chunks.append(content)
                    yield {"event": "message.delta", "content": content}
                elif kind == "run.failed":
                    error = str(evt.get("error") or "executor failed")
                    break
                elif kind in ("run.completed", "run.started"):
                    last_meta = evt  # metadados (tool_calls_count, cost) — o
                    # "final" do kernel já sinaliza início/fim; não re-emite
                else:
                    # passthrough (6c): eventos intermediários do executor —
                    # tool/fase/rota — atravessam para o front-end (SSE) sem o
                    # kernel opinar sobre o formato deles
                    last_meta = evt
                    yield evt
        except GeneratorExit:
            # Caller abandonou o stream (desconexão SSE, .close()) — sem isto o
            # run ficaria preso em `running` até o recover() (15min). BaseException,
            # então o `except Exception` abaixo não o captura.
            self.runs.update_run(run.id, status="cancelled",
                                 error="stream interrompido pelo cliente")
            raise
        except Exception as exc:  # noqa: BLE001 — falha do executor é estado, não crash
            error = str(exc)

        if error is not None:
            self.runs.fail_run(run.id, error)
            trajectory.append("failed")
            yield {"event": "final",
                  "run": self._result(run.id, session_id, trajectory, decision=decision,
                                      output="".join(chunks))}
            return

        result = {"output": "".join(chunks), **{k: v for k, v in last_meta.items()
                                                 if k not in {"event", "status", "run_id", "runtime_adapter"}}}

        if self.evaluator is not None:
            self._transition(run, "evaluating", trajectory)
            verdict = self.evaluator.evaluate(run_id=run.id, request=request, result=result)
            if not getattr(verdict, "passed", True):
                self.runs.fail_run(run.id, f"quality gate: {getattr(verdict, 'reason', '')}")
                trajectory.append("failed")
                yield {"event": "final",
                      "run": self._result(run.id, session_id, trajectory, decision=decision,
                                          output=result.get("output"))}
                return

        cost = self._extract_cost(result)
        self.runs.complete_run(run.id, output={"output": result.get("output")},
                               cost_estimate=cost,
                               tool_calls_count=int(result.get("tool_calls_count") or 0))
        trajectory.append("completed")
        self._record_cost(run, cost)
        yield {"event": "final",
              "run": self._result(run.id, session_id, trajectory, decision=decision,
                                  output=result.get("output"))}

    def continue_run(self, run_id: str, *, extra_input: dict[str, Any] | None = None,
                     executor: Any | None = None) -> KernelRun:
        """Continua um run em ``queued`` (após resume/aprovação) até o fim.

        ``extra_input`` re-injeta objetos vivos que não persistem (ex.: client
        do bauer_native). O payload persiste saneado; a execução usa o real.
        """
        run = self._require_run(run_id)
        ensure_transition(run.status, "running")
        payload = {**(run.input or {}), **(extra_input or {}), "run_id": run.id}
        return self._run_to_completion(run, payload, run.session_id, [run.status],
                                       executor=executor, adapter=None,
                                       decision=None, request=None)

    # ── aprovações (Sprint 3) ────────────────────────────────────────────────

    def approve(self, approval_id: str, *, continue_with: dict[str, Any] | None = None,
                executor: Any | None = None) -> KernelRun | dict[str, Any]:
        """Aprova e retoma: waiting_approval → queued (→ execução, se possível).

        Sem ApprovalManager injetado, KeyError. Retorna o KernelRun final se a
        continuação rodou; senão o dict do resume (run fica queued).
        """
        if self.approvals is None:
            raise RuntimeError("ApprovalManager não injetado no Kernel")
        record = self.approvals.approve(approval_id)
        if not record.run_id:
            return {"approval_id": approval_id, "status": "approved", "run_id": None}
        resumed = self.resume(record.run_id)
        try:
            return self.continue_run(record.run_id, extra_input=continue_with,
                                     executor=executor)
        except Exception:  # noqa: BLE001 — sem payload executável fica queued
            return resumed

    def deny(self, approval_id: str) -> dict[str, Any]:
        """Nega a aprovação: waiting_approval → failed (policy denied)."""
        if self.approvals is None:
            raise RuntimeError("ApprovalManager não injetado no Kernel")
        record = self.approvals.deny(approval_id)
        if record.run_id:
            run = self.runs.get_run(record.run_id)
            if run is not None and run.status == "waiting_approval":
                self.runs.fail_run(record.run_id, f"aprovação negada: {record.reason}")
        return {"approval_id": approval_id, "status": "denied", "run_id": record.run_id}

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

    def recover(self, *, max_age_s: int = 900) -> list[dict[str, Any]]:
        """Recuperação pós-restart: runs presos em estados não-terminais há mais
        de ``max_age_s`` são marcados como failed (RuntimeRecovery existente) —
        prontos para re-submissão pelo caller."""
        recovery = self.recovery
        if recovery is None:
            from ..runtime.resilience import RuntimeRecovery
            recovery = RuntimeRecovery(store=self.runs.store)
        return recovery.recover_stuck_runs(max_age_s=max_age_s)

    # ── fase de execução (compartilhada por execute e continue_run) ──────────

    def _run_to_completion(self, run: Any, payload: dict[str, Any], session_id: str,
                           trajectory: list[str], *, executor: Any | None,
                           adapter: Any | None, decision: Any, request: Any) -> KernelRun:
        max_retries = max(0, int(getattr(request, "max_retries", 0) or 0))
        backoff_s = max(0.0, float(getattr(request, "retry_backoff_s", 0.0) or 0.0))
        fallbacks = list(getattr(request, "fallback_adapters", None) or [])

        if adapter is None and executor is None:
            adapter = self._adapter_for(run)

        max_replans = (max(0, int(getattr(self.evaluator, "max_replans", 0) or 0))
                       if self.evaluator is not None else 0)
        replans_used = 0

        # Laço de replan (Sprint 5): executa → avalia; gate reprovado com
        # orçamento volta a planning e re-executa com feedback. Uma volta só
        # quando não há evaluator.
        while True:
            self.runs.start_run(run.id)  # → running (evento run.started existente)
            trajectory.append("running")

            # Laço de resiliência (Sprint 4): até max_retries no MESMO executor
            # (estado retrying auditável), depois fallback de executor em ordem —
            # cada fallback ganha seu próprio orçamento de retries.
            attempt = 0
            last_error = ""
            while True:
                try:
                    result = (executor(payload) if executor is not None
                              else adapter.run_agent(payload)) or {}
                    if result.get("status") == "failed" or result.get("event") == "run.failed":
                        last_error = str(result.get("error") or "executor failed")
                    else:
                        break  # sucesso
                except Exception as exc:  # noqa: BLE001 — falha do executor é estado, não crash
                    last_error = str(exc)
                    result = {}

                if attempt < max_retries:
                    attempt += 1
                    self._transition(run, "retrying", trajectory)
                    if backoff_s > 0:
                        import time
                        time.sleep(backoff_s * attempt)  # backoff linear
                    self._transition(run, "queued", trajectory)
                    self.runs.start_run(run.id)
                    trajectory.append("running")
                    continue

                switched = False
                while fallbacks:
                    next_name = fallbacks.pop(0)
                    try:
                        adapter = self.adapter_factory(next_name, config=self.config)
                    except Exception as exc:  # noqa: BLE001 — tenta o próximo da lista
                        last_error = f"{last_error}; fallback '{next_name}' indisponível: {exc}"
                        continue
                    executor = None      # fallback é sempre via adapter
                    attempt = 0          # orçamento de retries zerado p/ o novo executor
                    self.runs.update_run(run.id, runtime_adapter=next_name)
                    self._publish("run.state.changed", run, status="running",
                                  message=f"fallback de executor → {next_name}",
                                  data={"fallback_adapter": next_name})
                    switched = True
                    break
                if switched:
                    continue

                self.runs.fail_run(run.id, last_error)
                trajectory.append("failed")
                return self._result(run.id, session_id, trajectory, decision=decision,
                                    output=result.get("output"))

            # evaluating — quality gate antes de concluir (Sprint 5; None = pula)
            if self.evaluator is None:
                break
            self._transition(run, "evaluating", trajectory)
            verdict = self.evaluator.evaluate(run_id=run.id, request=request, result=result)
            if getattr(verdict, "passed", True):
                break
            reason = getattr(verdict, "reason", "")
            if replans_used >= max_replans:
                self.runs.fail_run(run.id, f"quality gate: {reason}")
                trajectory.append("failed")
                return self._result(run.id, session_id, trajectory, decision=decision,
                                    output=result.get("output"))
            # replan: evaluating → planning → policy_check → queued → running,
            # com o motivo do gate no payload p/ o executor corrigir o rumo.
            replans_used += 1
            self._transition(run, "planning", trajectory)
            self._transition(run, "policy_check", trajectory)
            self._transition(run, "queued", trajectory)
            payload = {**payload, "replan_feedback": reason,
                       "replan_attempt": replans_used}

        cost = self._extract_cost(result)
        self.runs.complete_run(run.id, output={"output": result.get("output")},
                               cost_estimate=cost,
                               tool_calls_count=int(result.get("tool_calls_count") or 0))
        trajectory.append("completed")
        self._record_cost(run, cost)
        return self._result(run.id, session_id, trajectory, decision=decision,
                            output=result.get("output"))

    # ── helpers ───────────────────────────────────────────────────────────────

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

    def _request_approval(self, request: KernelRequest, run: Any, decision: Any) -> str | None:
        """ApprovalRecord real quando há manager (ele publica approval.requested);
        senão só o evento — o run fica waiting_approval de qualquer forma."""
        if self.approvals is not None:
            record = self.approvals.request(
                operation=request.operation, tool_name="kernel",
                reason=decision.reason, risk_level=decision.risk_level,
                payload={"agent_id": request.agent_id, **request.metadata},
                run_id=run.id, session_id=run.session_id,
            )
            return record.id
        self._publish("approval.requested", run, message=decision.reason,
                      data={"operation": request.operation})
        return None

    def _extract_cost(self, result: dict[str, Any]) -> float | None:
        try:
            raw = result.get("cost_estimate") or result.get("cost_usd")
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def _record_cost(self, run: Any, cost: float | None) -> None:
        if self.budget is None or not cost:
            return
        try:
            self.budget.record_run_cost(run_id=run.id, agent_id=run.agent_id,
                                        company_id=None, cost_usd=cost,
                                        metadata={"source": "kernel"})
        except Exception as exc:  # noqa: BLE001 — contabilidade nunca derruba o run
            from ...logging_config import log_suppressed
            log_suppressed("kernel.record_cost", exc)

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

    def _require_run(self, run_id: str) -> Any:
        run = self.runs.get_run(run_id)
        if run is None:
            raise KeyError(f"Run not found: {run_id}")
        return run

    def _adapter_for(self, run: Any) -> Any:
        return self.adapter_factory(getattr(run, "runtime_adapter", None) or None,
                                    config=self.config)

    def _result(self, run_id: str, session_id: str, trajectory: list[str], *,
                decision: Any = None, output: Any = None,
                approval_id: str | None = None) -> KernelRun:
        run = self.runs.get_run(run_id)
        return KernelRun(
            run_id=run_id,
            session_id=session_id,
            status=run.status,
            output=output if output is not None else (run.output or {}).get("output"),
            error=run.error,
            policy_action=getattr(decision, "action", None),
            policy_reason=getattr(decision, "reason", None),
            approval_id=approval_id,
            trajectory=trajectory,
        )


# ── composição padrão + flag ──────────────────────────────────────────────────


def kernel_enabled(cfg: Any) -> bool:
    """True se ``kernel.enabled`` está ligado no config (default: False)."""
    try:
        return bool(getattr(getattr(cfg, "kernel", None), "enabled", False))
    except Exception:  # noqa: BLE001
        return False


def evaluator_from_config(cfg: Any):
    """Evaluator montado a partir de ``kernel.evaluator_enabled``/``max_replans``
    do config; None quando desligado. Aceita o config inteiro ou só a seção."""
    ksec = getattr(cfg, "kernel", cfg)
    if not bool(getattr(ksec, "evaluator_enabled", False)):
        return None
    from .evaluator import Evaluator
    return Evaluator(max_replans=int(getattr(ksec, "max_replans", 1) or 0))


def build_kernel(cfg: Any | None = None, *, root: str = "memory/runtime",
                 workspace: str = "workspace", with_policy: bool = True) -> BauerKernel:
    """Composição padrão do Kernel com os componentes existentes (produção)."""
    from ..events.bus import EventBus
    from ..policy.approvals import ApprovalManager
    from ..runtime.autonomy import BudgetManager
    from ..runtime.resilience import RuntimeControl, RuntimeRecovery
    from ..runtime.run_manager import RunManager
    from ..runtime.state_store import JsonlStateStore

    store = JsonlStateStore(root)
    bus = EventBus(store=store)
    runs = RunManager(store=store, event_bus=bus)
    policy = None
    if with_policy:
        from ..policy.engine import PolicyEngine
        policy = PolicyEngine(workspace=workspace, runtime_root=root)
    return BauerKernel(
        runs=runs, bus=bus, policy=policy, config=cfg,
        evaluator=evaluator_from_config(cfg),
        control=RuntimeControl(store=store),
        approvals=ApprovalManager(root=root, event_bus=bus),
        budget=BudgetManager(root=root),
        recovery=RuntimeRecovery(store=store),
    )
