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
    trajectory: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "completed"
