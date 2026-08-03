"""theme.py — fonte única da linguagem visual do Bauer (F0 do plano 028).

Antes deste módulo havia TRÊS paletas no mesmo produto: o CLI minimal
(teal #00d4aa em ui.py), a intro em gradiente teal→azul→roxo (ascii_intro.py)
e o SPA em paleta GitHub (#0d1117/#58a6ff em desktop/src/styles.css). Cada
superfície parecia de um projeto diferente.

Aqui os tokens nascem uma vez e vão para os dois lados: o terminal importa
daqui, e o CSS do SPA é GERADO daqui por `export_css_vars()` (com teste de
divergência). Sem a geração, a divergência volta — foi exatamente assim que
ela apareceu.

Acento: **violeta elétrico** #a855f7. Contraste medido sobre o fundo #0a0c10:

    ACCENT      #a855f7   4.95:1  → AA sem folga  · glifos, barras, molduras
    ACCENT_TEXT #c084fc   7.41:1  → AAA           · palavra legível em acento

Existem dois tons por causa da margem, não da aprovação: o acento puro passa
em AA (piso 4.5) por 0.45 de sobra — qualquer ajuste de fundo o derruba. Onde
o acento carrega TEXTO que se lê, use ACCENT_TEXT, que tem folga de AAA.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# ── Superfícies ──────────────────────────────────────────────────────────────
VOID = "#0a0c10"      # fundo do terminal/app
SURFACE = "#12151b"   # painel elevado
LINE = "#1e232c"      # moldura/divisor

# ── Acento (violeta elétrico) ────────────────────────────────────────────────
ACCENT = "#a855f7"       # o único neon — marca o que está VIVO
ACCENT_TEXT = "#c084fc"  # variante legível p/ texto pequeno em acento
ACCENT_DEEP = "#7c3aed"  # preenchimento/sombra do acento
CLOUD = "#7aa2f7"        # turno saiu da máquina (troca o acento no selo)

# ── Texto ────────────────────────────────────────────────────────────────────
WHITE = "#e5e7eb"     # primário
DIM = "#6b7280"       # secundário
FAINT = "#4b5563"     # colchetes/moldura/vazio de barra

# ── Sinal (reservado a RESULTADO — nunca decoração) ──────────────────────────
OK = "#22c55e"
WARN = "#f59e0b"
BAD = "#ef4444"

#: Gradiente EXCLUSIVO da marca (logo e boot). Nunca em conteúdo.
BRAND_GRADIENT = [ACCENT_DEEP, ACCENT, "#e879f9"]


# ── Glifos (com queda para ASCII) ────────────────────────────────────────────
@dataclass(frozen=True)
class Glyphs:
    """Conjunto de glifos da UI. Duas encarnações: `UNICODE` (terminal
    moderno) e `ASCII` (cmd legado, CI, redirecionamento para arquivo)."""

    bot: str            # barra de acento da resposta
    prompt: str
    ok: str
    fail: str
    running: str        # tool em andamento
    skill: str
    rail: str           # trilho de execução
    caret: str          # cursor de streaming
    gauge_full: str
    gauge_empty: str
    seal_local: str
    seal_cloud: str
    step_on: str        # esteira do Kernel — estado cumprido
    step_off: str       # esteira do Kernel — estado pendente


UNICODE = Glyphs(
    bot="▏", prompt="❯", ok="✓", fail="✗", running="◐", skill="↳",
    rail="┃", caret="▍", gauge_full="▰", gauge_empty="▱",
    seal_local="◆", seal_cloud="☁", step_on="⟐", step_off="◦",
)

ASCII = Glyphs(
    bot="|", prompt=">", ok="OK", fail="XX", running="..", skill="->",
    rail="|", caret="_", gauge_full="#", gauge_empty="-",
    seal_local="*", seal_cloud="~", step_on="=", step_off=".",
)


# ── Modo de renderização ─────────────────────────────────────────────────────
def color_enabled(env: "dict[str, str] | None" = None) -> bool:
    """Cor ligada? Respeita NO_COLOR (padrão de facto) e BAUER_UI=plain."""
    e = os.environ if env is None else env
    if e.get("NO_COLOR"):
        return False
    if e.get("BAUER_UI", "").strip().lower() == "plain":
        return False
    return True


def unicode_enabled(env: "dict[str, str] | None" = None, *, stream=None) -> bool:
    """Glifos Unicode são seguros aqui?

    Nega quando BAUER_UI=plain, quando o encoding do stream não codifica os
    blocos (cmd legado em cp437/cp1252) — testado de fato, não adivinhado por
    nome de plataforma.
    """
    e = os.environ if env is None else env
    if e.get("BAUER_UI", "").strip().lower() == "plain":
        return False
    out = sys.stdout if stream is None else stream
    enc = getattr(out, "encoding", None)
    if not enc:
        return True  # sem encoding declarado (StringIO em teste) — assume ok
    try:
        "▰◆⟐❯".encode(enc)
    except (UnicodeEncodeError, LookupError):
        return False
    return True


def glyphs(env: "dict[str, str] | None" = None, *, stream=None) -> Glyphs:
    """Conjunto de glifos adequado ao terminal atual."""
    return UNICODE if unicode_enabled(env, stream=stream) else ASCII


# ── Contraste (a régua da paleta, não um palpite) ────────────────────────────
def _luminance(hex_color: str) -> float:
    def _chan(v: int) -> float:
        c = v / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 0.2126 * _chan(r) + 0.7152 * _chan(g) + 0.0722 * _chan(b)


def contrast_ratio(fg: str, bg: str = VOID) -> float:
    """Razão de contraste WCAG entre duas cores hex."""
    a, b = _luminance(fg), _luminance(bg)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


# ── Ponte para o SPA ─────────────────────────────────────────────────────────
#: Nome do token → valor. Ordem preservada é a ordem do CSS gerado.
TOKENS: "dict[str, str]" = {
    "bauer-void": VOID,
    "bauer-surface": SURFACE,
    "bauer-line": LINE,
    "bauer-accent": ACCENT,
    "bauer-accent-text": ACCENT_TEXT,
    "bauer-accent-deep": ACCENT_DEEP,
    "bauer-cloud": CLOUD,
    "bauer-text": WHITE,
    "bauer-dim": DIM,
    "bauer-faint": FAINT,
    "bauer-ok": OK,
    "bauer-warn": WARN,
    "bauer-bad": BAD,
}

#: Aliases que o SPA já usa hoje (styles.css) apontando para os tokens novos.
#: Mantê-los evita reescrever 488 linhas de CSS de uma vez — a migração das
#: telas acontece por dentro, sem big bang.
_ALIASES: "dict[str, str]" = {
    "bg": "bauer-void",
    "bg-2": "bauer-surface",
    "bg-3": "bauer-line",
    "border": "bauer-line",
    "border-2": "bauer-faint",
    "text": "bauer-text",
    "text-2": "bauer-dim",
    "text-3": "bauer-faint",
    "accent": "bauer-accent",
    "green": "bauer-ok",
    "amber": "bauer-warn",
    "red": "bauer-bad",
    "purple": "bauer-accent-text",
}

#: Caminho do CSS gerado, relativo à raiz do repo.
CSS_PATH = "desktop/src/tokens.css"

# ASCII puro de proposito: este texto e escrito em arquivo e lido por
# ferramentas de build; acento aqui so cria chance de mojibake.
_CSS_HEADER = """\
/* GERADO por bauer/theme.py -- NAO EDITE A MAO.
 *
 * Regenere com:  python -m bauer.theme
 * O teste tests/test_theme.py falha se este arquivo divergir do Python.
 */
"""


def export_css_vars() -> str:
    """Gera o conteúdo de `desktop/src/tokens.css` a partir de TOKENS."""
    lines = [_CSS_HEADER, ":root {"]
    for name, value in TOKENS.items():
        lines.append(f"  --{name}: {value};")
    lines.append("")
    lines.append("  /* aliases da paleta antiga -> tokens do Bauer */")
    for alias, target in _ALIASES.items():
        lines.append(f"  --{alias}: var(--{target});")
    lines.append("}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":  # pragma: no cover - utilitário de geração
    # Escreve o ARQUIVO em utf-8 explícito em vez de imprimir no stdout: o
    # console do Windows abre em cp1252 e engasgaria em qualquer não-ASCII
    # (aconteceu na primeira execução deste gerador). Redirecionar `>` também
    # herda o encoding do console — escrever direto é o caminho previsível.
    from pathlib import Path

    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / CSS_PATH
    dest.write_text(export_css_vars(), encoding="utf-8")
    print(f"tokens escritos em {dest}")
