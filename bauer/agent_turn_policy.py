"""Políticas pequenas aplicadas a um turno do agente."""

from __future__ import annotations

REFLECT_EVERY = 6
REFLECT_NUDGE = (
    "[SISTEMA — ponto de reflexão] Você já executou {n} tool calls neste turno "
    "sem dar uma resposta final. Pare e avalie: (1) resuma em 1 frase o que já "
    "descobriu; (2) decida se falta UM passo concreto — se sim, execute apenas "
    "ele; (3) caso contrário, responda ao usuário agora com o que tem."
)


def maybe_reflect(context, calls: int) -> None:
    if calls > 0 and calls % REFLECT_EVERY == 0:
        context.add_user(REFLECT_NUDGE.format(n=calls))
