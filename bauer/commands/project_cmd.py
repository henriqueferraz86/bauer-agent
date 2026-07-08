"""Comando bauer project."""

from __future__ import annotations

from pathlib import Path
from ..workspace_manager import WorkspaceManager
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
    wm = WorkspaceManager(workspace)
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
    wm = WorkspaceManager(workspace)
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
        wm = WorkspaceManager(workspace)
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
