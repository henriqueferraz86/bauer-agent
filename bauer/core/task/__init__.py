"""Contrato de tarefa — escopo, critérios de aceite e como validar.

Acrescenta a peça que faltava ao redor do Planner (``autonomous_planner.py``) e
dos schemas inter-agente (``bauer/contracts.py``): o que *aquela* tarefa pode
tocar e o que precisa provar. Ver ``contract.py`` para o porquê.
"""

from __future__ import annotations

__all__ = ["Scope", "TaskContract", "Validation"]


def __getattr__(name: str):
    if name in set(__all__):
        from . import contract as _c

        return getattr(_c, name)
    raise AttributeError(name)
