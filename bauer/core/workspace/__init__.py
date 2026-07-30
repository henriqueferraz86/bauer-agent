"""Ambiente de trabalho do run — isolamento por tarefa.

Nível 1 (worktree) reusa ``task_worktree.py``. A decisão vem do
``TaskContract.isolation``, não de uma regra geral: isolar todo run quebraria o
fluxo de fix direto no master. Ver ``isolation.py``.
"""

from __future__ import annotations

__all__ = ["Isolamento", "finalizar", "preparar"]


def __getattr__(name: str):
    if name in set(__all__):
        from . import isolation as _i

        return getattr(_i, name)
    raise AttributeError(name)
