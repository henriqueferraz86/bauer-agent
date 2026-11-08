"""Validação única de IDs de sessão (WEB-01, sprint 59).

Todo transporte (HTTP ``/chat``/``/stream``/``/loop``, OpenAI-compat,
CLI e canais) e todo store de sessão passam por :func:`validate_session_id`.
Um ID é um único componente de nome de arquivo portável: nada de separador,
caminho absoluto, drive do Windows, ``..`` ou nome reservado de dispositivo.
O ``SessionStore`` JSONL ainda confere, depois de resolver o caminho, que o
arquivo fica dentro do diretório de sessões e não é um symlink.
"""

from __future__ import annotations

import re

#: Primeiro caractere alfanumérico (exclui ``.``/``..`` e nomes ocultos);
#: o resto cobre UUIDs, ``agent-<nome>``, ``tg:<chat_id>``,
#: ``tg-project-<hash>-<id>`` e ``agent:<id>:<slot>`` do gateway.
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,199}")
_WINDOWS_RESERVED = re.compile(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?")


class InvalidSessionIdError(ValueError):
    """ID de sessão fora do formato aceito (traversal, separador, absoluto…)."""


def validate_session_id(session_id: object) -> str:
    """Devolve o ID inalterado se for seguro; senão levanta InvalidSessionIdError."""
    if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
        raise InvalidSessionIdError(
            "session_id inválido: use até 200 caracteres [A-Za-z0-9_.:@+-], "
            "começando por letra ou dígito, sem separadores de caminho."
        )
    if session_id.endswith(".") or _WINDOWS_RESERVED.fullmatch(session_id.split(":", 1)[0]):
        raise InvalidSessionIdError("session_id inválido: nome reservado ou terminado em '.'.")
    return session_id


def is_valid_session_id(session_id: object) -> bool:
    try:
        validate_session_id(session_id)
    except InvalidSessionIdError:
        return False
    return True


def optional_session_id(session_id: str | None) -> str | None:
    """Valida um ID opcional vindo de um transporte; vazio/None continua None."""
    if session_id is None or session_id == "":
        return None
    return validate_session_id(session_id)
