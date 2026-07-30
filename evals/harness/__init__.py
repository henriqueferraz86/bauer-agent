"""Harness Evaluation Suite — mede comportamento do harness, não do modelo.

Ver ``runner.py`` para o porquê de isto não ser "mais um teste unitário".
"""

from __future__ import annotations

__all__ = ["Relatorio", "Veredito", "cenario", "formatar", "rodar"]


def __getattr__(name: str):
    if name in set(__all__):
        from . import runner as _r

        return getattr(_r, name)
    raise AttributeError(name)
