"""Comando bauer project."""

from __future__ import annotations

from pathlib import Path
from ..workspace_manager_factory import get_workspace_manager
import typer

from ._common import _PROJECT_WORKSPACE, console

project_app = typer.Typer(help="Gerenciamento de projeto (PROJECT.md)")


@project_app.command("init")
def project_init(
    name: str = typer.Argument(..., help="Nome do projeto"),
    description: str = typer.Option("", "--desc", help="Descricao do projeto"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Inicializa o workspace com PROJECT.md e TASKS.md."""
    wm = get_workspace_manager(workspace)
    created = wm.init_project(name, description)
    if created:
        for p in created:
            console.print(f"[green]criado:[/green] {p}")
    else:
        console.print(f"[dim]Projeto ja inicializado em {workspace}/[/dim]")


@project_app.command("status")
def project_status(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Mostra PROJECT.md e resumo de tarefas."""
    wm = get_workspace_manager(workspace)
    console.print(wm.get_project_info())

    tasks = wm.list_tasks()
    if not tasks:
        console.print("[dim]Nenhuma tarefa registrada ainda.[/dim]")
        return

    from collections import Counter
    counts = Counter(t.status for t in tasks)
    summary = "  ".join(f"{s}: {n}" for s, n in sorted(counts.items()))
    console.print(f"[dim]Tarefas — {summary} | Total: {len(tasks)}[/dim]")


@project_app.command("board")
def project_board(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    refresh: int = typer.Option(3, "--refresh", "-r", help="Intervalo de atualizacao em segundos"),
):
    """Kanban ao vivo no terminal — atualiza automaticamente via Rich Live.

    Exibe as tarefas do TASKS.md em colunas por status.
    Pressione Ctrl+C para sair.
    """
    import time
    from rich.live import Live
    from rich.panel import Panel as RPanel
    from rich.columns import Columns
    from rich.text import Text

    _STATUS_ORDER = ["TODO", "IN_PROGRESS", "BLOCKED", "DONE"]
    _STATUS_LABEL = {
        "TODO":        ("TODO",          "blue"),
        "IN_PROGRESS": ("EM PROGRESSO",  "yellow"),
        "BLOCKED":     ("BLOQUEADO",     "red"),
        "DONE":        ("CONCLUIDO",     "green"),
    }
    _COMPACT_THRESHOLD = 8

    def _build_board():
        wm = get_workspace_manager(workspace)
        tasks = wm.list_tasks()
        by_status: dict[str, list] = {s: [] for s in _STATUS_ORDER}
        for t in tasks:
            if t.status in by_status:
                by_status[t.status].append(t)

        panels = []
        for status in _STATUS_ORDER:
            label, color = _STATUS_LABEL[status]
            col_tasks = by_status[status]
            compact = len(col_tasks) > _COMPACT_THRESHOLD
            # Para DONE compacto: mostra só os 8 mais recentes
            visible = col_tasks[-_COMPACT_THRESHOLD:] if compact else col_tasks
            hidden = len(col_tasks) - len(visible)

            lines = Text()
            if hidden > 0:
                lines.append(f"  + {hidden} anteriores...\n", style="dim")
            if not col_tasks:
                lines.append("  (vazio)\n", style="dim")
            for t in visible:
                lines.append(f"  #{t.id} ", style="dim")
                title = t.title if len(t.title) <= 38 else t.title[:35] + "..."
                lines.append(f"{title}\n", style="bold" if status == "IN_PROGRESS" else "")

            count_label = f" ({len(col_tasks)})"
            panels.append(RPanel(
                lines,
                title=f"[{color}]{label}{count_label}[/{color}]",
                border_style=color,
                padding=(0, 1),
            ))

        ts = time.strftime("%H:%M:%S")
        return Columns(panels, equal=True, expand=True), ts

    console.print(f"\n[dim]Kanban ao vivo — {workspace}/TASKS.md "
                  f"(atualiza a cada {refresh}s — Ctrl+C para sair)[/dim]\n")

    with Live(console=console, refresh_per_second=1, screen=False) as live:
        while True:
            try:
                board, ts = _build_board()
                from rich.console import Group
                live.update(Group(board, Text(f"  Atualizado: {ts}", style="dim")))
                time.sleep(refresh)
            except KeyboardInterrupt:
                break

    console.print("\n[dim]Board encerrado.[/dim]")


# ---------------------------------------------------------------------------
# Projeto ATIVO (registro multi-projeto — ~/.bauer/projects.json)
#
# Distinto dos comandos acima, que operam no PROJECT.md de UM workspace. Aqui é
# o ponteiro que diz "em qual projeto o Bauer está" para o `serve` e o Desktop.
#
# Existiam porque faltava: `set_active()` tinha UM call site em todo o codebase
# — `desktop_api.py`, ou seja, só o botão da tela Projetos. Pela CLI não havia
# como trocar, então quem trabalha no terminal ficava com o Desktop apontando
# para outro projeto, em silêncio.
# ---------------------------------------------------------------------------


def _caminho_ou_none(valor: str) -> "Path | None":
    """Resolve um caminho, ou None se o texto não for um caminho utilizável.

    O alvo do `project use` pode ser nome, id OU caminho — quando é nome/id,
    `resolve()` pode falhar (caractere inválido no SO, path longo demais). Isso
    não é erro: só significa "não era caminho, tenta o próximo critério".
    """
    try:
        return Path(valor).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return None


def _resolver_projeto(alvo: str):
    """Resolve nome, id ou caminho para uma entrada do registro.

    Retorna (entry, erro). Aceita as três formas porque é o que o usuário tem à
    mão: o nome que ele vê no `project list`, o id que aparece no JSON, ou o
    próprio caminho quando ele está dentro da pasta.
    """
    from .. import projects_registry as pr

    projetos = pr.list_projects(enrich=False)
    if not projetos:
        return None, "Nenhum projeto registrado. Rode `bauer agent` de dentro da pasta para registrar."

    alvo_norm = alvo.strip()

    exatos = [p for p in projetos if p.get("id") == alvo_norm]
    if exatos:
        return exatos[0], None

    por_nome = [p for p in projetos if (p.get("name") or "").lower() == alvo_norm.lower()]
    if len(por_nome) == 1:
        return por_nome[0], None
    if len(por_nome) > 1:
        ids = ", ".join(p["id"] for p in por_nome)
        return None, f"'{alvo_norm}' é ambíguo — há {len(por_nome)} projetos com esse nome. Use o id: {ids}"

    cam = _caminho_ou_none(alvo_norm)
    if cam is not None:
        por_path = [
            p for p in projetos if _caminho_ou_none(p.get("path", "")) == cam
        ]
        if por_path:
            return por_path[0], None

    parciais = [p for p in projetos if alvo_norm.lower() in (p.get("name") or "").lower()]
    if len(parciais) == 1:
        return parciais[0], None
    if len(parciais) > 1:
        nomes = ", ".join(p.get("name", "?") for p in parciais)
        return None, f"'{alvo_norm}' casa com mais de um projeto: {nomes}"

    disponiveis = ", ".join(p.get("name", "?") for p in projetos)
    return None, f"Projeto '{alvo_norm}' não encontrado. Registrados: {disponiveis}"


@project_app.command("list")
def project_list():
    """Lista os projetos registrados e marca o ativo."""
    from rich.table import Table

    from .. import projects_registry as pr

    projetos = pr.list_projects(enrich=False)
    if not projetos:
        console.print(
            "[yellow]Nenhum projeto registrado.[/yellow]\n"
            "[dim]Rode `bauer agent` de dentro da pasta do projeto para registrar.[/dim]"
        )
        return

    tabela = Table(show_header=True, header_style="bold")
    tabela.add_column("", width=2)
    tabela.add_column("Nome")
    tabela.add_column("Caminho", style="dim")
    tabela.add_column("id", style="dim")

    for p in projetos:
        ativo = p.get("active")
        tabela.add_row(
            "[green]●[/green]" if ativo else "",
            f"[bold]{p.get('name', '?')}[/bold]" if ativo else p.get("name", "?"),
            str(p.get("path", "")),
            p.get("id", ""),
        )
    console.print(tabela)
    console.print("[dim]● = ativo (usado pelo `bauer serve` e pelo Desktop)[/dim]")


@project_app.command("use")
def project_use(
    alvo: str = typer.Argument(..., metavar="PROJETO", help="Nome, id ou caminho do projeto"),
):
    """Define o projeto ATIVO — o que o `bauer serve` e o Desktop usam.

    Aceita nome, id ou caminho:

        bauer project use forex-ia
        bauer project use ~/projetos/forex-ia
    """
    from .. import projects_registry as pr

    entry, erro = _resolver_projeto(alvo)
    if erro:
        console.print(f"[red]{erro}[/red]")
        raise typer.Exit(code=1)

    pid = entry["id"]
    anterior = pr.get_active()
    if anterior == pid:
        console.print(f"[dim]'{entry.get('name')}' já era o projeto ativo.[/dim]")
        return

    if not pr.set_active(pid):
        console.print(f"[red]Não consegui ativar '{entry.get('name')}'.[/red]")
        raise typer.Exit(code=1)

    console.print(
        f"[green]✓ Projeto ativo: [bold]{entry.get('name')}[/bold][/green] "
        f"[dim]{entry.get('path')}[/dim]"
    )
    if anterior:
        ant = pr.get_project(anterior) or {}
        if ant.get("name"):
            console.print(f"[dim]  (antes: {ant['name']})[/dim]")


@project_app.command("active")
def project_active():
    """Mostra qual projeto está ativo."""
    from .. import projects_registry as pr

    pid = pr.get_active()
    if not pid:
        console.print("[yellow]Nenhum projeto ativo.[/yellow] [dim]Use `bauer project use <nome>`.[/dim]")
        raise typer.Exit(code=1)
    entry = pr.get_project(pid) or {}
    console.print(f"[bold]{entry.get('name', pid)}[/bold] [dim]{entry.get('path', '')}[/dim]")
