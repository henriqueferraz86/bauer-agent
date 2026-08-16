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
from .states import KERNEL_ONLY_STATES, KernelStateError, ensure_transition


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
        breaker: Any | None = None,          # bauer.circuit_breaker.CircuitBreaker
        contract: Any | None = None,         # core.task.TaskContract (S12 nível 3)
        heartbeat: Any | None = None,        # core.runtime.resilience.RunHeartbeat
        heartbeat_interval_s: float | None = None,
    ) -> None:
        self.runs = runs
        # Contrato da tarefa, lido do workspace ANTES do run. Só o nível 3 do
        # isolamento (aprovação humana) depende dele aqui; escopo, aceite e diff
        # já chegam pelos gates do Evaluator. Snapshot pela mesma razão do
        # AcceptanceGate: reler do disco deixaria o agente desligar a própria
        # exigência de aprovação no meio da execução.
        self.contract = contract
        self.bus = bus or getattr(runs, "event_bus", None)
        self.policy = policy
        self.config = config
        self.evaluator = evaluator
        self.control = control
        self.approvals = approvals
        self.budget = budget
        self.recovery = recovery
        # None = sem circuito (comportamento anterior). Com breaker, executor
        # que falha sistematicamente para de ser tentado a cada run.
        self.breaker = breaker
        # Pulso de vida do run. Montado por DEFAULT sobre o mesmo store dos runs:
        # é proteção contra o recovery matar run vivo, e proteção que depende de
        # alguém lembrar de injetar é proteção que um dia não está lá. Passe
        # `heartbeat_interval_s=0` para desligar.
        from ..runtime.resilience import INTERVALO_PULSO_PADRAO_S, RunHeartbeat
        self.heartbeat = (heartbeat if heartbeat is not None
                          else RunHeartbeat(store=runs.store))
        self.heartbeat_interval_s = (INTERVALO_PULSO_PADRAO_S
                                     if heartbeat_interval_s is None
                                     else float(heartbeat_interval_s))
        if adapter_factory is None:
            from ..runtime.adapters import get_runtime_adapter
            adapter_factory = get_runtime_adapter
        self.adapter_factory = adapter_factory

    def _pulso(self, run_id: str):
        """Contexto que mantém o run "vivo" aos olhos do recovery.

        Cobre exatamente os trechos em que o Kernel está bloqueado dentro de
        código de terceiros — o executor e os gates — que são justamente os que
        podem passar dos 900s sem nenhuma transição de estado.
        """
        if self.heartbeat is None or self.heartbeat_interval_s <= 0:
            import contextlib
            return contextlib.nullcontext()
        return self.heartbeat.pulso(run_id, intervalo_s=self.heartbeat_interval_s)

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
        # Persistido no run: sem isso, "quantos runs autônomos tinham contrato?"
        # só daria para responder olhando o código, e a resposta mudaria a cada
        # refactor. Gravado, vira contagem sobre o histórico real.
        if request.autonomous:
            stored_input["autonomous"] = True
            stored_input["task_contract"] = self.contract is not None
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
        self._publish("run.planning.started", run, status="planning",
                      data={"operation": request.operation, "replan": False})
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

        # S12 nível 3 — aprovação humana pedida pelo CONTRATO da tarefa.
        # `waiting_approval` já existia completo (transição, approve, deny,
        # resume) e `TaskContract.requires_approval` já existia no schema; o que
        # não existia era o fio entre os dois. Medido: o campo estava declarado
        # e NUNCA era lido — quem escrevesse `requires_approval: true` num
        # contrato ganharia a falsa certeza de que a tarefa pararia para pedir.
        motivo = _exige_aprovacao(self.contract)
        if motivo:
            self._transition(run, "waiting_approval", trajectory)
            approval_id = self._pedir_aprovacao_do_contrato(request, run, motivo)
            return decision, self._result(run.id, session_id, trajectory,
                                          decision=decision, approval_id=approval_id)

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
        with self._pulso(run.id):
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
        # _open_run devolve o snapshot de `created`; o preflight já transicionou
        # até `queued` NO STORE. Devolver o objeto velho fazia `run.status` dizer
        # "created" contra um estado persistido "queued" — a docstring acima
        # promete queued, e um caller que confiasse nela leria errado.
        return (self.runs.get_run(run.id) or run), early

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
        #: metadados do run (tool_calls_count, cost) — SÓ de run.completed/started.
        #: Era a mesma variável que ecoava os eventos passthrough, e por isso um
        #: `tool.finished` chegando DEPOIS do `run.completed` sobrescrevia custo e
        #: contagem de tools: o run concluía com cost=None e tool_calls_count=0, e
        #: o BudgetManager subcontava todo run via streaming. Duas
        #: responsabilidades, duas variáveis.
        meta: dict[str, Any] = {}
        error: str | None = None
        # Pulso durante o consumo do stream: entre um delta e o próximo não há
        # transição de estado, e um turno longo passaria dos 900s parecendo
        # travado. O `finally` do contexto fecha a thread em TODAS as saídas
        # daqui — inclusive o GeneratorExit da desconexão.
        with self._pulso(run.id):
            try:
                source = (executor(payload) if executor is not None
                          else adapter.stream_agent(payload))
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
                        meta.update(evt)  # o "final" do kernel já sinaliza
                        # início/fim; não re-emite
                    else:
                        # passthrough (6c): eventos intermediários do executor —
                        # tool/fase/rota — atravessam para o front-end (SSE) sem
                        # o kernel opinar sobre o formato deles
                        yield evt
            except GeneratorExit:
                # Caller abandonou o stream (desconexão SSE, .close()) — sem isto
                # o run ficaria preso em `running` até o recover() (15min).
                # BaseException, então o `except Exception` abaixo não o captura.
                self._cancelar_se_nao_terminal(run.id, "stream interrompido pelo cliente")
                raise
            except Exception as exc:  # noqa: BLE001 — falha do executor é estado, não crash
                error = str(exc)

            if error is not None:
                # `_fail_se_nao_terminal`, não `fail_run`: no /stream dois
                # escritores mexem no mesmo run — o gerador SSE e a thread órfã do
                # turno. Se a thread conclui PRIMEIRO, um fail_run cru apagava o
                # `completed` de um trabalho que deu certo. É a corrida medida no
                # CI em 2026-07-30 e já resolvida em `_run_to_completion`.
                self._fail_se_nao_terminal(run.id, error, trajectory)
                yield {"event": "final",
                      "run": self._result(run.id, session_id, trajectory, decision=decision,
                                          output="".join(chunks))}
                return

            result = {"output": "".join(chunks), **{k: v for k, v in meta.items()
                                                     if k not in {"event", "status", "run_id", "runtime_adapter"}}}

            # Dentro do pulso de propósito: os gates são a outra metade do
            # problema. AcceptanceGate e TestsGate valem 600s cada por default —
            # sozinhos passam do `max_age_s` do recovery, sem uma única transição
            # de estado no meio.
            if self.evaluator is not None:
                verdict = self._avaliar(run, request, result, trajectory)
                if not getattr(verdict, "passed", True):
                    self._fail_se_nao_terminal(
                        run.id, f"quality gate: {getattr(verdict, 'reason', '')}", trajectory)
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
        with self._pulso(run.id):
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
        except KernelStateError:
            # Run não está num estado continuável — desfecho DOCUMENTADO desta
            # função: fica queued e alguém retoma depois.
            return resumed
        except Exception as exc:  # noqa: BLE001 — ver abaixo: falha, não silêncio
            # Qualquer outra coisa é bug nosso ou do executor. O `except
            # Exception` que existia aqui engolia tudo e devolvia "queued" — o
            # usuário aprovava, a API respondia sucesso, nada rodava, e o run
            # ficava num estado não-terminal até o recover() de 15min convertê-lo
            # em "stuck for more than 900s", que é a causa ERRADA. Aprovação
            # humana virando no-op silencioso é o pior desfecho possível.
            self._fail_se_nao_terminal(
                record.run_id,
                f"falha ao continuar após aprovação: {type(exc).__name__}: {exc}",
                [],
            )
            raise

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
        #: assinatura da saída da passada anterior — §13, "plano sem mudança
        #: entre replans". Replanar custa um orçamento inteiro de novo; gastá-lo
        #: para produzir exatamente a mesma coisa é o pior negócio do Kernel.
        assinatura_anterior: "str | None" = None

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
                alvo = self._nome_do_executor(executor, adapter)
                if (self.breaker is not None and executor is None
                        and self.breaker.is_open(alvo)):
                    # Circuito ABERTO: este executor já falhou o suficiente em
                    # runs ANTERIORES. Insistir custa o timeout inteiro para
                    # redescobrir o que já se sabe — e provider fora do ar afeta
                    # todos os runs, não só este. Vai direto ao fallback.
                    last_error = f"circuito aberto para '{alvo}'"
                    result = {}
                else:
                    try:
                        result = (executor(payload) if executor is not None
                                  else adapter.run_agent(payload)) or {}
                        if result.get("status") == "cancelled":
                            # Interrupção deliberada no MEIO da execução
                            # (kill-switch entre rodadas, Ctrl+C). NÃO é falha:
                            # retry, fallback, gates e replan estariam todos
                            # errados — ninguém pediu para insistir. E não conta
                            # contra o circuito: o executor não falhou.
                            self.runs.update_run(run.id, status="cancelled",
                                                 error=str(result.get("error") or "cancelado"))
                            trajectory.append("cancelled")
                            return self._result(run.id, session_id, trajectory,
                                                decision=decision, output=result.get("output"))
                        if (result.get("status") == "failed"
                                or result.get("event") == "run.failed"):
                            last_error = str(result.get("error") or "executor failed")
                            self._registrar_falha(alvo)
                        else:
                            self._registrar_sucesso(alvo)
                            break  # sucesso
                    except Exception as exc:  # noqa: BLE001 — falha do executor é estado, não crash
                        last_error = str(exc)
                        result = {}
                        self._registrar_falha(alvo, exc)

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
                    if self.breaker is not None and self.breaker.is_open(next_name):
                        # Circuito aberto: este executor já falhou o suficiente
                        # nos runs ANTERIORES. Tentar de novo custa o timeout
                        # inteiro para redescobrir o que já se sabe — e é o
                        # cenário em que fallback mais importa (provider fora do
                        # ar afeta todos os runs, não só este).
                        last_error = f"{last_error}; fallback '{next_name}' com circuito aberto"
                        self._publish("run.state.changed", run, status="running",
                                      message=f"fallback '{next_name}' pulado: circuito aberto",
                                      data={"circuit_open": next_name})
                        continue
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

                self._fail_se_nao_terminal(run.id, last_error, trajectory)
                return self._result(run.id, session_id, trajectory, decision=decision,
                                    output=result.get("output"))

            # evaluating — quality gate antes de concluir (Sprint 5; None = pula)
            if self.evaluator is None:
                break
            verdict = self._avaliar(run, request, result, trajectory)
            if getattr(verdict, "passed", True):
                break
            reason = getattr(verdict, "reason", "")

            # §13: plano sem mudança entre replans. O replan anterior recebeu o
            # feedback do gate e devolveu byte a byte a mesma coisa — o executor
            # não incorporou nada. Insistir gasta outro orçamento inteiro para
            # chegar ao mesmo lugar, e o usuário paga a espera em tempo real.
            assinatura = _assinatura(result)
            esteril = bool(replans_used) and assinatura == assinatura_anterior
            if esteril:
                self._publish(
                    "run.progress.warning", run, status="evaluating",
                    message=("replan não mudou nada: mesma saída depois do "
                             "feedback do gate"),
                    data={"code": "plano_sem_mudanca", "replans": replans_used})

            # Um único ponto de desfecho para os dois motivos de desistir. Duas
            # chamadas a `fail_run` seriam duas formas de fechar um run dentro
            # do mesmo laço — exatamente o que o ratchet de custódia existe para
            # não deixar crescer.
            if esteril or replans_used >= max_replans:
                extra = (f" (replan {replans_used} devolveu a mesma saída — o "
                         f"feedback não foi incorporado)" if esteril else "")
                self.runs.fail_run(run.id, f"quality gate: {reason}{extra}")
                trajectory.append("failed")
                return self._result(run.id, session_id, trajectory, decision=decision,
                                    output=result.get("output"))
            assinatura_anterior = assinatura
            # replan: evaluating → planning → policy_check → queued → running,
            # com o motivo do gate no payload p/ o executor corrigir o rumo.
            replans_used += 1
            self._publish("run.replanning", run, status="evaluating", message=reason,
                          data={"attempt": replans_used, "max_replans": max_replans})
            self._transition(run, "planning", trajectory)
            # `request` é None quando se chega aqui por continue_run() — um run
            # ADMITIDO (admit()) continuado por outra thread, que é como o /loop
            # e o /v1 do serve funcionam. O resto da função já lê `request` por
            # getattr pela mesma razão (max_retries/backoff/fallbacks acima);
            # este acesso direto era o único que restava, e derrubava justamente
            # o caminho que `continue_governed` documenta como completo.
            self._publish("run.planning.started", run, status="planning",
                          data={"operation": getattr(request, "operation",
                                                     "runtime.execute"),
                                "replan": True, "attempt": replans_used})
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

    @staticmethod
    def _nome_do_executor(executor: Any, adapter: Any) -> str:
        """Chave do circuito. Só adapters entram: um ``executor`` injetado é o
        motor in-process do próprio Bauer (o laço de turno), não um provider —
        abrir circuito contra ele puniria o Bauer pelo erro do modelo."""
        if executor is not None:
            return ""
        return str(getattr(adapter, "name", "") or "bauer_native")

    def _registrar_sucesso(self, alvo: str) -> None:
        if self.breaker is not None and alvo:
            self.breaker.record_success(alvo)

    def _registrar_falha(self, alvo: str, exc: "BaseException | None" = None) -> None:
        if self.breaker is not None and alvo:
            self.breaker.record_failure(alvo, exc)

    def _avaliar(self, run: Any, request: Any, result: dict, trajectory: list[str]) -> Any:
        """`evaluating` + os dois eventos de validação, num lugar só.

        Estava duplicado entre ``execute`` e ``stream``, e a duplicata é
        exatamente como a validação de um dos dois ficaria muda sem ninguém
        notar — o gate continuaria reprovando, mas a auditoria não saberia por
        quê. Publica QUAIS gates rodaram: sem isso, "validation.failed" diz que
        reprovou e esconde onde.
        """
        self._transition(run, "evaluating", trajectory)
        nomes = [getattr(g, "name", "gate") for g in getattr(self.evaluator, "gates", [])]
        self._publish("run.validation.started", run, status="evaluating",
                      data={"gates": nomes})
        verdict = self.evaluator.evaluate(run_id=run.id, request=request, result=result)
        if not getattr(verdict, "passed", True):
            self._publish(
                "run.validation.failed", run, status="evaluating",
                message=getattr(verdict, "reason", ""),
                data={"reprovados": [g.gate for g in getattr(verdict, "gates", [])
                                     if not g.passed]},
            )
        return verdict

    def _fail_se_nao_terminal(self, run_id: str, error: str,
                              trajectory: list[str]) -> None:
        """fail_run que NÃO sobrescreve um desfecho já decidido.

        ``RunManager.cancel_run`` protege terminais; ``fail_run`` não. Isso abre
        uma corrida real: o usuário cancela (``/loop/{id}/stop``) enquanto o
        executor está retornando falha — e o ``failed`` apagava o ``cancelled``.
        Cancelamento é decisão de quem manda; falha é consequência. A decisão
        vence.
        """
        from ..runtime.run_manager import TERMINAL_RUN_STATUSES
        atual = self.runs.get_run(run_id)
        if atual is not None and atual.status in TERMINAL_RUN_STATUSES:
            trajectory.append(atual.status)
            return
        self.runs.fail_run(run_id, error)
        trajectory.append("failed")

    def _cancelar_se_nao_terminal(self, run_id: str, motivo: str) -> None:
        """`cancelled` que NÃO sobrescreve um desfecho já decidido.

        Mesma regra de ``_fail_se_nao_terminal`` — quem chega primeiro ao
        terminal decide — aplicada à desconexão do cliente SSE. O cliente
        fechar a conexão depois de o trabalho concluir não desfaz o trabalho.
        """
        from ..runtime.run_manager import TERMINAL_RUN_STATUSES
        atual = self.runs.get_run(run_id)
        if atual is None or atual.status in TERMINAL_RUN_STATUSES:
            return
        self.runs.update_run(run_id, status="cancelled", error=motivo)

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

    def _pedir_aprovacao_do_contrato(self, request: KernelRequest, run: Any,
                                     motivo: str) -> "str | None":
        """Aprovação exigida pelo CONTRATO, não pela policy.

        Reusa o mesmo ApprovalManager e o mesmo `bauer kernel approve/deny`: um
        segundo mecanismo de aprovação em paralelo seria a fragmentação que o
        Kernel existe para eliminar. O que muda é a ORIGEM, e ela vai no
        payload — quem audita precisa distinguir "a policy pediu" de "a tarefa
        se declarou arriscada".
        """
        if self.approvals is not None:
            record = self.approvals.request(
                operation=request.operation, tool_name="kernel",
                reason=motivo, risk_level=str(
                    getattr(self.contract, "risk_level", "") or "medium"),
                payload={"agent_id": request.agent_id, "origem": "task_contract",
                         **request.metadata},
                run_id=run.id, session_id=run.session_id,
            )
            return record.id
        self._publish("approval.requested", run, message=motivo,
                      data={"operation": request.operation, "origem": "task_contract"})
        return None

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


