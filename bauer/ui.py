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
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import theme

#: Nomes de cor que este módulo reexporta. NÃO são importados por valor: o
#: acento é trocável em tempo de execução (`theme.set_accent`, Ctrl+T), e
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
_MODE = "rich"
_EMOJIS = True


def use_glyphs(g: "theme.Glyphs | None" = None, *, stream=None) -> theme.Glyphs:
    """Fixa o conjunto de glifos da sessão. Sem argumento, detecta pelo stream."""
    global _ACTIVE
    if _MODE == "plain" or not _EMOJIS:
        _ACTIVE = theme.ASCII
    else:
        _ACTIVE = g if g is not None else theme.glyphs(stream=stream)
    return _ACTIVE


def active_glyphs() -> theme.Glyphs:
    return _ACTIVE


def configure(*, mode: str = "rich", emojis: bool = True, stream=None) -> theme.Glyphs:
    """Aplica a preferência visual da sessão sem alterar variáveis do SO.

    ``plain`` é uma escolha explícita do usuário: suprime cor e glifos
    decorativos nos renderizadores do Bauer. ``compact`` preserva a semântica,
    mas faz cards virarem linhas para caber em terminais menores. O fallback de
    encoding continua sendo responsabilidade de ``theme.glyphs``.
    """
    global _MODE, _EMOJIS
    _MODE = mode if mode in {"rich", "compact", "plain"} else "rich"
    _EMOJIS = bool(emojis)
    return use_glyphs(stream=stream)


def visual_mode() -> str:
    return _MODE


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


# ── Linguagem textual compartilhada ─────────────────────────────────────────
_STATUS = {
    "success": ("ok", theme.OK),
    "running": ("running", theme.ACCENT),
    "info": ("bot", theme.ACCENT),
    "warning": ("warn", theme.WARN),
    "error": ("fail", theme.BAD),
    "blocked": ("fail", theme.BAD),
}


def status_line(label: str, detail: str = "", *, kind: str = "info") -> Text:
    """Linha semântica, legível também no conjunto ASCII.

    Não aceita markup de chamadores: títulos, detalhes e paths vindos do runtime
    são sempre texto. Isso evita uma saída de tool acidentalmente virar estilo
    Rich e mantém a aparência uniforme entre comandos.
    """
    glyph_name, color = _STATUS.get(kind, _STATUS["info"])
    glyph = getattr(_ACTIVE, glyph_name)
    text = Text("  ")
    text.append("[", style=theme.FAINT)
    text.append(glyph, style=color)
    text.append("] ", style=theme.FAINT)
    text.append(label, style=f"bold {theme.WHITE}" if kind in {"error", "blocked"} else theme.WHITE)
    if detail:
        text.append("  ", style=theme.FAINT)
        text.append(detail, style=theme.DIM)
    return text


def notice(title: str, detail: str = "", *, kind: str = "info", hint: str = "") -> RenderableType:
    """Aviso, erro ou confirmação com o mesmo contrato visual em toda a CLI."""
    line = status_line(title, detail, kind=kind)
    if _MODE in {"compact", "plain"}:
        return Group(line, Text(f"     {hint}", style=theme.FAINT)) if hint else line

    _, color = _STATUS.get(kind, _STATUS["info"])
    body: list[RenderableType] = [line]
    if hint:
        body.append(Text(f"  {hint}", style=theme.FAINT))
    return Panel(
        Group(*body),
        border_style=color,
        box=theme.box_style(_ACTIVE),
        padding=(0, 1),
    )


def session_header(
    title: str,
    *,
    workspace: str = "",
    model: str = "",
    provider: str = "",
    meta: "list[str] | tuple[str, ...]" = (),
) -> RenderableType:
    """Resumo inicial de uma sessão ou execução sem repetir uma parede de texto."""
    rows: list[tuple[str, str]] = []
    if workspace:
        rows.append(("workspace", workspace))
    if model:
        rows.append(("modelo", model + (f" ({provider})" if provider else "")))
    if meta:
        rows.append(("execução", " · ".join(item for item in meta if item)))

    if _MODE in {"compact", "plain"}:
        detail = " · ".join(f"{label}: {value}" for label, value in rows)
        return status_line(title, detail, kind="running")

    grid = Table.grid(padding=(0, 1), expand=True)
    grid.add_column(style=theme.DIM, no_wrap=True)
    grid.add_column(style=theme.WHITE, overflow="fold")
    for label, value in rows:
        grid.add_row(label, value)
    return Panel(
        grid,
        title=Text(title, style=f"bold {theme.ACCENT_TEXT}"),
        title_align="left",
        border_style=theme.ACCENT,
        box=theme.box_style(_ACTIVE),
        padding=(0, 1),
    )


