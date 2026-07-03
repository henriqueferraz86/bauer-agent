"""Comando bauer boards."""

from __future__ import annotations

import typer

from ._common import console

boards_app = typer.Typer(help="Multi-board kanban — cada projeto pode ter seu proprio store SQLite")


@boards_app.command("list")
def boards_list_cmd():
    """Lista todos os boards kanban_db existentes em ~/.bauer/kanban/boards/."""
    from .. import kanban_db as _kb
    from rich.table import Table

    boards = _kb.list_boards()
    active = _kb.get_active_board()
    if not boards:
        console.print("[dim]Nenhum board encontrado. Crie com [bold]bauer boards create <nome>[/bold].[/dim]")
        return

    tbl = Table(title=f"Kanban boards ({len(boards)})", show_header=True,
                header_style="bold cyan")
    tbl.add_column("Nome", no_wrap=True)
    tbl.add_column("Ativo", justify="center")
    tbl.add_column("Tasks", justify="right")
    tbl.add_column("Path", style="dim")
    for name in boards:
        with _kb.connect(name) as conn:
            try:
                row = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()
                count = row["c"] if row else 0
            except Exception:
                count = 0
        is_active = "★" if name == active else ""
        tbl.add_row(name, is_active, str(count), str(_kb.board_path(name)))
    console.print(tbl)


@boards_app.command("create")
def boards_create_cmd(
    name: str = typer.Argument(..., help="Nome do board (alnum/dash/underscore)"),
    activate: bool = typer.Option(
        False, "--activate", "-a",
        help="Define este board como o ativo apos criar",
    ),
):
    """Cria um novo board kanban_db (DB SQLite vazia)."""
    from .. import kanban_db as _kb

    if not name.strip():
        console.print("[red]Nome do board nao pode ser vazio.[/red]")
        raise typer.Exit(code=1)

    if name in _kb.list_boards():
        console.print(f"[yellow]Board '[bold]{name}[/bold]' ja existe.[/yellow]")
        if not activate:
            raise typer.Exit(code=0)

    with _kb.connect(name) as conn:
        _kb.init_db(conn)
    console.print(f"[green]✓[/green] Board criado: [bold]{name}[/bold]")
    console.print(f"  Path: [dim]{_kb.board_path(name)}[/dim]")

    if activate:
        _kb.set_active_board(name)
        console.print(f"[green]✓[/green] Board ativo agora: [bold]{name}[/bold]")


@boards_app.command("switch")
def boards_switch_cmd(
    name: str = typer.Argument(..., help="Nome do board a ativar"),
):
    """Define o board ativo (escreve em ~/.bauer/kanban/active_board)."""
    from .. import kanban_db as _kb

    if name not in _kb.list_boards():
        console.print(f"[red]Board '[bold]{name}[/bold]' nao existe.[/red]")
        existing = ", ".join(_kb.list_boards()) or "(nenhum)"
        console.print(f"[dim]Boards disponiveis: {existing}[/dim]")
        raise typer.Exit(code=1)

    _kb.set_active_board(name)
    console.print(f"[green]✓[/green] Board ativo: [bold]{name}[/bold]")


@boards_app.command("show")
def boards_show_cmd(
    name: str = typer.Argument("", help="Nome do board (vazio = ativo)"),
):
    """Mostra estatisticas e tasks de um board."""
    from .. import kanban_db as _kb
    from rich.table import Table

    target = name or _kb.get_active_board()
    if target not in _kb.list_boards():
        console.print(f"[red]Board '[bold]{target}[/bold]' nao existe.[/red]")
        raise typer.Exit(code=1)

    with _kb.connect(target) as conn:
        tasks = _kb.list_tasks(conn)
        counts: dict[str, int] = {}
        for t in tasks:
            counts[t.status] = counts.get(t.status, 0) + 1

    console.print(f"[bold]Board:[/bold] {target}")
    console.print(f"[dim]Path:[/dim] {_kb.board_path(target)}")
    console.print()
    if not tasks:
        console.print("[dim]Sem tasks.[/dim]")
        return

    summary = Table(show_header=True, header_style="bold cyan")
    summary.add_column("Status")
    summary.add_column("Tasks", justify="right")
    for status in sorted(counts):
        summary.add_row(status, str(counts[status]))
    console.print(summary)
    console.print()

    tbl = Table(title=f"Tasks ({len(tasks)})", show_header=True,
                header_style="bold")
    tbl.add_column("ID", style="dim", no_wrap=True)
    tbl.add_column("Status", no_wrap=True)
    tbl.add_column("Prioridade", no_wrap=True)
    tbl.add_column("Titulo")
    for t in tasks[:25]:
        tbl.add_row(t.id[:12], t.status, t.priority, t.title[:60])
    console.print(tbl)
    if len(tasks) > 25:
        console.print(f"[dim]... +{len(tasks) - 25} tasks (use boards-show "
                      f"com filtros para ver tudo).[/dim]")


@boards_app.command("rm")
def boards_rm_cmd(
    name: str = typer.Argument(..., help="Nome do board a remover"),
    force: bool = typer.Option(False, "--force", "-f",
                                help="Nao pede confirmacao"),
):
    """Remove um board (apaga o arquivo SQLite). Operacao IRREVERSIVEL."""
    from .. import kanban_db as _kb

    if name not in _kb.list_boards():
        console.print(f"[yellow]Board '[bold]{name}[/bold]' nao existe.[/yellow]")
        raise typer.Exit(code=1)

    path = _kb.board_path(name)
    if not force:
        if not typer.confirm(
            f"Remover board '{name}' definitivamente? Path: {path}",
            default=False,
        ):
            console.print("[dim]Cancelado.[/dim]")
            return

    try:
        path.unlink(missing_ok=True)
        # Remove o diretorio do board se estiver vazio (mas mantem workspaces/logs).
        try:
            path.parent.rmdir()
        except OSError:
            pass
    except OSError as exc:
        console.print(f"[red]Erro removendo {path}:[/red] {exc}")
        raise typer.Exit(code=1)

    # Se era o board ativo, reseta o marcador.
    if _kb.get_active_board() == name:
        _kb.active_board_marker_path().unlink(missing_ok=True)
        console.print("[yellow]Marcador 'active_board' removido — proximo "
                      "comando usara 'default'.[/yellow]")
    console.print(f"[green]✓[/green] Board removido: [bold]{name}[/bold]")
