"""Detecção leve e opt-in de wake word para sessões de voz.

O detector trabalha sobre a transcrição curta produzida pelo capturador já
existente. Isso mantém o recurso local e sem dependência de um SDK proprietário;
um detector acústico dedicado pode ser plugado depois sem mudar o contrato
``extract_command``.
"""

from __future__ import annotations

import os
import re
import unicodedata

DEFAULT_WAKE_WORD = "bauer"


def configured_wake_word() -> str:
    """Retorna a wake word configurada, com fallback seguro."""
    value = os.environ.get("BAUER_WAKE_WORD", DEFAULT_WAKE_WORD).strip()
    return value or DEFAULT_WAKE_WORD


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def extract_command(text: str, *, wake_word: str | None = None) -> str | None:
    """Extrai o comando depois da wake word.

    Retorna ``None`` quando a frase não contém a wake word. Quando contém
    somente a wake word, retorna ``""`` para permitir aguardar a próxima fala.
    A busca ignora maiúsculas, acentos e pontuação ao redor do gatilho.
    """
    candidate = str(text or "").strip()
    trigger = (wake_word or configured_wake_word()).strip()
    if not candidate or not trigger:
        return None

    folded_text = _fold(candidate)
    folded_trigger = _fold(trigger)
    pattern = re.compile(
        rf"(?<![\w]){re.escape(folded_trigger)}(?![\w])",
        flags=re.UNICODE,
    )
    match = pattern.search(folded_text)
    if match is None:
        return None

    # O folding remove acentos, mas preserva a quantidade de caracteres para
    # os casos comuns de português; protege contra qualquer divergência antes
    # de cortar a frase original.
    start = min(match.end(), len(candidate))
    return candidate[start:].lstrip(" \t,;:!?-–—")


def is_wake_word_only(text: str, *, wake_word: str | None = None) -> bool:
    """Indica se a transcrição contém somente o gatilho."""
    command = extract_command(text, wake_word=wake_word)
    return command == ""
