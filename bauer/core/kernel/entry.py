"""RunEntry — a porta única de execução governada.

Cada front-end (CLI, serve, scheduler, worker, swarm, orchestrator, App Factory,
daemon) diz **o que** quer rodar; o RunEntry cuida do resto: montar o
``KernelRequest``, entregar a custódia ao Kernel, traduzir o desfecho de volta e
degradar para execução direta quando o Kernel está desligado.

Existe porque a migração de ``bauer run`` (S8/§9.3) mostrou que o padrão são
~40 linhas repetidas por caminho: construir o request, escrever o executor,
distinguir "a governança barrou antes de começar" de "rodou e terminou assim",
traduzir kill-switch e ``KeyboardInterrupt`` em cancelamento. Nove caminhos
copiando isso à mão seria nove chances de divergir — e divergência entre trilhos
de execução é exatamente o problema que o Kernel existe para resolver.

Nome deliberadamente NÃO é "ExecutionGateway": ``bauer/gateway.py`` já é o
WebSocket Gateway (protocolo Claw3D), com cinco módulos ``gateway_*`` ao redor, e
``bauer/execution_engine.py`` já existe. Ver docs/harness/PLANO_HARNESS_90.md §3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


#: desfechos que o executor pode reportar no dict de retorno
CANCELLED = "cancelled"
FAILED = "failed"


@dataclass
class GovernedResult:
    """Desfecho de uma execução governada, na forma que os front-ends usam."""

    status: str                       # completed | failed | cancelled | blocked
    run_id: str | None = None
    output: Any = None
    error: str | None = None
    #: True quando a governança barrou no preflight — o executor NUNCA rodou.
    #: Distingue "bloqueado" de "rodou e falhou", que os front-ends reportam de
    #: formas diferentes (exit code, HTTP status, evento schedule.skipped).
    blocked_before_start: bool = False
    #: False quando o Kernel estava desligado — execução direta, sem governança.
    governed: bool = False
    policy_reason: str | None = None
    approval_id: str | None = None
    result: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "completed"


def run_governed(
    kernel: Any,
    executor: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    agent_id: str,
    task: Any = None,
    input: "dict[str, Any] | None" = None,
    session_id: str | None = None,
    operation: str = "runtime.execute",
    max_retries: int = 0,
    retry_backoff_s: float = 0.0,
    fallback_adapters: "list[str] | None" = None,
    metadata: "dict[str, Any] | None" = None,
) -> GovernedResult:
    """Roda ``executor`` sob custódia do Kernel — ou direto, se não houver Kernel.

    ``executor(payload) -> dict`` recebe ``run_id`` no payload (útil para publicar
    eventos próprios do front-end durante a execução) e devolve:

        {"output": ..., "tool_calls_count": int, "cost_estimate": float,
         "status": "cancelled" | "failed" (opcional), "error": str (opcional)}

    Sem ``status``, o desfecho é sucesso e **o Kernel decide** se conclui — os
    quality gates rodam antes de ``completed``. Com ``status: cancelled``, é
    terminal na hora: cancelamento é intenção deliberada, então retry, fallback,
    gates e replan todos estariam errados ali.

    ``kernel=None`` (flag desligada) roda o executor direto e devolve
    ``governed=False``. O caminho legado fica intocado — é o contrato da flag.
    """
    payload_in = dict(input or {})

    if kernel is None:
        return _sem_kernel(executor, payload_in)

    from .schemas import KernelRequest

    rodou: list[int] = []
    # o KernelRun devolve só `output`; front-ends precisam do dict inteiro do
    # executor (custo, contagem de tools, payload próprio). Capturado aqui para
    # `result` valer nos DOIS caminhos — governado e direto.
    ultimo: dict[str, Any] = {}

    def _wrapped(payload: dict[str, Any]) -> dict[str, Any]:
        rodou.append(1)
        try:
            out = executor(payload) or {}
        except KeyboardInterrupt:
            # BaseException: o `except Exception` do Kernel NÃO pega, e o run
            # ficaria preso em `running` até o recover() de 15min.
            out = {"status": CANCELLED, "error": "interrompido pelo usuário (Ctrl+C)"}
        ultimo.clear()
        ultimo.update(out)
        return out

    krun = kernel.execute(
        KernelRequest(
            # KernelRequest usa "" como ausência (não None) — _open_run gera um
            # session-<uuid> quando vem vazio
            task=task, agent_id=agent_id, input=payload_in, session_id=session_id or "",
            operation=operation, max_retries=max_retries,
            retry_backoff_s=retry_backoff_s,
            fallback_adapters=list(fallback_adapters or []),
            metadata=dict(metadata or {}),
        ),
        executor=_wrapped,
    )

    if not rodou:
        # kill-switch, policy deny ou ask no preflight
        return GovernedResult(
            status="blocked", run_id=krun.run_id, error=krun.error,
            blocked_before_start=True, governed=True,
            policy_reason=krun.policy_reason, approval_id=krun.approval_id,
        )

    return GovernedResult(
        status=krun.status, run_id=krun.run_id, output=krun.output,
        error=krun.error, governed=True, policy_reason=krun.policy_reason,
        result=dict(ultimo),
    )


def continue_governed(
    kernel: Any,
    run_id: str,
    executor: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    extra_input: "dict[str, Any] | None" = None,
) -> GovernedResult:
    """Custódia para trabalho ASSÍNCRONO já admitido — ``admit()`` + custódia.

    O caso: endpoints que devolvem ``run_id`` na hora e seguem trabalhando numa
    thread (o ``/loop`` da web). ``execute()`` não serve porque ele cria o run por
    dentro, e o cliente HTTP precisa do id ANTES de o trabalho acabar. Então:

        handler:  run, early = kernel.admit(req)   -> run_id devolvido ao cliente
        worker:   continue_governed(kernel, run.id, executor)

    ``continue_run`` roda o mesmo ``_run_to_completion`` de ``execute()`` — logo
    os gates rodam, o replan vale e o Kernel é quem declara ``completed``. É a
    diferença entre "governado na entrada" e "governado de ponta a ponta" num
    caminho que não pode ser síncrono.

    ``kernel=None`` roda direto, como ``run_governed``.
    """
    if kernel is None:
        return _sem_kernel(executor, dict(extra_input or {}))

    ultimo: dict[str, Any] = {}

    def _wrapped(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            out = executor(payload) or {}
        except KeyboardInterrupt:
            out = {"status": CANCELLED, "error": "interrompido pelo usuário (Ctrl+C)"}
        ultimo.clear()
        ultimo.update(out)
        return out

    krun = kernel.continue_run(run_id, extra_input=extra_input, executor=_wrapped)
    return GovernedResult(
        status=krun.status, run_id=krun.run_id, output=krun.output,
        error=krun.error, governed=True, result=dict(ultimo),
    )


def _sem_kernel(executor, payload_in: dict[str, Any]) -> GovernedResult:
    """Kernel desligado: executa direto e traduz o dict de retorno."""
    try:
        result = executor(payload_in) or {}
    except KeyboardInterrupt:
        return GovernedResult(status=CANCELLED, error="interrompido pelo usuário (Ctrl+C)")
    status = str(result.get("status") or "completed")
    return GovernedResult(
        status=status, output=result.get("output"),
        error=str(result.get("error")) if result.get("error") else None,
        result=result,
    )
