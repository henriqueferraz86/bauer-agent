"""Switch único do backend de task-store (achado #10 da auditoria 023).

O Bauer tem duas gerações de armazenamento de tarefas:

  * ``markdown`` — ``WorkspaceManager`` → ``TASKS.md`` (legado, default).
  * ``sqlite``   — ``WorkspaceManagerSqlite`` → kernel ``kanban_db`` (o mesmo
    store que swarm/specify/decompose/boards/daemon já usam).

**Por que uma factory e não trocar call site por call site:** as duas gerações
leem de fontes DIFERENTES (arquivo vs. board SQLite). Apontar um consumidor
isolado para o sqlite faria ele ler uma base vazia enquanto os outros
continuam no markdown — isso CRIA split-brain em vez de curar. Com a factory,
o backend é resolvido num lugar só e a virada move todos de uma vez.

**Antes de virar para ``sqlite``, migre os dados:** ``bauer kanban-migrate``
(idempotente). Sem isso, o store novo está vazio.

Uso::

    from .workspace_manager_factory import get_workspace_manager

    wm = get_workspace_manager(workspace)      # respeita a config
    wm.add_task("...")                          # API idêntica nos dois

As duas classes expõem a MESMA API pública e retornam o MESMO dataclass
``Task`` — a paridade é fixada por ``tests/test_task_store_parity.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["get_workspace_manager", "resolve_task_backend", "board_for_workspace"]


def board_for_workspace(workspace: str | Path) -> str:
    """Board do kanban_db que corresponde a este workspace (achado #10-F).

    No backend markdown o isolamento entre projetos vem de graça do CAMINHO
    (``<projeto>/TASKS.md``). No sqlite ele vem do **board** — sem mapear
    workspace→board, todo projeto cairia no board ativo e as tarefas de
    projetos diferentes apareceriam misturadas.

    Reusa ``projects_registry.project_id`` (sha1 do caminho absoluto, 12 chars)
    — a identidade de projeto que o Bauer já usa em todo lugar. Não inventa
    convenção nova, e não colide entre projetos homônimos em raízes
    diferentes.

    O ``bauer kanban-migrate`` usa ESTA MESMA função como default do
    ``--board``: sem isso o usuário migraria os dados para um board e o
    sistema leria de outro (as tarefas "sumiriam").
    """
    from .projects_registry import project_id

    return project_id(workspace)


def resolve_task_backend(override: str | None = None) -> str:
    """Retorna ``"markdown"`` ou ``"sqlite"``.

    Ordem: argumento explícito → ``agent.task_backend`` da config → default
    ``"markdown"``. Falha de leitura da config nunca quebra o caller — cai no
    default conservador (mesmo padrão dos outros toggles do agente).
    """
    if override:
        value = override.strip().lower()
        return value if value in ("markdown", "sqlite") else "markdown"
    try:
        from .config_loader import load_config

        value = str(load_config().agent.task_backend).strip().lower()
        return value if value in ("markdown", "sqlite") else "markdown"
    except Exception:
        return "markdown"


def get_workspace_manager(
    workspace: str | Path = "workspace",
    *,
    backend: str | None = None,
    **kwargs: Any,
):
    """Instancia o WorkspaceManager do backend configurado.

    Args:
        workspace: diretório do workspace (mesmo significado nas duas gerações).
        backend: força ``"markdown"``/``"sqlite"``, ignorando a config. Útil em
            testes e no comando de migração.
        **kwargs: repassados só para o backend sqlite (``board``,
            ``regenerate_view``) — o markdown não os aceita e os ignora.
            Sem ``board`` explícito, usa :func:`board_for_workspace` para
            preservar o isolamento por projeto (#10-F). Quem sabe qual board
            quer (swarm, `boards switch`, `--board`) continua mandando.
    """
    if resolve_task_backend(backend) == "sqlite":
        from .workspace_manager_sqlite import WorkspaceManagerSqlite

        kwargs.setdefault("board", board_for_workspace(workspace))
        return WorkspaceManagerSqlite(workspace, **kwargs)

    from .workspace_manager import WorkspaceManager

    return WorkspaceManager(workspace)
