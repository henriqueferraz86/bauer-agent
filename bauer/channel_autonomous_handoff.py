"""O agente do Telegram decide sozinho quando a demanda vira trabalho autônomo.

Antes (sprint 77), trabalho longo pelo Telegram só existia via `/loop
<objetivo>`, digitado pelo operador. Uma mensagem comum rodava como turno de
conversa, com prazo por turno (`BAUER_GATEWAY_TURN_TIMEOUT`), e uma demanda
grande era cortada no meio. Na Beelink, em 2026-11-11, isso aconteceu aos
125 s, com 41 tools já executadas.

Agora o modelo tem a tool `iniciar_trabalho_autonomo(objetivo)`. Ela só
**registra** o pedido no controle de execução do turno: o `/loop` usa a mesma
sessão e precisa da trava dela, que este turno ainda segura. Quando o turno
termina, o bridge do Telegram inicia o loop pelo mesmo caminho governado do
comando (`telegram_loop.start_loop`: admissão no Kernel, política, aprovação,
registro idempotente por `update_id`).

A tool só existe dentro de um turno de conversa **privada** do Telegram, que
é a mesma exigência do `/loop`. Na CLI, na web e em grupos ela não aparece no
schema e é recusada se chamada.
"""

from __future__ import annotations

from typing import Any

TOOL_NAME = "iniciar_trabalho_autonomo"

TOOL_DESCRIPTION = (
    "Inicia trabalho AUTONOMO em segundo plano (o mesmo do comando /loop) para "
    "uma demanda que nao cabe numa resposta de conversa: implementar ou "
    "corrigir codigo em varios arquivos, rodar e corrigir testes, executar "
    "um plano com varias etapas, ou qualquer tarefa que leve mais que alguns "
    "minutos. O trabalho comeca assim que esta resposta for enviada, aparece "
    "no painel como execucao em andamento e o resultado e entregue nesta "
    "conversa. Para perguntas, consultas, status e mudancas pequenas, "
    "responda direto sem esta tool. Depois de chamar, NAO faca o trabalho "
    "neste turno: diga ao usuario, em uma ou duas frases, o que sera feito."
)

TOOL_ARGS = {
    "objetivo": (
        "str — objetivo completo e autocontido do trabalho, com criterio de "
        "conclusao (obrigatorio)"
    ),
}

_MAX_GOAL_CHARS = 4000


def handoff_available() -> bool:
    """Há um turno de conversa privada do Telegram em andamento nesta thread?"""
    from .execution_control import current_control

    control = current_control()
    return (control is not None and getattr(control, "channel", None) == "telegram"
            and bool(getattr(control, "private_chat", False)))


def request_handoff(args: dict[str, Any]) -> str:
    """Corpo da tool: valida e registra o objetivo no controle do turno."""
    from .execution_control import current_control
    from .tools.base import ToolError

    control = current_control()
    if not handoff_available() or control is None:
        raise ToolError(
            f"{TOOL_NAME} so existe em conversa privada do Telegram. Nada foi iniciado."
        )
    goal = str(args.get("objetivo") or args.get("goal") or "").replace("\x00", "").strip()
    if not goal:
        raise ToolError(f"{TOOL_NAME} requer 'objetivo'.")
    goal = goal[:_MAX_GOAL_CHARS]
    previous = control.autonomous_goal
    control.autonomous_goal = goal
    replaced = " (substitui o pedido anterior deste turno)" if previous else ""
    return (
        f"Trabalho autonomo agendado{replaced}: comeca assim que esta resposta for "
        "enviada, sob a mesma governanca do /loop. Nao execute o trabalho agora; "
        "responda ao usuario dizendo o que sera feito."
    )


def take_goal_from(control: Any) -> str | None:
    """Objetivo registrado no turno, se ele terminou sem cancelamento."""
    if control is None:
        return None
    goal = getattr(control, "autonomous_goal", None)
    if not goal or control.cancel_event.is_set():
        return None
    return str(goal)
