"""ascii_intro.py — Tela de entrada do Bauer Agent.

Exibe "BAUER AGENT" em fonte LED dot-matrix 5x7 + tagline,
fiel a identidade visual da imagem de referencia.
"""
from __future__ import annotations

import sys
import time

from rich.align import Align
from rich.console import Console
from rich.text import Text


# ── Paleta ────────────────────────────────────────────────────────────────────
S_LED = "bold yellow"   # pixel aceso — titulo LED
S_TAG = "yellow"        # tagline


# ── Glifos LED dot-matrix  5 colunas x 7 linhas
# 'O' = pixel ligado  |  ' ' = pixel apagado
# ─────────────────────────────────────────────────────────────────────────────
_G: dict[str, list[str]] = {
    "B": [
        "OOOO ",
        "O  O ",
        "O  O ",
        "OOOO ",
        "O  O ",
        "O  O ",
        "OOOO ",
    ],
    "A": [
        " OOO ",
        "O   O",
        "O   O",
        "OOOOO",
        "O   O",
        "O   O",
        "O   O",
    ],
    "U": [
        "O   O",
        "O   O",
        "O   O",
        "O   O",
        "O   O",
        "O   O",
        " OOO ",
    ],
    "E": [
        "OOOOO",
        "O    ",
        "O    ",
        "OOO  ",
        "O    ",
        "O    ",
        "OOOOO",
    ],
    "R": [
        "OOOO ",
        "O  O ",
        "O  O ",
        "OOOO ",
        "OO   ",
        "O O  ",
        "O  O ",
    ],
    " ": [
        "    ",
        "    ",
        "    ",
        "    ",
        "    ",
        "    ",
        "    ",
    ],
    "G": [
        " OOO ",
        "O   O",
        "O    ",
        "O  OO",
        "O   O",
        "O   O",
        " OOO ",
    ],
    "N": [
        "O   O",
        "OO  O",
        "O O O",
        "O  OO",
        "O   O",
        "O   O",
        "O   O",
    ],
    "T": [
        "OOOOO",
        "  O  ",
        "  O  ",
        "  O  ",
        "  O  ",
        "  O  ",
        "  O  ",
    ],
}

_TITLE   = "BAUER AGENT"
_TAGLINE = "walks with you.  works for you.  eternal purpose."
_GAP     = 1   # colunas de gap entre letras adjacentes


# ── Renderizacao do titulo LED ────────────────────────────────────────────────

def _led_rows(text: str = _TITLE) -> list[Text]:
    """Retorna 7 objetos Text — uma linha LED por objeto."""
    raw: list[str] = [""] * 7
    for i, ch in enumerate(text.upper()):
        glyph = _G.get(ch, _G[" "])
        if i > 0:
            for r in range(7):
                raw[r] += " " * _GAP
        for r in range(7):
            raw[r] += glyph[r]

    rows: list[Text] = []
    for line in raw:
        t = Text()
        for ch in line:
            if ch == "O":
                t.append(ch, style=S_LED)
            else:
                t.append(" ")
        rows.append(t)
    return rows


# ── Console helper ────────────────────────────────────────────────────────────

def _make_console(base: Console | None = None) -> Console:
    if base is not None:
        return base
    kwargs: dict = {"highlight": False}
    if sys.platform == "win32":
        kwargs["legacy_windows"] = False
    return Console(**kwargs)


# ── API publica ───────────────────────────────────────────────────────────────

def play_intro(
    console: Console | None = None,
    *,
    skip: bool = False,
    banner_only: bool = False,  # mantido para compatibilidade; ignorado
) -> None:
    """Exibe a tela de entrada: titulo LED + tagline."""
    if skip:
        return

    con = _make_console(console)

    con.print()
    con.print()

    # Titulo em fonte LED dot-matrix
    for row in _led_rows(_TITLE):
        con.print(Align.center(row))

    con.print()

    # Tagline
    con.print(Align.center(Text(_TAGLINE, style=S_TAG)))

    con.print()
    con.print()

    time.sleep(0.8)