class KernelWiringError(RuntimeError):
    """``kernel.enabled: true`` pedido, mas a composição do Kernel falhou.

    Erro, não aviso: governança pedida e silenciosamente não entregue é o pior
    resultado possível — o run roda ingovernado com a config afirmando o
    contrário.
    """


def require_kernel(cfg: Any, build_fn: Any, *, label: str) -> "BauerKernel | None":
    """Aplica a semântica da flag num só lugar.

    - ``kernel.enabled`` desligado → ``None``, caminho legado intocado.
    - ligado → ``build_fn()``; qualquer falha vira :class:`KernelWiringError`.

    Existe porque os call sites embrulhavam ``build_kernel`` em
    ``except Exception: log_suppressed(...)``. Com a flag LIGADA isso degradava
    para execução ingovernada sem ninguém perceber — o log é gravado, mas o run
    prossegue como se nada fosse. Medir cobertura do Kernel nessas condições é
    impossível: o número mede o que o wiring conseguiu, não o que foi pedido.

    ``label`` identifica o call site na mensagem de erro (ex.: ``"bauer run"``).
    """
    if not kernel_enabled(cfg):
        return None
    try:
        return build_fn()  # type: ignore[no-any-return]
    except Exception as exc:  # noqa: BLE001 — reembalado com contexto do call site
        raise KernelWiringError(
            f"kernel.enabled: true, mas a composição do Kernel falhou em {label}: "
            f"{type(exc).__name__}: {exc}. Corrija a config ou desligue "
            f"kernel.enabled — rodar ingovernado em silêncio não é opção."
        ) from exc


