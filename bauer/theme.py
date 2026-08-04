"""theme.py — fonte única da linguagem visual do Bauer (F0 do plano 028).

Antes deste módulo havia TRÊS paletas no mesmo produto: o CLI minimal
(teal #00d4aa em ui.py), a intro em gradiente teal→azul→roxo (ascii_intro.py)
e o SPA em paleta GitHub (#0d1117/#58a6ff em desktop/src/styles.css). Cada
superfície parecia de um projeto diferente.

Aqui os tokens nascem uma vez e vão para os dois lados: o terminal importa
daqui, e o CSS do SPA é GERADO daqui por `export_css_vars()` (com teste de
divergência). Sem a geração, a divergência volta — foi exatamente assim que
ela apareceu.

Acento padrão: **violeta elétrico** #a855f7 — mas TROCÁVEL em tempo de execução
(`set_accent`, Ctrl+T / `/theme`). As variantes de cada acento não são
escolhidas à mão: `ACCENT_TEXT` é derivado clareando até cruzar 7:1 de
contraste, e `ACCENT_DEEP` escurecendo. Assim adicionar um acento novo é uma
linha, e o invariante de legibilidade vale para todos por construção — não por
alguém ter conferido na hora de escrever.

Dois tons por causa da MARGEM, não da aprovação: o violeta puro passa em AA
(4.95 contra piso 4.5) por 0.45 de sobra, e qualquer ajuste de fundo o derruba.
Onde o acento carrega TEXTO que se lê, use ACCENT_TEXT.

**Acento nunca invade cor de sinal.** OK/WARN/BAD significam resultado; se o
acento ficar perto de um deles, o usuário deixa de distinguir "isto está vivo"
de "isto deu errado". O piso é ΔE (CIE76) ≥ 25 contra cada sinal, garantido por
teste. Consequência medida: **não cabe um âmbar/laranja saturado** — essa faixa
de matiz é do WARN e do BAD (âmbar #f0a02a fica a ΔE 7.8 do WARN). Só versões
claras da família quente entram (papaia, salmão).
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass

# ── Superfícies ──────────────────────────────────────────────────────────────
VOID = "#0a0c10"      # fundo do terminal/app
SURFACE = "#12151b"   # painel elevado
LINE = "#1e232c"      # moldura/divisor

CLOUD = "#7aa2f7"        # turno saiu da máquina (troca o acento no selo)

# ── Texto ────────────────────────────────────────────────────────────────────
WHITE = "#e5e7eb"     # primário
DIM = "#6b7280"       # secundário
FAINT = "#4b5563"     # colchetes/moldura/vazio de barra

# ── Sinal (reservado a RESULTADO — nunca decoração) ──────────────────────────
OK = "#22c55e"
WARN = "#f59e0b"
BAD = "#ef4444"


# ── Contraste (a régua da paleta, não um palpite) ────────────────────────────
# Definido ANTES das paletas: a derivação de ACCENT_TEXT roda no import e
# precisa medir contraste para saber quando parar de clarear.
def _rgb(hex_color: str) -> "tuple[int, int, int]":
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _canal_linear(v: int) -> float:
    """Canal sRGB (0-255) → linear, para luminância e Lab."""
    c = v / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    r, g, b = _rgb(hex_color)
    return (
        0.2126 * _canal_linear(r)
        + 0.7152 * _canal_linear(g)
        + 0.0722 * _canal_linear(b)
    )


def contrast_ratio(fg: str, bg: str = VOID) -> float:
    """Razão de contraste WCAG entre duas cores hex."""
    a, b = _luminance(fg), _luminance(bg)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


# ── Manipulação de cor (base da derivação) ──────────────────────────────────


def _hex(r: float, g: float, b: float) -> str:
    def _c(v: float) -> int:
        return max(0, min(255, round(v)))

    return f"#{_c(r):02x}{_c(g):02x}{_c(b):02x}"


def _misturar(c1: str, c2: str, t: float) -> str:
    """Interpola c1→c2 em `t` (0..1), em sRGB simples."""
    r1, g1, b1 = _rgb(c1)
    r2, g2, b2 = _rgb(c2)
    return _hex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t)


def _para_texto(accent: str, *, alvo: float = 7.0, fundo: str = VOID) -> str:
    """Clareia o acento até cruzar `alvo` de contraste (AAA por padrão).

    Busca em passos pequenos em vez de fórmula fechada: contraste WCAG não é
    linear na mistura, e a busca é barata (roda uma vez por acento).
    """
    if contrast_ratio(accent, fundo) >= alvo:
        return accent
    for passo in range(1, 21):
        candidato = _misturar(accent, "#ffffff", passo * 0.05)
        if contrast_ratio(candidato, fundo) >= alvo:
            return candidato
    return "#ffffff"


def _profundo(accent: str, *, fator: float = 0.35, fundo: str = VOID) -> str:
    """Escurece o acento na direção do fundo (preenchimento/sombra)."""
    return _misturar(accent, fundo, fator)


# ── Distância perceptual (a régua que protege as cores de sinal) ────────────
def _lab(hex_color: str) -> "tuple[float, float, float]":
    r, g, b = (_canal_linear(v) for v in _rgb(hex_color))
    x = r * 0.4124 + g * 0.3576 + b * 0.1805
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = r * 0.0193 + g * 0.1192 + b * 0.9505

    def _f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = _f(x / 0.95047), _f(y / 1.0), _f(z / 1.08883)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(c1: str, c2: str) -> float:
    """Distância perceptual CIE76 entre duas cores.

    Serve para uma pergunta só: este acento é confundível com uma cor de
    sinal? Abaixo de ~25 o usuário deixa de distinguir "vivo" de "deu errado".
    """
    l1, a1, b1 = _lab(c1)
    l2, a2, b2 = _lab(c2)
    return math.sqrt((l1 - l2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2)


#: Piso de distância entre acento e QUALQUER cor de sinal. Medido: âmbar
#: #f0a02a fica a 7.8 do WARN — indistinguível na prática.
DELTA_E_MINIMO = 25.0

#: Acentos disponíveis. Adicionar um é uma linha: nome → hex. As variantes
#: (texto/profundo) são derivadas, e o teste garante contraste e distância dos
#: sinais — um acento que não passa REPROVA a suíte em vez de virar bug visual.
PALETAS: "dict[str, str]" = {
    "violeta":   "#a855f7",   # padrão
    "lavanda":   "#c4b5fd",
    "indigo":    "#818cf8",
    "azul":      "#60a5fa",
    "ceu":       "#38bdf8",
    "ciano":     "#22d3ee",
    "turquesa":  "#2dd4bf",
    "teal":      "#00d4aa",   # o Bauer clássico, antes do plano 028
    "menta":     "#6ee7b7",
    "lima":      "#b8ff2f",
    "ouro":      "#fde047",
    "papaia":    "#ffab70",
    "salmao":    "#ff8a65",
    "pessego":   "#fca5a5",
    "rosa":      "#f472b6",
    "magenta":   "#e879f9",
    "fucsia":    "#d946ef",
}

#: Acento em uso. Trocado por `set_accent`; lido por todo mundo como
#: `theme.ACCENT` (atributo de módulo, resolvido na hora do render) — por isso
#: a troca vale na tela inteira sem ninguém precisar se re-registrar.
ACCENT_NAME = "violeta"
ACCENT = PALETAS[ACCENT_NAME]
ACCENT_TEXT = _para_texto(ACCENT)
ACCENT_DEEP = _profundo(ACCENT)


def _gradiente_da_marca(accent: str) -> "list[str]":
    """Gradiente do logo/boot, derivado do acento — profundo → acento → claro.

    Derivado e não fixo: um gradiente violeta com o acento em lima faria o
    logo brigar com o resto da tela.
    """
    return [_profundo(accent, fator=0.45), accent, _para_texto(accent, alvo=9.0)]


BRAND_GRADIENT = _gradiente_da_marca(ACCENT)


def accent_names() -> "list[str]":
    """Nomes dos acentos, na ordem em que o ciclo (Ctrl+T) percorre."""
    return list(PALETAS)


def set_accent(nome: str) -> str:
    """Troca o acento da sessão inteira. Devolve o nome aplicado.

    Reatribui os ATRIBUTOS DO MÓDULO: quem lê `theme.ACCENT` na hora do render
    (a maioria do kit) muda de cor sem se re-registrar em nada. Quem capturou o
    valor no import não muda — por isso os consumidores foram convertidos para
    leitura tardia junto com esta função (ver plano 028; foi assim que a
    `bottom_toolbar` e o `_PT_STYLE` ficaram para trás no F0).

    Levanta KeyError se o nome não existe — errar o nome em silêncio deixaria o
    usuário achando que a tecla não funciona.
    """
    global ACCENT_NAME, ACCENT, ACCENT_TEXT, ACCENT_DEEP, BRAND_GRADIENT

    if nome not in PALETAS:
        raise KeyError(
            f"acento desconhecido: {nome!r}. Disponíveis: {', '.join(PALETAS)}"
        )
    ACCENT_NAME = nome
    ACCENT = PALETAS[nome]
    ACCENT_TEXT = _para_texto(ACCENT)
    ACCENT_DEEP = _profundo(ACCENT)
    BRAND_GRADIENT = _gradiente_da_marca(ACCENT)
    _sincronizar_tokens()
    return nome


def next_accent() -> str:
    """Avança para o próximo acento do ciclo. Devolve o nome aplicado."""
    nomes = accent_names()
    return set_accent(nomes[(nomes.index(ACCENT_NAME) + 1) % len(nomes)])

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
    warn: str           # sinal de aviso (título de card, linha de nota)


UNICODE = Glyphs(
    bot="▏", prompt="❯", ok="✓", fail="✗", running="◐", skill="↳",
    rail="┃", caret="▍", gauge_full="▰", gauge_empty="▱",
    seal_local="◆", seal_cloud="☁", step_on="⟐", step_off="◦", warn="⚠",
)

ASCII = Glyphs(
    bot="|", prompt=">", ok="OK", fail="XX", running="..", skill="->",
    rail="|", caret="_", gauge_full="#", gauge_empty="-",
    seal_local="*", seal_cloud="~", step_on="=", step_off=".", warn="!",
)


def box_style(g: "Glyphs | None" = None):
    """Moldura do Rich compatível com o conjunto de glifos em uso.

    `box.ROUNDED` desenha `┌─┐│└┘`, que o cp1252 NÃO codifica — um card de
    confirmação com moldura arredondada estoura no cmd legado. E é justamente
    a tela que aparece antes de um `rm -rf`: quebrar ali é o pior lugar
    possível. Achado pela matriz de terminais do F6.
    """
    from rich import box

    ativo = g if g is not None else UNICODE
    return box.ROUNDED if ativo is UNICODE else box.ASCII


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


# ── Ponte para o SPA ─────────────────────────────────────────────────────────
def _montar_tokens() -> "dict[str, str]":
    """Nome do token → valor. Ordem preservada é a ordem do CSS gerado."""
    return {
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


#: Tokens do acento em uso. Recalculado por `set_accent` — o CSS gerado é
#: sempre do acento ATUAL (o arquivo commitado é o do padrão, ver o teste de
#: divergência, que fixa o acento antes de comparar).
TOKENS: "dict[str, str]" = _montar_tokens()


def _sincronizar_tokens() -> None:
    """Reconstrói TOKENS no lugar após uma troca de acento.

    Muta o dict existente em vez de rebindar o nome: código que fez
    `from .theme import TOKENS` continua vendo os valores novos.
    """
    TOKENS.clear()
    TOKENS.update(_montar_tokens())

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
