"""Contratos do Bauer Kernel: KernelRequest (entrada) e KernelRun (saída)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class KernelRequest:
    """Pedido de execução para o Kernel — a ÚNICA porta de entrada.

    ``input`` é o payload passado ao runtime adapter (contrato existente de
    ``run_agent``: client, model, messages/task…). ``operation`` é o que a
    Policy Engine avalia antes de executar (default: runtime.execute, que já
    inclui o gate de orçamento do BudgetManager).
    """

    task: str = ""
    session_id: str = ""
    agent_id: str = "default"
    runtime_adapter: str = ""          # vazio → default do config (RuntimeSection)
    input: dict[str, Any] = field(default_factory=dict)
    operation: str = "runtime.execute"
    metadata: dict[str, Any] = field(default_factory=dict)
    # resiliência (Sprint 4)
    max_retries: int = 0               # re-tentativas no MESMO executor (estado retrying)
    retry_backoff_s: float = 0.0       # espera entre tentativas (linear: n * backoff)
    fallback_adapters: list[str] = field(default_factory=list)  # executores alternativos, em ordem
    #: True quando NINGUEM está entre os turnos — laço que roda até acabar
    #: sozinho (`bauer run`, `/loop`, scheduler, dispatcher). É o eixo que
    #: separa "o usuário vê cada passo" de "o agente decide sozinho por uma
    #: hora", e por isso o único que faz sentido para exigir contrato de tarefa.
    #:
    #: DECLARADO na origem, não inferido do endpoint. Derivar de
    #: `input["endpoint"]` erraria justamente o caso que mais importa: o `/loop`
    #: disparado de dentro do `bauer agent` interativo é autônomo e nasce pelo
    #: caminho interativo. Quem inicia um laço sem supervisão sabe disso; a
    #: string do endpoint, não.
    autonomous: bool = False


@dataclass
class KernelRun:
    """Resultado de ``BauerKernel.execute`` — espelho do Run persistido, mais a
    trajetória de estados percorrida e a decisão de policy (auditabilidade)."""

    run_id: str
    session_id: str
    status: str                        # terminal ou waiting_approval
    output: Any = None
    error: str | None = None
    policy_action: str | None = None   # allow | ask | deny (None = sem policy)
    policy_reason: str | None = None
    approval_id: str | None = None     # ApprovalRecord criado quando policy = ask
    trajectory: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "completed"