def evaluator_from_config(cfg: Any, *, workspace: "str | None" = None):
    """Evaluator montado a partir do config; None quando desligado.

    Aceita o config inteiro ou só a seção. ``workspace`` habilita os gates que
    precisam olhar o projeto — hoje o de testes (S11), que sem ele não teria
    onde rodar.
    """
    ksec = getattr(cfg, "kernel", cfg)
    if not bool(getattr(ksec, "evaluator_enabled", False)):
        return None
    from .evaluator import DEFAULT_GATES, Evaluator

    gates = list(DEFAULT_GATES)
    if workspace:
        from ..task import TaskContract
        contrato = TaskContract.descobrir(workspace)
        # ScopeGate entra sozinho quando HÁ contrato com escopo: quem escreveu o
        # perímetro quer que ele valha, sem precisar ligar uma segunda flag.
        if contrato is not None and contrato.tem_escopo:
            from .gates import ScopeGate
            gates.append(ScopeGate(workspace, contrato))
        if contrato is not None:
            # Os dois que só fazem sentido com contrato — e por isso não
            # precisam de flag: escrever um contrato de tarefa JÁ é o opt-in.
            # O contrato é passado como SNAPSHOT (lido aqui, antes do run):
            # reler do disco depois deixaria o agente reescrever o próprio
            # critério de aprovação.
            from .gates import AcceptanceGate, DiffGate
            gates.append(DiffGate(workspace, contrato))
            gates.append(AcceptanceGate(
                contrato, workspace,
                timeout_s=int(contrato.validation.timeout_seconds or 600)))
        # SecretsGate vale SEMPRE que há workspace: segredo no diff não depende
        # de a tarefa ter contrato, e é o único gate cujo custo de falso-negativo
        # (credencial commitada) não tem desfazer.
        from .gates import SecretsGate
        gates.append(SecretsGate(workspace))
        if bool(getattr(ksec, "tests_gate", False)):
            from .gates import TestsGate
            # o contrato manda no teto de tempo — é ele que conhece a tarefa;
            # o config é o default de quem não declarou contrato
            timeout = int(getattr(ksec, "tests_gate_timeout_s", 600) or 600)
            if contrato is not None:
                timeout = int(contrato.validation.timeout_seconds or timeout)
            gates.append(TestsGate(
                workspace, timeout_s=timeout,
                modo=str(getattr(ksec, "tests_gate_mode", "regressao") or "regressao"),
            ))
    return Evaluator(gates, max_replans=int(getattr(ksec, "max_replans", 1) or 0))


