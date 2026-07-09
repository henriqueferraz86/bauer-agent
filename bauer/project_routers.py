"""Router-por-projeto — Fase 1 do isolamento por projeto no ``bauer serve``.

A Fase 0 (``_active_project_hint`` em server.py) é um nudge de prompt: pede ao
modelo para trabalhar dentro da subpasta do projeto ativo, mas a sandbox
continua sendo o workspace inteiro do serve. Aqui a fronteira é real: cada
projeto ganha seu PRÓPRIO ``ToolRouter``, com sandbox/policy/kanban/audit
escopados na pasta do projeto — ``write_file``/``run_command``/``kanban_*``
não conseguem tocar nada fora dela.

v1 (deliberado, ver conversa de design): todos os projetos compartilham o
MESMO ``llm_client``/config do serve — só o workspace muda por projeto.
Modelo/policy por-projeto é uma fase futura.

Limitação conhecida: um projeto registrado como pai/filho de outro projeto
registrado não ganha proteção cruzada extra (a sandbox de cada um ainda é só
a pasta dele) — não é uma escalada de privilégio (o usuário registrou os
dois), mas os dois routers podem, em teoria, ver os arquivos um do outro
através da relação de pastas. Não endereçado nesta fase.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("bauer.project_routers")

#: Teto de routers de projeto mantidos vivos simultaneamente. Cada um segura
#: handles (event bus, audit logger) — sem teto, uma sessão que visita muitos
#: projetos ao longo do dia vazaria memória/file handles indefinidamente.
DEFAULT_MAX_CACHED = 8


class ProjectRouterCache:
    """Cache LRU de ``ToolRouter`` por ``project_id``, construído sob demanda.

    ``default_router`` é devolvido quando ``project_id`` é None, desconhecido,
    aponta para uma pasta que não existe mais, ou é um diretório sensível —
    falha SEGURA: na dúvida, usa o router default (o que já se comportava
    hoje) em vez de arriscar uma sandbox mal-formada.

    ``builder(project_path) -> ToolRouter`` é injetado pelo caller (o
    ``server.py`` fecha sobre ``llm_client``/config do serve) — mantém este
    módulo livre de import pesado e fácil de testar com um builder falso.
    """

    def __init__(
        self,
        default_router: Any,
        builder: Callable[[Path], Any],
        *,
        max_cached: int = DEFAULT_MAX_CACHED,
    ) -> None:
        self._default = default_router
        self._builder = builder
        self._max = max_cached
        self._cache: "OrderedDict[str, Any]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, project_id: Optional[str]) -> Any:
        """Router para ``project_id``, ou o router default em qualquer caso
        não resolvível (None, desconhecido, pasta sumida, pasta sensível)."""
        if not project_id:
            return self._default
        with self._lock:
            cached = self._cache.get(project_id)
            if cached is not None:
                self._cache.move_to_end(project_id)
                return cached
        router = self._build_safe(project_id)
        if router is None:
            return self._default
        with self._lock:
            self._cache[project_id] = router
            self._cache.move_to_end(project_id)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
        return router

    def _build_safe(self, project_id: str) -> Optional[Any]:
        try:
            from . import projects_registry as pr

            entry = pr.get_project(project_id)
            if not entry:
                return None
            path = Path(entry["path"])
            if not path.is_dir():
                logger.warning(
                    "Projeto '%s' aponta para pasta inexistente: %s", project_id, path
                )
                return None
            if pr.is_sensitive_dir(path):
                logger.warning(
                    "Projeto '%s' resolve para pasta sensível — recusando router: %s",
                    project_id, path,
                )
                return None
            return self._builder(path)
        except Exception:  # noqa: BLE001 — nunca derruba o turno; cai no default
            logger.exception("Falha ao construir router para projeto '%s'", project_id)
            return None

    def invalidate(self, project_id: str) -> None:
        """Remove um projeto do cache (ex.: pasta foi apagada/movida)."""
        with self._lock:
            self._cache.pop(project_id, None)

    def __len__(self) -> int:
        return len(self._cache)
