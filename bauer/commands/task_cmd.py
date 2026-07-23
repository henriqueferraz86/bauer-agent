"""Comando bauer task — gerenciamento de tarefas (TASKS.md)."""

from __future__ import annotations

from pathlib import Path
from rich.table import Table
from ..workspace_manager import WorkspaceError
from ..workspace_manager_factory import get_workspace_manager
from ..workspace_manager_factory import get_workspace_manager
import typer

from ._common import _PROJECT_WORKSPACE, console

task_app = typer.Typer(help="Gerenciamento de tarefas (TASKS.md)")


@task_app.command("add")
def task_add(
    title: str = typer.Argument("", help="Titulo da tarefa — omita para modo entrevista"),
    desc: str = typer.Option("", "--desc", help="Descricao opcional"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Adiciona uma nova tarefa. Sem argumentos: modo entrevista interativo."""
    from ..agent_wizard import wizard_create_task

    spec_id = ""
    if not title:
        result = wizard_create_task()
        if result is None:
            raise typer.Exit(code=0)
        title = result["title"]
        desc = result.get("description", "")
        spec_id = result.get("spec_id", "")
        # Prioridade e agent como prefixo na descrição se definidos
        extras: list[str] = []
        if result.get("priority") and result["priority"] != "media":
            extras.append(f"[{result['priority']}]")
        if result.get("assigned_agent"):
            extras.append(f"@{result['assigned_agent']}")
        if extras:
            desc = " ".join(extras) + (f" {desc}" if desc else "")

    wm = get_workspace_manager(workspace)
    task = wm.add_task(title, desc, spec_id=spec_id)
    spec_tag = f" [dim](spec: {spec_id})[/dim]" if spec_id else ""
    console.print(f"[green]✓ Tarefa {task.id} criada:[/green] {task.title}{spec_tag}")


@task_app.command("list")
def task_list(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    status: str = typer.Option("", "--status", help="Filtrar por status (TODO, DONE, ...)"),
):
    """Lista todas as tarefas com status."""
    wm = get_workspace_manager(workspace)
    tasks = wm.list_tasks()

    if not tasks:
        console.print("[dim]Nenhuma tarefa. Adicione com: bauer task add 'titulo'[/dim]")
        return

    if status:
        tasks = [t for t in tasks if t.status == status.upper()]

    table = Table(title=f"Tarefas — {workspace}/TASKS.md")
    table.add_column("id", style="dim", width=5)
    table.add_column("status", width=12)
    table.add_column("titulo")

    _STATUS_COLOR = {
        "TODO": "white",
        "READY": "cyan",
        "IN_PROGRESS": "yellow",
        "DONE": "green",
        "BLOCKED": "red",
        "FAILED": "magenta",
    }
    for t in tasks:
        color = _STATUS_COLOR.get(t.status, "white")
        table.add_row(t.id, f"[{color}]{t.status}[/{color}]", t.title)

    console.print(table)


def _task_update(workspace: Path, task_id: str, new_status: str) -> None:
    wm = get_workspace_manager(workspace)
    try:
        task = wm.update_task_status(task_id, new_status)
        console.print(f"[green]{task.id}[/green] → [{new_status}] {task.title}")
    except WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)


@task_app.command("start")
def task_start(
    task_id: str = typer.Argument(..., help="ID da tarefa (ex: 001)"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Marca tarefa como IN_PROGRESS."""
    _task_update(workspace, task_id, "IN_PROGRESS")


@task_app.command("done")
def task_done(
    task_id: str = typer.Argument(..., help="ID da tarefa (ex: 001)"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Marca tarefa como DONE."""
    _task_update(workspace, task_id, "DONE")


@task_app.command("block")
def task_block(
    task_id: str = typer.Argument(..., help="ID da tarefa (ex: 001)"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Marca tarefa como BLOCKED."""
    _task_update(workspace, task_id, "BLOCKED")


@task_app.command("ready")
def task_ready(
    task_id: str = typer.Argument(..., help="ID da tarefa (ex: 001)"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    assignee: str = typer.Option("", "--assignee", "-a", help="Responsavel/agente opcional"),
    max_retries: int = typer.Option(2, "--max-retries", help="Tentativas antes de FAILED"),
    max_runtime_seconds: int = typer.Option(0, "--max-runtime-seconds", help="Timeout do worker (0 = sem limite)"),
):
    """Marca tarefa como READY e opt-in para o dispatcher hibrido."""
    from ..task_dispatcher import TaskDispatcher

    dispatcher = TaskDispatcher(workspace, max_retries=max_retries)
    task = dispatcher.mark_ready(
        task_id,
        assignee=assignee,
        max_retries=max_retries,
        max_runtime_seconds=max_runtime_seconds or None,
    )
    console.print(f"[green]{task.id}[/green] -> [READY] {task.title}")


@task_app.command("fail")
def task_fail(
    task_id: str = typer.Argument(..., help="ID da tarefa (ex: 001)"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Marca tarefa como FAILED."""
    _task_update(workspace, task_id, "FAILED")


@task_app.command("board")
def task_board(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    compact: bool = typer.Option(False, "--compact", "-c", help="Mostra apenas ID e titulo (sem descricao)"),
):
    """Exibe o Kanban board no terminal com todos os status de TASKS.md."""
    import sys as _sys
    from rich.columns import Columns
    from rich.markup import escape as _esc
    from rich.panel import Panel
    from rich.text import Text

    # Detecta se o terminal suporta UTF-8 (Linux/Mac sim, Windows legacy nao)
    _utf8 = _sys.platform != "win32" or (
        hasattr(_sys.stdout, "encoding") and
        (_sys.stdout.encoding or "").lower().replace("-", "") == "utf8"
    )

    # Icones: versao rica (UTF-8) ou ASCII puro (Windows legacy)
    if _utf8:
        _ICONS = {
            "TODO":        "📋",
            "READY":       "▶",
            "IN_PROGRESS": "🔄",
            "DONE":        "✅",
            "BLOCKED":     "🚫",
            "FAILED":      "✖",
        }
        _BAR_FULL  = "█"
        _BAR_EMPTY = "░"
        _ELLIPSIS  = "…"
    else:
        _ICONS = {
            "TODO":        "[ ]",
            "READY":       "[>]",
            "IN_PROGRESS": "[~]",
            "DONE":        "[x]",
            "BLOCKED":     "[!]",
            "FAILED":      "[x!]",
        }
        _BAR_FULL  = "#"
        _BAR_EMPTY = "."
        _ELLIPSIS  = "..."

    wm = get_workspace_manager(workspace)
    tasks = wm.list_tasks()

    if not tasks:
        console.print("[dim]Nenhuma tarefa. Adicione com: bauer task add 'titulo'[/dim]")
        return

    # Configuracao de cada coluna: (status, label, cor)
    COLUMNS = [
        ("TODO",        "TODO",        "bright_white"),
        ("READY",       "READY",       "cyan"),
        ("IN_PROGRESS", "IN PROGRESS", "yellow"),
        ("BLOCKED",     "BLOCKED",     "red"),
        ("FAILED",      "FAILED",      "magenta"),
        ("DONE",        "DONE",        "green"),
    ]

    _CARD_COLOR = {
        "TODO":        "white",
        "READY":       "cyan",
        "IN_PROGRESS": "yellow",
        "DONE":        "green",
        "BLOCKED":     "red",
        "FAILED":      "magenta",
    }

    # Agrupa tarefas por status
    by_status: dict[str, list] = {s: [] for s, *_ in COLUMNS}
    for t in tasks:
        bucket = by_status.get(t.status)
        if bucket is not None:
            bucket.append(t)

    panels = []
    for status, label, border_color in COLUMNS:
        col_tasks = by_status[status]
        icon = _ICONS.get(status, status)

        # Monta o conteudo do painel
        lines = Text()
        if not col_tasks:
            lines.append("  (vazio)\n", style="dim")
        else:
            for t in col_tasks:
                card_color = _CARD_COLOR.get(status, "white")
                lines.append(f" [{t.id}] ", style="dim")
                lines.append(t.title, style=card_color)
                lines.append("\n")
                if not compact and t.description:
                    desc = t.description[:40] + (_ELLIPSIS if len(t.description) > 40 else "")
                    lines.append(f"       {desc}\n", style="dim")

        title = f"{_esc(icon)} {label} ({len(col_tasks)})"
        panels.append(
            Panel(
                lines,
                title=f"[bold {border_color}]{title}[/bold {border_color}]",
                border_style=border_color,
                expand=True,
                padding=(0, 1),
            )
        )

    # Barra de progresso
    total = len(tasks)
    done_count = len(by_status["DONE"])
    pct = int(done_count / total * 100) if total else 0
    bar = _BAR_FULL * (pct // 5) + _BAR_EMPTY * (20 - pct // 5)

    console.print()
    console.print(Columns(panels, equal=True, expand=True))
    console.print(
        f"[dim]  Progresso: {bar} {pct}%  "
        f"({done_count}/{total} concluidas)[/dim]\n"
    )
