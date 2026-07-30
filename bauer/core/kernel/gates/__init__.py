"""Gates do Evaluator — as checagens que decidem se um run pode concluir.

Vivem aqui, e NÃO num `core/validation/` próprio, de propósito: o mecanismo de
gate já existe (``core/kernel/evaluator.Evaluator``, com lista plugável e laço
``evaluating → planning`` limitado por ``max_replans``, ligado no Kernel desde o
Sprint 5). Um segundo pipeline de validação em paralelo seria exatamente a
fragmentação que o Kernel existe para eliminar.

O que faltava não era mecanismo — era **conteúdo**. Até o S11 só havia dois
gates, ambos farejando texto do output. Este pacote é onde entram os que
executam: testes, escopo, segredos, diff.
"""

from __future__ import annotations

__all__ = ["ScopeGate", "TestsGate"]


def __getattr__(name: str):
    if name == "TestsGate":
        from .tests import TestsGate

        return TestsGate
    if name == "ScopeGate":
        from .scope import ScopeGate

        return ScopeGate
    raise AttributeError(name)
