"""Normalização do texto que será falado pelo Bauer."""

from __future__ import annotations

import re


# Faixas Unicode usadas por emojis e seus modificadores/combinações. O texto
# exibido no CLI não passa por esta função: ela é exclusiva do caminho de voz.
_EMOJI_RE = re.compile(
    "["
    "\\U0001F000-\\U0001FAFF"
    "\\u2300-\\u23FF"
    "\\u2600-\\u27BF"
    "\\u2B00-\\u2BFF"
    "\\uFE0E-\\uFE0F"
    "\\u200D"
    "\\u20E3"
    "\\U0001F3FB-\\U0001F3FF"
    "]+"
)
_SPEECH_MARKUP_RE = re.compile(r"[*_~`#|<>\[\]{}:•◦▪▫◘○●◻◼▸►→←┃│=]+")
_LIST_PREFIX_RE = re.compile(r"(?m)^\s*(?:[-+]|\d+[.)])\s+")
_WHITESPACE_RE = re.compile(r"\s+")


def strip_emoji_for_speech(text: str) -> str:
    """Limpa emojis e marcação visual antes de enviar texto ao sintetizador.

    A resposta exibida pode conter Markdown, listas e símbolos de interface;
    esses caracteres não devem ser pronunciados literalmente pelo TTS.
    """
    clean = _EMOJI_RE.sub(" ", str(text or ""))
    clean = _LIST_PREFIX_RE.sub(" ", clean)
    clean = _SPEECH_MARKUP_RE.sub(" ", clean)
    return _WHITESPACE_RE.sub(" ", clean).strip()
