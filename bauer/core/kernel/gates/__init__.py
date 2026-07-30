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

__all__ = ["AcceptanceGate", "DiffGate", "ScopeGate", "SecretsGate", "TestsGate"]

#: import preguiçoso: um gate que não entra na composição não paga import.
#: `secrets` puxa o scanner inteiro; `tests` puxa o app_verify.
_MODULOS = {
    "AcceptanceGate": "acceptance",
    "DiffGate": "diff",
    "ScopeGate": "scope",
    "SecretsGate": "secrets",
    "TestsGate": "tests",
}


def __getattr__(name: str):
    modulo = _MODULOS.get(name)
    if modulo is None:
        raise AttributeError(name)
    import importlib

    return getattr(importlib.import_module(f".{modulo}", __name__), name)
