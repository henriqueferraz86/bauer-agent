"""ascii_intro.py — Tela de entrada do Bauer Agent.

Logo "BAUER" em blocos com gradiente teal→azul→roxo + painel de sessão
moderno com bordas arredondadas. Paleta consistente com indicators.py.
"""
from __future__ import annotations

import sys

# Blocos █ exigem utf-8 — garante o encoding mesmo se nenhum outro módulo
# tiver reconfigurado o stdout (evita UnicodeEncodeError em consoles cp1252).
if sys.platform == "win32":
    try:
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box


# ── Paleta (mesma do indicators.py) ────────────────────────────────────────────
_GRADIENT = ["#00d4aa", "#3b82f6", "#a855f7"]  # teal → azul → roxo
ACCENT = "#00d4aa"
BLUE = "#3b82f6"
PURPLE = "#7c3aed"
DIM = "#6b7280"
WHITE = "#f9fafb"


# ── Glifos em bloco  (largura 7 × altura 5) ─────────────────────────────────────
_G: dict[str, list[str]] = {
    "B": ["██████ ", "██   ██", "██████ ", "██   ██", "██████ "],
    "A": [" █████ ", "██   ██", "███████", "██   ██", "██   ██"],
    "U": ["██   ██", "██   ██", "██   ██", "██   ██", " █████ "],
    "E": ["███████", "██     ", "█████  ", "██     ", "███████"],
    "R": ["██████ ", "██   ██", "██████ ", "██   ██", "██   ██"],
    " ": ["       ", "       ", "       ", "       ", "       "],
}

_TITLE = "BAUER"
_SUBTITLE = "adaptive LLM runtime"
_HEIGHT = 5
_GAP = 1


# ── Gradiente ───────────────────────────────────────────────────────────────────

def _lerp(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = round(r1 + (r2 - r1) * t)
    g = round(g1 + (g2 - g1) * t)
    b = round(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _grad_color(frac: float, stops: list[str] = _GRADIENT) -> str:
    if frac <= 0:
        return stops[0]
    if frac >= 1:
        return stops[-1]
    seg = frac * (len(stops) - 1)
    i = int(seg)
    return _lerp(stops[i], stops[i + 1], seg - i)


# ── Renderização do logo ────────────────────────────────────────────────────────

def _logo_rows(text: str = _TITLE) -> list[Text]:
    """Retorna `_HEIGHT` linhas Text, cada uma com gradiente horizontal."""
    raw: list[str] = [""] * _HEIGHT
    for i, ch in enumerate(text.upper()):
        glyph = _G.get(ch, _G[" "])
        if i > 0:
            for r in range(_HEIGHT):
                raw[r] += " " * _GAP
        for r in range(_HEIGHT):
            raw[r] += glyph[r]

    width = max((len(r) for r in raw), default=1)
    rows: list[Text] = []
    for line in raw:
        t = Text()
        for col, ch in enumerate(line):
            if ch == "█":
                t.append(ch, style=_grad_color(col / max(width - 1, 1)))
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


# ── API pública ─────────────────────────────────────────────────────────────────

def play_intro(
    console: Console | None = None,
    *,
    skip: bool = False,
    banner_only: bool = False,  # compat; ignorado
) -> None:
    """Exibe o logo BAUER em gradiente + subtítulo."""
    if skip:
        return

    con = _make_console(console)
    con.print()
    for row in _logo_rows(_TITLE):
        con.print(Align.center(row))
    con.print()
    con.print(Align.center(Text(_SUBTITLE, style=f"italic {DIM}")))
    con.print()


def session_panel(
    title: str,
    model_name: str,
    context_tokens: int | str,
    *,
    provider: str | None = None,
    commands: list[tuple[str, str]] | None = None,
    extra_rows: list[tuple[str, str]] | None = None,
) -> Panel:
    """Painel de sessão moderno (bordas arredondadas) com modelo, provider,
    contexto e atalhos de comando.

    commands: lista de (comando, descrição) — ex: [("/exit", "sair")].
    """
    info = Table.grid(padding=(0, 2))
    info.add_column(justify="left", style=BLUE, width=2)
    info.add_column(justify="left", style=DIM, width=10)
    info.add_column(justify="left", style=WHITE)

    def _row(label: str, value: str) -> None:
        info.add_row("◆", label, value)

    _row("Modelo", str(model_name))
    if provider:
        _row("Provider", str(provider))
    try:
        _ctx = f"{int(context_tokens):,}".replace(",", ".")
    except (ValueError, TypeError):
        _ctx = str(context_tokens)
    _row("Contexto", f"{_ctx} tokens")
    for label, value in (extra_rows or []):
        _row(label, value)

    body: list = [info]

    if commands:
        cmd_line = Text()
        for idx, (cmd, desc) in enumerate(commands):
            if idx:
                cmd_line.append("   ")
            cmd_line.append(cmd, style=f"bold {PURPLE}")
            cmd_line.append(f" {desc}", style=DIM)
        body.append(Text())
        body.append(cmd_line)

    return Panel(
        Group(*body),
        title=Text(title, style=f"bold {ACCENT}"),
        title_align="left",
        border_style=BLUE,
        box=box.ROUNDED,
        padding=(1, 2),
    )
