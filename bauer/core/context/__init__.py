"""Engenharia de contexto — uma porta para o que entra na janela.

NÃO reescreve `context_manager.py`: delega a ele e acrescenta proveniência.
Ver `builder.py` para o porquê.
"""

from __future__ import annotations

__all__ = ["ContextBuilder", "ItemContexto", "PacoteContexto", "PRIORIDADE"]


def __getattr__(name: str):
    if name in set(__all__):
        from . import builder as _b

        return getattr(_b, name)
    raise AttributeError(name)