def agent_hud_header(
    *,
    workspace: str,
    model: str,
    provider: str,
    tool_count: int,
    tool_mode: str,
    local: bool,
    resumed: bool,
) -> RenderableType:
    """HUD compacto do ``bauer agent``: identidade, estado e contexto útil.

    Diferentemente de ``session_header`` (feito para o resumo de um ``run``),
    a conversa interativa precisa de uma única faixa discreta no topo. O
    diagnóstico completo continua disponível em ``bauer status``/``doctor``;
    não deve empurrar a primeira mensagem do usuário para baixo.
    """
    if _MODE in {"compact", "plain"}:
        location = workspace or "workspace atual"
        state = "retomada" if resumed else "nova sessão"
        return status_line(
            "bauer agent",
            f"online · {provider} / {model} · {location} · {state}",
            kind="running",
        )

    unicode = _ACTIVE is not theme.ASCII
    sparkle = "✦" if unicode else "*"
    online = "●" if unicode else "*"
    folder = "⌂" if unicode else "@"
    runtime = "local" if local else "cloud"
    session_state = "retomada" if resumed else "nova sessão"

    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)

    brand = Text()
    brand.append(f"{sparkle}  BAUER AGENT", style=f"bold {theme.ACCENT_TEXT}")
    brand.append("  ·  INTERATIVO", style=f"bold {theme.ACCENT_TEXT}")
    grid.add_row(brand, Text(""), Text(""))

    connection = Text()
    connection.append(f"{online} ", style=theme.OK)
    connection.append("online  ", style=theme.DIM)
    connection.append(f"{provider} / {model}", style=theme.WHITE)

    place = Text()
    place.append(f"{folder} ", style=theme.ACCENT)
    place.append("workspace  ", style=theme.DIM)
    place.append(workspace or "atual", style=theme.WHITE)

    execution = Text()
    execution.append("◎ ", style=theme.ACCENT)
    execution.append(f"{tool_count} tools · {tool_mode} · {runtime}", style=theme.DIM)
    execution.append(f" · {session_state}", style=theme.FAINT)
    grid.add_row(connection, place, execution)

    return Panel(
        grid,
        border_style=theme.ACCENT,
        box=theme.box_style(_ACTIVE),
        padding=(0, 1),
    )


def progress_line(round_number: int, *, tools: int, tools_limit: int, elapsed: str, cost: str) -> Text:
    """Progresso de execução autônoma, sempre com os mesmos campos e ordem."""
    return status_line(
        f"rodada {round_number}",
        f"{tools}/{tools_limit} tools · {elapsed} · {cost}",
        kind="running",
    )


def result_card(
    title: str,
    detail: str,
    *,
    kind: str = "success",
    hint: str = "",
) -> RenderableType:
    """Fechamento de uma operação; nunca inventa métricas ausentes."""
    return notice(title, detail, kind=kind, hint=hint)


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
    """Uma ação do agente em trilho: ``  ┃ [✓] read_file  auth/login.py``.

    Status primeiro em colchetes (verde/vermelho); nome alinhado em coluna;
    args e tempo esmaecidos. Calma e escaneável de cima a baixo.
    """
    glyph, gstyle = {
        "ok": (_ACTIVE.ok, theme.OK),
        "fail": (_ACTIVE.fail, theme.BAD),
    }.get(status, (GLYPH_RUNNING, theme.DIM))

    t = Text("  ")
    t.append(f"{_ACTIVE.rail}  ", style=theme.FAINT)
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
    from rich.panel import Panel

    return Panel(
        corpo,
        title=Text(titulo, style=f"bold {theme.WARN}"),
        title_align="left",
        border_style=theme.FAINT,
        # A moldura segue os glifos: `ROUNDED` usa box-drawing que o cp1252 não
        # codifica, e este card é o que aparece antes de um comando perigoso.
        box=theme.box_style(_ACTIVE),
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


def accent_swatch_line(nome: str) -> RenderableType:
    """Amostra do acento novo aplicada em componentes REAIS.

    Por que uma amostra e não só o nome: **texto já impresso não pode ser
    recolorido**. O painel de sessão, o logo e as linhas de boot são
    scrollback — bytes que já saíram para o terminal. Só o prompt e a barra de
    status são redesenhados a cada tecla, e trocar a cor de dois glifos
    pequenos parece "não fez nada" (reportado em uso real).

    Então a confirmação mostra o tema novo NO QUE ELE VAI PINTAR daqui para a
    frente: linha de tool, medidor de contexto, esteira. É uma prévia do
    próximo turno, no lugar de uma promessa.
    """
    from rich.console import Group

    g = active_glyphs()
    cor = theme.PALETAS.get(nome, theme.ACCENT)
    claro = theme._para_texto(cor)

    titulo = Text("  ")
    titulo.append(f"{g.seal_local} ", style=cor)
    titulo.append(f"{g.gauge_full * 4} ", style=cor)
    titulo.append(nome, style=f"bold {claro}")
    titulo.append("   Ctrl+T cicla · /theme lista", style=theme.FAINT)

    amostra = Text("  ")
    amostra.append("[", style=theme.FAINT)
    amostra.append(g.ok, style=theme.OK)
    amostra.append("] ", style=theme.FAINT)
    amostra.append("read_file".ljust(11), style=theme.WHITE)
    amostra.append("exemplo.py", style=theme.DIM)
    amostra.append("   ctx ", style=theme.DIM)
    amostra.append(g.gauge_full * 3, style=cor)
    amostra.append(g.gauge_empty * 5, style=theme.FAINT)
    amostra.append("  ", style=theme.DIM)
    amostra.append(g.step_on * 3, style=cor)
    amostra.append(g.step_off, style=theme.FAINT)

    nota = Text("  ", style=theme.FAINT)
    nota.append("o que já está na tela mantém a cor antiga — só o novo usa esta",
                style=theme.FAINT)

    return Group(titulo, amostra, nota)


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
