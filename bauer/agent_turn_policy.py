"""Políticas pequenas aplicadas a um turno do agente."""

from __future__ import annotations

REFLECT_EVERY = 6
REFLECT_NUDGE = (
    "[SISTEMA — ponto de reflexão] Você já executou {n} tool calls neste turno "
    "sem dar uma resposta final. Pare e avalie: (1) resuma em 1 frase o que já "
    "descobriu; (2) decida se falta UM passo concreto — se sim, execute apenas "
    "ele; (3) caso contrário, responda ao usuário agora com o que tem."
)


def maybe_reflect(context, calls: int, budget=None) -> None:
    """Injeta o ponto de reflexão, exceto em turno autônomo.

    Um orçamento compartilhado (``remaining_tool_calls``) marca o /loop: ali o
    usuário pediu trabalho longo e sem supervisão, e "responda agora com o que
    tem" a cada 6 tools fazia o loop desistir no meio da tarefa (sprint 81).
    O detector de chamadas repetidas continua valendo nos dois modos.
    """
    if hasattr(budget, "remaining_tool_calls"):
        return
    if calls > 0 and calls % REFLECT_EVERY == 0:
        context.add_user(REFLECT_NUDGE.format(n=calls))