#: Níveis de risco que, sozinhos, param a tarefa para um humano decidir.
#: `high` e `critical` só; `medium` é o DEFAULT do contrato e parar em tudo que
#: não declarou risco transformaria o Kernel num pedido de confirmação por run.
_RISCO_QUE_PEDE_HUMANO = {"high", "critical"}


def _exige_aprovacao(contrato: Any) -> str:
    """Motivo da parada, ou "" para seguir. Duas portas independentes.

    `requires_approval: true` é a explícita — quem escreveu quer que pare.
    `risk_level: high|critical` é a implícita: declarar risco alto e não parar
    faria o campo ser decoração, que é exatamente o que ele era antes disto.
    """
    if contrato is None:
        return ""
    if bool(getattr(contrato, "requires_approval", False)):
        return "o contrato da tarefa exige aprovação humana (requires_approval)"
    risco = str(getattr(contrato, "risk_level", "") or "").lower()
    if risco in _RISCO_QUE_PEDE_HUMANO:
        return f"o contrato declara risk_level '{risco}'"
    return ""


def _assinatura(result: dict[str, Any]) -> str:
    """Digest da saída de uma passada — a base do sinal "replan não mudou nada".

    Só o texto e a contagem de tools: custo e duração variam entre execuções
    idênticas, e incluí-los faria toda passada parecer diferente, que é
    exatamente o oposto do que o sinal precisa detectar.
    """
    import hashlib

    bruto = f"{result.get('output') or ''}\0{result.get('tool_calls_count') or 0}"
    return hashlib.sha256(bruto.encode("utf-8", "replace")).hexdigest()


