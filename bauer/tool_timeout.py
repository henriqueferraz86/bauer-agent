"""Interrupção por timeout de tool calls no agent loop.

Threads Python não podem ser mortas — o timeout retorna uma mensagem de erro
ao agente (que pode tentar outra tool ou responder ao usuário), enquanto a
thread da tool continua até completar naturalmente em background.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _FutureTimeout
from typing import Any, Callable


def call_with_timeout(
    fn: Callable[[], Any],
    timeout_s: float,
    name: str = "tool",
) -> tuple[Any, bool]:
    """Executa fn() com limite de tempo.

    Returns:
        (result, timed_out) — se `timed_out` é True, `result` é uma string
        de erro formatada; caso contrário é o retorno real de fn().

    Se `timeout_s` <= 0 executa sem limite de tempo.
    Exceções de fn() (exceto TimeoutError) propagam normalmente.

    O ``fn`` roda numa thread do executor. ContextVars NÃO cruzam para threads
    filhas automaticamente, então copiamos o contexto do chamador e rodamos
    ``fn`` dentro dele — sem isso, estado por-turno guardado em ContextVar
    (ex.: session_id/run_id do ToolRouter via `tool_router.set_runtime_ids`)
    fica invisível na execução da tool, e os eventos de tool perdem a
    atribuição de run/sessão.
    """
    if timeout_s <= 0:
        return fn(), False

    parent_ctx = contextvars.copy_context()
    ex = ThreadPoolExecutor(max_workers=1)
    future = ex.submit(parent_ctx.run, fn)
    try:
        result = future.result(timeout=timeout_s)
        ex.shutdown(wait=False)
        return result, False
    except _FutureTimeout:
        # Thread continua em background — não há como matar threads em Python.
        ex.shutdown(wait=False)
        msg = f"[Timeout: {name} excedeu {timeout_s:.0f}s — interrompido]"
        return msg, True
    except Exception:
        ex.shutdown(wait=False)
        raise
