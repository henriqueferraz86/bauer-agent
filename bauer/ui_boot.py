"""ui_boot.py — a sequência de boot que mostra o que a máquina de fato sabe (F4).

Antes: arte estática, e a sessão começava. As checagens que o Bauer faz — RAM
disponível, contexto que coube de verdade, provider vivo, modo de tool calling,
tools carregadas — aconteciam em silêncio, e o usuário só descobria que algo
estava torto quando o turno falhava.

**A tentação que este módulo evita.** "Boot ultrarealista" convida a rodar o
doctor a cada início para ter o que animar. Seria teatro caro: o
`_get_or_run_state` lê o `runtime_state.json` e só re-executa quando a config
muda, e a memória do projeto registra 23s→2s de startup conquistados com SSL
compartilhado e AuthManager preguiçoso. Forçar checagem para ter animação
jogaria isso fora.

Então a regra aqui é: **nada é medido para exibir**. Cada linha mostra um fato
que o boot já conhecia, com a PROCEDÊNCIA junto — medido agora, ou lido do
cache e de quando. Um painel que diz "RAM 46 GB" sem dizer que isso foi medido
há três dias é tão enganoso quanto não mostrar nada.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from . import theme, ui


def _idade(iso: str) -> str:
    """'agora', '3 min', '2 h', '5 d' — desde um timestamp ISO."""
    if not iso:
        return ""
    try:
        quando = datetime.fromisoformat(iso)
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""
    segundos = (datetime.now(timezone.utc) - quando).total_seconds()
    if segundos < 90:
        return "agora"
    if segundos < 5400:
        return f"{int(segundos // 60)} min"
    if segundos < 172800:
        return f"{int(segundos // 3600)} h"
    return f"{int(segundos // 86400)} d"


def boot_line(
    rotulo: str,
    valor: str,
    *,
    status: str = "ok",       # ok | warn | fail | info
    detalhe: str = "",
) -> Text:
    """Uma linha de boot: `[✓] contexto    32.768 tokens   (aplicado)`."""
    g = ui.active_glyphs()
    glifo, cor = {
        "ok": (g.ok, theme.OK),
        "warn": (g.fail if status == "fail" else "!", theme.WARN),
        "fail": (g.fail, theme.BAD),
    }.get(status, (g.step_off, theme.FAINT))

    t = Text("  ")
    t.append("[", style=theme.FAINT)
    t.append(glifo, style=cor)
    t.append("] ", style=theme.FAINT)
    t.append(rotulo.ljust(12), style=theme.DIM)
    t.append(valor, style=theme.WHITE)
    if detalhe:
        t.append(f"  {detalhe}", style=theme.FAINT)
    return t


def boot_lines(
    state: "dict",
    *,
    tool_mode: str = "",
    tools: "list[str] | None" = None,
    local: "bool | None" = None,
) -> "list[Text]":
    """Traduz o `runtime_state` (+ o que o boot já computou) em linhas.

    Só lê o que já existe. Nenhuma chamada de rede, nenhum probe — ver o
    docstring do módulo.
    """
    linhas: "list[Text]" = []
    ctx = state.get("context") or {}

    provider = str(state.get("configured_provider") or "?")
    modelo = str(state.get("active_model") or state.get("configured_model") or "?")
    disponivel = bool(state.get("model_available", True))
    linhas.append(boot_line(
        "modelo", modelo,
        status="ok" if disponivel else "fail",
        detalhe=provider + ("" if disponivel else "  — não encontrado"),
    ))

    aplicado = ctx.get("applied")
    pedido = ctx.get("requested")
    if aplicado:
        # O contexto APLICADO é o que importa e quase nunca é o pedido: RAM,
        # modelfile e teto do provider cortam. Mostrar só o pedido foi por anos
        # a origem de "configurei 128k e ele truncou".
        detalhe = ""
        if pedido and int(pedido) != int(aplicado):
            detalhe = f"pedido {int(pedido):,}".replace(",", ".")
        linhas.append(boot_line(
            "contexto", f"{int(aplicado):,}".replace(",", ".") + " tokens",
            status="ok" if not detalhe else "warn",
            detalhe=detalhe,
        ))

    ram_livre = state.get("ram_available_mb") or 0
    ram_total = state.get("ram_total_mb") or 0
    if ram_total:
        linhas.append(boot_line(
            "memória", f"{ram_livre / 1024:.1f} GB livres",
            detalhe=f"de {ram_total / 1024:.0f} GB",
        ))

    modo = tool_mode or str(state.get("tool_mode") or "")
    if modo:
        # `none` significa que o modelo não chama tool nenhuma — é a diferença
        # entre um agente e um chat, e passava despercebido.
        linhas.append(boot_line(
            "tools", f"{len(tools)} carregadas" if tools else "—",
            status="fail" if modo == "none" else "ok",
            detalhe=f"modo {modo}",
        ))

    if local is not None:
        g = ui.active_glyphs()
        linhas.append(boot_line(
            "execução",
            f"{g.seal_local} nesta máquina" if local else f"{g.seal_cloud} nuvem",
        ))

    return linhas


def aplicar_preferencia_de_cor(console, ui_config=None) -> str:
    """Aplica as preferências visuais do config. Devolve a cor em uso.

    O Rich fixa o `color_system` na construção do `Console`; trocar depois é
    mexer no atributo interno `_color_system`. Feito aqui, num lugar só, e com
    o valor traduzido pelo enum do próprio Rich — para não inventar um segundo
    vocabulário de cor.
    """
    try:
        from rich.color import ColorSystem

        from .config_loader import load_config

        ui_config = ui_config or load_config().ui
        pref = str(getattr(ui_config, "truecolor", "auto") or "auto").lower()
        if pref == "sim":
            console._color_system = ColorSystem.TRUECOLOR
        elif pref == "nao":
            console._color_system = ColorSystem.STANDARD
        modo = str(getattr(ui_config, "mode", "rich") or "rich").lower()
        # Variáveis de ambiente são o escape hatch para CI/pipes e vencem a
        # preferência persistida, como já faziam antes de existir `ui.mode`.
        if os.environ.get("BAUER_UI", "").strip().lower() == "plain":
            modo = "plain"
        if modo == "plain":
            console._color_system = None
        ui.configure(
            mode=modo,
            emojis=bool(getattr(ui_config, "emojis", True)),
            stream=getattr(console, "file", None),
        )
    except Exception as exc:  # noqa: BLE001 — preferência ilegível: mantém o auto
        from .logging_config import log_suppressed
        log_suppressed("ui.preferencia_de_cor", exc)
    return str(getattr(console, "color_system", None))


def linha_de_cor(console) -> "Text | None":
    """Avisa quando o terminal não entrega as cores que o tema assume.

    O design system trabalha em truecolor: 17 acentos separados por contraste e
    distância perceptual (ΔE). Num terminal de 8 cores ANSI eles colapsam em
    meia dúzia — roxo vira magenta, azul vira ciano, o gradiente do logo vira
    listra. Medido em uso real: `TERM=xterm` sem `COLORTERM`.

    A causa quase sempre é SSH: ele encaminha `TERM` e **não** encaminha
    `COLORTERM`. O terminal de quem está do outro lado costuma suportar
    truecolor; o que se perde é o aviso disso.

    Devolve None quando está tudo bem — aviso que aparece sempre vira ruído e
    para de ser lido.
    """
    sistema = getattr(console, "color_system", None)
    if sistema in ("truecolor", "windows", None):
        return None   # None = sem cor (pipe/CI): o tema já degrada sozinho

    g = ui.active_glyphs()
    t = Text("  ")
    t.append("[", style=theme.FAINT)
    t.append(g.warn, style=theme.WARN)
    t.append("] ", style=theme.FAINT)
    t.append("cores      ", style=theme.DIM)
    quantas = {"standard": "8", "256": "256", "eight_bit": "256"}.get(str(sistema), str(sistema))
    t.append(f"terminal em {quantas} cores", style=theme.WHITE)
    t.append("  — os acentos ficam parecidos entre si", style=theme.FAINT)
    # Ensina o comando do BAUER, não `export` no `.bashrc`: a variável de
    # ambiente afirma a capacidade para TODO programa e fica fixa mesmo quando
    # o terminal muda; a config do Bauer vale só para o Bauer e viaja junto.
    t.append("\n               bauer config set ui.truecolor sim", style=theme.DIM)
    t.append("   (se este terminal suporta; o SSH não avisa)", style=theme.FAINT)
    return t


def boot_panel(
    state: "dict",
    *,
    tool_mode: str = "",
    tools: "list[str] | None" = None,
    local: "bool | None" = None,
    console=None,
) -> RenderableType:
    """As linhas de boot + a procedência do estado (medido agora vs cache)."""
    linhas = boot_lines(state, tool_mode=tool_mode, tools=tools, local=local)

    idade = _idade(str(state.get("generated_at") or ""))
    rodape = Text("  ")
    if idade == "agora":
        rodape.append("verificado agora", style=theme.FAINT)
    elif idade:
        # Procedência explícita: um painel que afirma "46 GB livres" sem dizer
        # que a medição é de três dias atrás mente com números verdadeiros.
        rodape.append(f"do cache, medido há {idade}", style=theme.FAINT)
        rodape.append("  ·  bauer doctor para remedir", style=theme.FAINT)

    avisos = [n for n in (state.get("notes") or []) if str(n).strip()]
    corpo: "list[RenderableType]" = list(linhas)

    # Antes dos avisos do doctor: sem cor de verdade, o resto da tela mente
    # sobre si mesma, então este aviso vale mais que os outros.
    if console is not None:
        _cor = linha_de_cor(console)
        if _cor is not None:
            corpo.append(_cor)

    for aviso in avisos[:3]:
        corpo.append(boot_line("", str(aviso), status="warn"))
    if idade:
        corpo.append(rodape)

    grid = Table.grid(padding=0)
    grid.add_column()
    for linha in corpo:
        grid.add_row(linha)
    return grid


def serve_panel(base_url: str, *, cockpit: bool, auth: bool, docs: bool = True) -> RenderableType:
    """O que o `bauer serve` acabou de abrir — com o cockpit em destaque.

    O SPA é servido em `/` desde sempre e o boot nunca o mencionou: 16 telas
    prontas que ninguém via porque a única pista era uma linha de HTTP.
    """
    g = ui.active_glyphs()
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="left", width=2)
    grid.add_column(justify="left", width=9)
    grid.add_column(justify="left")

    if cockpit:
        grid.add_row(
            Text(g.seal_local, style=theme.ACCENT),
            Text("cockpit", style=theme.DIM),
            Text(base_url, style=f"bold {theme.ACCENT_TEXT}"),
        )
    grid.add_row(
        Text(g.step_off, style=theme.FAINT),
        Text("API", style=theme.DIM),
        Text(f"{base_url}/docs" if docs else base_url, style=theme.WHITE),
    )
    grid.add_row(
        Text(g.step_off, style=theme.FAINT),
        Text("auth", style=theme.DIM),
        Text("habilitada", style=theme.OK) if auth
        else Text("desabilitada — qualquer um na rede pode usar", style=theme.WARN),
    )
    return Group(grid)