def build_kernel(cfg: Any | None = None, *, root: str = "memory/runtime",
                 workspace: str = "workspace", with_policy: bool = True,
                 bus: Any = None) -> BauerKernel:
    """Composição padrão do Kernel com os componentes existentes (produção).

    ``bus`` opcional: reaproveita um EventBus já criado pelo caller. Serve a
    quem precisa publicar ANTES do Kernel existir — o ``bauer run`` cria o
    worktree (e emite ``run.workspace.created``) antes de saber qual será o
    workspace do Kernel. Dois buses sobre o mesmo arquivo funcionariam, mas
    partiriam os subscribers em dois: quem assinasse no Kernel não veria os
    eventos do caller.
    """
    from ..events.bus import EventBus
    from ..policy.approvals import ApprovalManager
    from ..runtime.autonomy import BudgetManager
    from ..runtime.resilience import RuntimeControl, RuntimeRecovery
    from ..runtime.run_manager import RunManager
    from ..runtime.state_store import JsonlStateStore

    store = getattr(bus, "store", None) or JsonlStateStore(root)
    bus = bus or EventBus(store=store)
    runs = RunManager(store=store, event_bus=bus)
    policy = None
    if with_policy:
        from ..policy.engine import PolicyEngine
        policy = PolicyEngine(workspace=workspace, runtime_root=root)
    from ...circuit_breaker import CircuitBreaker
    from ..task import TaskContract

    # Lido UMA vez, aqui, e passado adiante. O `evaluator_from_config` já fazia
    # a mesma leitura por dentro; agora o contrato também alimenta o nível 3 do
    # isolamento (aprovação humana), e ler duas vezes abriria a janela para os
    # dois enxergarem contratos diferentes.
    contrato = TaskContract.descobrir(workspace) if workspace else None

    return BauerKernel(
        runs=runs, bus=bus, policy=policy, config=cfg, contract=contrato,
        # Um breaker POR Kernel, nao global: o estado do circuito acompanha o
        # processo que executa. Threshold alto de proposito — abrir cedo demais
        # transformaria uma indisponibilidade curta em fallback permanente.
        breaker=CircuitBreaker(failure_threshold=5, reset_timeout=120.0),
        evaluator=evaluator_from_config(cfg, workspace=workspace),
        heartbeat_interval_s=getattr(getattr(cfg, "kernel", None),
                                     "heartbeat_interval_s", None),
        control=RuntimeControl(store=store),
        approvals=ApprovalManager(root=root, event_bus=bus),
        budget=BudgetManager(root=root),
        recovery=RuntimeRecovery(store=store),
    )
