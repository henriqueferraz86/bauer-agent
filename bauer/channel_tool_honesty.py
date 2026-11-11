"""Aviso quando o modelo escreve chamadas de tool como texto em vez de chamá-las.

Achado na Beelink em 2026-11-11: no Telegram, o `deepseek/deepseek-chat` (via
OpenRouter) respondeu com blocos como ``delegate_task(agent_name=..., task=...)``
e ``patch(path=..., old_string=...)`` escritos na mensagem, sem nenhuma tool
executada no turno. Nos turnos seguintes, o modelo descreveu progresso dos
"agentes" e arquivos que nunca existiram. O operador via no Telegram que havia
trabalho em execução, mas o painel não mostrava nenhum agente, porque nada
tinha sido disparado.

Este módulo não tenta adivinhar intenção nem reexecutar nada. Só detecta
o caso inequívoco: nenhuma tool executada **e** a resposta contém uma
chamada, com argumento nomeado, de uma tool que o turno tinha disponível.
Nesse caso, o canal acrescenta um aviso explícito à resposta.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# `nome(` seguido (após espaços/quebras) de `argumento=`: forma de chamada,
# não menção em prosa como "use read_file para ler".
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


def textual_tool_calls(response: str, tool_names: Iterable[str]) -> list[str]:
    """Tools disponíveis que aparecem na resposta escritas como chamada."""
    known = set(tool_names)
    found: list[str] = []
    for match in _CALL.finditer(response or ""):
        name = match.group(1)
        if name in known and name not in found:
            found.append(name)
    return found


def unexecuted_tool_calls_note(
    response: str, tool_log: Iterable[object], tool_names: Iterable[str],
) -> str | None:
    """Aviso para anexar à resposta, ou ``None`` se não há o que avisar."""
    if any(True for _ in tool_log):
        return None
    names = textual_tool_calls(response, tool_names)
    if not names:
        return None
    listed = ", ".join(f"`{name}`" for name in names)
    return (
        "⚠️ Aviso do Bauer: nenhuma ferramenta foi executada neste turno. "
        f"As chamadas acima ({listed}) foram só escritas como texto pelo modelo: "
        "nada foi delegado, criado ou alterado, e nenhum agente está trabalhando "
        "nisso. Peça de novo para executar ou troque o modelo com /model."
    )
