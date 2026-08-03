"""Bauer UI kit — linguagem visual do terminal (Tema Minimal).

Estética escolhida: sóbria e refinada. Quase monocromática (branco/cinza) +
UM acento neon (violeta elétrico). Ações do agente status-primeiro em
colchetes [✓]/[✗], resposta com barra de acento à esquerda. Componentes puros
(retornam Text/renderáveis Rich), testáveis via `render_str`.

As cores e glifos NÃO nascem aqui: vêm de `bauer/theme.py`, que também gera o
CSS do SPA. Ver plano 028 — três paletas divergentes foi o que motivou isso.
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from . import theme

#: Nomes de cor que este módulo reexporta. NÃO são importados por valor: o
#: acento é trocável em tempo de execução (`theme.set_accent`, Ctrl+0), e
#: `from .theme import ACCENT` congelaria a cor do import — foi exatamente
#: assim que a `bottom_toolbar` e o `_PT_STYLE` ficaram para trás no F0.
#: As funções abaixo leem `theme.X` na hora do render; este `__getattr__`
#: (PEP 562) mantém `ui.ACCENT` vivo para quem lê de fora.
_REEXPORTA = frozenset(
    {"ACCENT", "ACCENT_TEXT", "ACCENT_DEEP", "BAD", "DIM", "FAINT", "OK", "WARN",
     "WHITE", "CLOUD", "VOID", "SURFACE", "LINE"}
)


def __getattr__(name: str):
    if name in _REEXPORTA:
        return getattr(theme, name)
    if name == "GRADIENT":
        #: Gradiente da MARCA (logo/boot). Conteúdo nunca usa.
        return theme.BRAND_GRADIENT
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# ── Glifos ──────────────────────────────────────────────────────────────────
# Constantes mantidas para compatibilidade de import; os componentes resolvem
# o conjunto em tempo de render (`theme.glyphs()`) para cair em ASCII num
# console que não codifica os blocos.
GLYPH_BOT = theme.UNICODE.bot
GLYPH_PROMPT = theme.UNICODE.prompt
GLYPH_OK = theme.UNICODE.ok
GLYPH_FAIL = theme.UNICODE.fail
GLYPH_RUNNING = "·"    # tool em andamento (status neutro)
GLYPH_SKILL = theme.UNICODE.skill

#: Largura p/ alinhar o nome da tool em coluna (visual de tabela sem tabela).
_NAME_COL = 15

#: Conjunto de glifos em uso. Default Unicode para render determinístico em
#: teste; a aplicação decide UMA vez no boot com `use_glyphs(stream=...)`,
#: olhando o console real. Resolver por chamada olharia `sys.stdout`, que não é
#: necessariamente o destino do render (Rich escreve no `file` do Console).
_ACTIVE: theme.Glyphs = theme.UNICODE


def use_glyphs(g: "theme.Glyphs | None" = None, *, stream=None) -> theme.Glyphs:
    """Fixa o conjunto de glifos da sessão. Sem argumento, detecta pelo stream."""
    global _ACTIVE
    _ACTIVE = g if g is not None else theme.glyphs(stream=stream)
    return _ACTIVE


def active_glyphs() -> theme.Glyphs:
    return _ACTIVE


# ── Gradiente (helpers, não usados pelo tema Minimal) ───────────────────────
def _lerp(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    return f"#{round(r1+(r2-r1)*t):02x}{round(g1+(g2-g1)*t):02x}{round(b1+(b2-b1)*t):02x}"


def grad_color(frac: float, stops: "list[str] | None" = None) -> str:
    # `stops=GRADIENT` como default seria avaliado no IMPORT e congelaria o
    # gradiente do acento inicial — com a troca em tempo de execução, o logo
    # ficaria na cor antiga. None resolve na chamada.
    stops = stops if stops is not None else theme.BRAND_GRADIENT
    if frac <= 0:
        return stops[0]
    if frac >= 1:
        return stops[-1]
    seg = frac * (len(stops) - 1)
    i = int(seg)
    return _lerp(stops[i], stops[i + 1], seg - i)


# ── Cabeçalho da resposta ────────────────────────────────────────────────────
def response_header(model: str = "", cost: str = "", elapsed: str = "") -> RenderableType:
    """`▏ bauer` (barra de acento) à esquerda; meta esmaecida à direita.

    Sem régua/gradiente — a barra de acento é o único toque de cor no cabeçalho.
    """
    meta_parts = [p for p in (model, cost, elapsed) if p]
    grid = Table.grid(expand=True, padding=0)
    grid.add_column(justify="left")
    grid.add_column(justify="right")
    left = Text()
    left.append(f"{_ACTIVE.bot} ", style=f"bold {theme.ACCENT}")
    # ACCENT_TEXT no nome: 4.95:1 do acento puro é apertado para palavra em
    # corpo de texto; o glifo (não-texto) fica no acento cheio.
    left.append("bauer", style=f"bold {theme.ACCENT_TEXT}")
    right = Text(" · ".join(meta_parts), style=theme.DIM) if meta_parts else Text()
    grid.add_row(left, right)
    return grid


# ── Linha de tool (status-primeiro) ─────────────────────────────────────────
def _fmt_elapsed(ms: int | None) -> str:
    if ms is None or ms < 0:
        return ""
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms/1000:.1f}s"


def tool_line(
    name: str,
    arg_summary: str = "",
    *,
    status: str = "run",   # run | ok | fail
    elapsed_ms: int | None = None,
    rail: bool = False,     # compat de assinatura (ignorado no tema Minimal)
) -> Text:
    """Uma ação do agente:  ``  [✓] read_file      auth/login.py   90ms``

    Status primeiro em colchetes (verde/vermelho); nome alinhado em coluna;
    args e tempo esmaecidos. Calma e escaneável de cima a baixo.
    """
    glyph, gstyle = {
        "ok": (_ACTIVE.ok, theme.OK),
        "fail": (_ACTIVE.fail, theme.BAD),
    }.get(status, (GLYPH_RUNNING, theme.DIM))

    t = Text("  ")
    t.append("[", style=theme.FAINT)
    t.append(glyph, style=gstyle)
    t.append("] ", style=theme.FAINT)
    t.append(name.ljust(_NAME_COL), style=theme.WHITE)
    if arg_summary:
        t.append(" ")
        t.append(arg_summary, style=theme.DIM)
    el = _fmt_elapsed(elapsed_ms)
    if el:
        t.append(f"  {el}", style=theme.FAINT)
    return t


def tool_block(
    name: str,
    arg_summary: str = "",
    *,
    status: str = "run",
    elapsed_ms: int | None = None,
    result: str = "",
) -> RenderableType:
    """A ação do agente COM corpo (plano 028 F3).

    Cabeçalho igual ao `tool_line` (a linha continua sendo a unidade de leitura)
    e, embaixo, o que a ação de fato fez:

      - edição de arquivo → o diff que a tool aplicou, colorido;
      - qualquer outra    → nada (o resumo do cabeçalho basta).

    O diff NÃO é recalculado aqui: vem pronto no resultado da tool `patch`
    (ver ui_diff). Recalcular abriria espaço para exibir uma coisa e ter
    escrito outra.
    """
    from rich.console import Group

    from .ui_diff import parece_diff, render_diff, separar_diff

    cabecalho = tool_line(name, arg_summary, status=status, elapsed_ms=elapsed_ms)
    if not result or not parece_diff(result):
        return cabecalho
    _msg, diff = separar_diff(result)
    if not diff.strip():
        return cabecalho
    return Group(cabecalho, render_diff(diff))


def approval_card(titulo: str, corpo: RenderableType) -> RenderableType:
    """Card de decisão de comando, no tema (plano 028 F3).

    Substitui o `Panel` amarelo cru — a cor de aviso continua no TÍTULO (é
    aviso de verdade), mas a moldura entra na paleta em vez de puxar um
    "yellow" do Rich que não existe em lugar nenhum do design.
    """
    from rich import box
    from rich.panel import Panel

    return Panel(
        corpo,
        title=Text(titulo, style=f"bold {theme.WARN}"),
        title_align="left",
        border_style=theme.FAINT,
        box=box.ROUNDED,
        padding=(0, 1),
    )


def approval_options() -> Text:
    """A linha de opções do card. `a` em destaque: é a que ENSINA — pergunta
    uma vez e nunca mais, e é por isso que o gate deixa de engessar."""
    t = Text("  ")
    for i, (tecla, rotulo, ensina) in enumerate((
        ("e", "executar uma vez", False),
        ("s", "toda a sessão", False),
        ("a", "sempre (aprende)", True),
        ("n", "negar", False),
    )):
        if i:
            t.append(" · ", style=theme.FAINT)
        t.append(tecla, style=f"bold {theme.ACCENT_TEXT if ensina else theme.WHITE}")
        t.append(f" {rotulo}", style=theme.DIM if ensina else theme.FAINT)
    return t


def accent_swatches(destaque: str = "") -> RenderableType:
    """Catálogo de acentos, cada nome pintado NA PRÓPRIA COR.

    Amostra em vez de lista: nome de cor não diz nada ("papaia" é o quê?), e o
    ponto de existirem 17 acentos é escolher olhando.
    """
    g = active_glyphs()
    grid = Table.grid(padding=(0, 2))
    for _ in range(4):
        grid.add_column()

    celulas: list[Text] = []
    for nome, cor in theme.PALETAS.items():
        t = Text()
        atual = nome == destaque
        t.append(f"{g.seal_local if atual else ' '} ", style=cor)
        t.append(f"{g.gauge_full * 3} ", style=cor)
        t.append(nome, style=f"bold {cor}" if atual else theme.DIM)
        celulas.append(t)

    for i in range(0, len(celulas), 4):
        linha = celulas[i:i + 4]
        linha += [Text("")] * (4 - len(linha))
        grid.add_row(*linha)
    return grid


def skill_line(name: str, score_pct: int) -> Text:
    """`  ↳ skill 'X' (80%)` — nota discreta de skill aplicada."""
    t = Text("  ")
    t.append(f"{_ACTIVE.skill} ", style=theme.ACCENT)
    t.append(f"skill '{name}'", style=theme.DIM)
    t.append(f" ({score_pct}%)", style=theme.FAINT)
    return t


# ── Medidor de contexto (mono + acento; vermelho só em perigo) ──────────────
def context_gauge(pct: float, width: int = 10) -> Text:
    """Barra ▰▰▰▱▱▱ em acento (preenchido) + apagado (vazio). O pct fica
    esmaecido; vira vermelho só quando >85% (perigo de estouro)."""
    pct = max(0.0, min(1.0, pct))
    filled = round(pct * width)
    danger = pct > 0.85
    fill_style = theme.BAD if danger else theme.ACCENT
    pct_style = theme.BAD if danger else theme.DIM
    t = Text()
    t.append(_ACTIVE.gauge_full * filled, style=fill_style)
    t.append(_ACTIVE.gauge_empty * (width - filled), style=theme.FAINT)
    t.append(f" {int(pct*100)}%", style=pct_style)
    return t


# ── Preview / teste ─────────────────────────────────────────────────────────
def render_str(renderable: RenderableType, width: int = 60) -> str:
    from rich.console import Console
    import io
    con = Console(file=io.StringIO(), width=width, highlight=False, color_system=None)
    con.print(renderable)
    return con.file.getvalue()
