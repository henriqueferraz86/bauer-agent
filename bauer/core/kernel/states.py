"""Máquina de estados do Bauer Kernel.

Estende os RunStatus existentes de forma ADITIVA (run_manager.py continua
dono do modelo Run e da persistência). O caminho legado — queued → running →
completed — permanece válido e intocado; os estados intermediários abaixo só
aparecem quando a execução passa pelo BauerKernel.

    CREATED → PLANNING → POLICY_CHECK → QUEUED → RUNNING → EVALUATING → COMPLETED
    policy deny  → FAILED          policy ask → WAITING_APPROVAL (estado já existente)
    RUNNING → RETRYING → QUEUED    RUNNING → PAUSED → QUEUED
    EVALUATING → PLANNING (replan, limitado por budget)
"""

from __future__ import annotations

#: transições legais quando a execução é coordenada pelo Kernel.
KERNEL_TRANSITIONS: dict[str, set[str]] = {
    "created": {"planning", "cancelled"},
    "planning": {"policy_check", "failed", "cancelled"},
    "policy_check": {"queued", "waiting_approval", "failed", "cancelled"},
    "queued": {"running", "cancelled"},
    "running": {"evaluating", "completed", "failed", "retrying", "paused",
                "waiting_approval", "cancelled"},
    "retrying": {"queued", "failed", "cancelled"},
    "paused": {"queued", "cancelled"},
    "waiting_approval": {"queued", "failed", "cancelled"},
    "evaluating": {"completed", "planning", "failed", "cancelled"},
    # terminais — nenhuma saída
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}

#: estados novos (do Kernel) que NÃO têm evento dedicado no run_manager —
#: para estes o Kernel publica `run.state.changed` no EventBus.
KERNEL_ONLY_STATES = {"created", "planning", "policy_check", "evaluating", "retrying", "paused"}


class KernelStateError(RuntimeError):
    """Transição de estado ilegal na máquina do Kernel."""


def can_transition(current: str, new: str) -> bool:
    return new in KERNEL_TRANSITIONS.get(current, set())


def ensure_transition(current: str, new: str) -> None:
    if not can_transition(current, new):
        raise KernelStateError(f"transição ilegal: {current!r} → {new!r}")
