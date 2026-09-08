"""Contexto de execução por turno para ferramentas compartilhadas.

O servidor pode reutilizar um ``ToolRouter`` entre requisições concorrentes.
Os IDs de sessão e run, portanto, pertencem ao contexto da execução atual e
não à instância do router.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_runtime_ids: ContextVar[tuple[str | None, str | None] | None] = ContextVar(
    "bauer_tool_router_runtime_ids", default=None
)


def set_runtime_ids(session_id: str | None, run_id: str | None) -> Any:
    """Instala os IDs do turno atual e devolve o token de restauração.

    A chamada e a restauração devem ocorrer na mesma thread ou task. Caminhos
    que criarem workers precisam propagar o contexto explicitamente.
    """
    return _runtime_ids.set((session_id, run_id))


def reset_runtime_ids(token: Any) -> None:
    """Restaura o contexto anterior sem deixar uma falha de limpeza vazar."""
    try:
        _runtime_ids.reset(token)
    except Exception:  # noqa: BLE001 - limpeza nunca deve derrubar o turno
        _runtime_ids.set(None)


def get_runtime_ids() -> tuple[str | None, str | None] | None:
    """Devolve os IDs do turno atual, quando houver um contexto instalado."""
    return _runtime_ids.get()
