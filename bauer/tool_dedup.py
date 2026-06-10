"""Dedup de tool calls — replay de chamadas idênticas em vez de re-executar.

Bug real (2026-06-10): em bridge mode o modelo repetiu read_file e execute_code
com argumentos idênticos várias vezes na mesma tarefa, queimando contexto e
tempo até estourar o budget. Os guardrails detectam loops de FALHA; isto cobre
o caso de repetição de chamadas BEM-SUCEDIDAS.

Regra:
- Se (tool, args) é byte-idêntico a uma chamada anterior bem-sucedida da mesma
  sessão E nenhuma tool mutante executou entre as duas → devolve o resultado
  cacheado com um aviso pedagógico, sem re-executar.
- Tools mutantes (write_file, patch, shell...) nunca são dedupadas e LIMPAM o
  cache — após uma mutação, qualquer leitura anterior pode estar obsoleta.
- execute_code é tratado como replayável: scripts idênticos repetidos no mesmo
  estado de workspace retornam o mesmo resultado na prática, e este é o caso
  de loop mais observado. A mutação de arquivos via write_file entre execuções
  invalida o cache normalmente.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict

# Tools que alteram estado do workspace/sistema — nunca dedupar, e invalidar
# o cache quando executam.
MUTATING_TOOLS = frozenset({
    "write_file", "patch", "append_file", "delete_file", "move_file",
    "copy_file", "create_dir", "shell", "cronjob", "process",
    "browser_click", "browser_fill", "browser_navigate",
    "kanban_create", "kanban_update", "kanban_claim", "kanban_comment",
    "memory", "todo", "mcp_call", "http_request", "delegate_task",
})

_MAX_ENTRIES = 32
_REPLAY_PREFIX = (
    "[dedup] Chamada idêntica à anterior nesta sessão — resultado reutilizado "
    "sem re-executar. Se precisar re-executar de verdade, mude algum argumento.\n"
)


class ToolCallDeduper:
    """Cache por sessão de resultados de tool calls bem-sucedidos.

    Uso (nos pontos de execução do agent)::

        cached = deduper.check(action_name, args)
        if cached is not None:
            tool_result = cached
        else:
            tool_result = router.execute(action_dict)
            deduper.record(action_name, args, tool_result, failed=is_error)
    """

    def __init__(self, max_entries: int = _MAX_ENTRIES):
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()  # execução paralela compartilha a instância
        self.replays = 0  # contagem para diagnostics/incidents

    @staticmethod
    def _key(action: str, args: dict) -> str:
        try:
            canon = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            canon = str(args)
        return hashlib.sha256(f"{action}|{canon}".encode()).hexdigest()

    def check(self, action: str, args: dict) -> str | None:
        """Retorna replay (com aviso) se a chamada é duplicata; None caso contrário."""
        if action in MUTATING_TOOLS:
            return None
        with self._lock:
            cached = self._cache.get(self._key(action, args))
            if cached is None:
                return None
            self.replays += 1
        return _REPLAY_PREFIX + cached

    def record(self, action: str, args: dict, result: str, failed: bool = False) -> None:
        """Registra o resultado de uma execução real.

        Mutação → invalida o cache inteiro (leituras anteriores podem estar
        obsoletas). Falha → não cacheia (retry legítimo deve re-executar).
        execute_code é híbrido: pode ter escrito arquivos via Python, então
        invalida leituras anteriores, mas permanece replayável para si mesmo
        (loop de scripts idênticos é o caso mais observado em uso real).
        """
        with self._lock:
            if action in MUTATING_TOOLS:
                self._cache.clear()
                return
            if failed:
                return
            if action == "execute_code":
                self._cache.clear()  # leituras anteriores podem estar obsoletas
            key = self._key(action, args)
            self._cache[key] = result
            self._cache.move_to_end(key)
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
