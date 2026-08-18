"""Commands for inspecting runtime runs."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

import typer
from rich.table import Table

from ..core.runtime.run_manager import RunManager
from ..core.events import EventBus
from ._common import console

runs_app = typer.Typer(help="Lista, inspeciona e cancela execucoes do runtime.")


@runs_app.command("list")
def runs_list(
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    manager = RunManager(root=state_dir)
    runs = manager.list_runs()
    if not runs:
        console.print("[yellow]Nenhuma run registrada.[/yellow]")
        return

    table = Table(title="Runs")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("session", style="dim", no_wrap=True)
    table.add_column("agent", no_wrap=True)
    table.add_column("adapter", no_wrap=True)
    table.add_column("tools", justify="right")
    table.add_column("started", style="dim")
    for run in runs:
        table.add_row(
            run.id,
            run.status,
            run.session_id,
            run.agent_id,
            run.runtime_adapter,
            str(run.tool_calls_count),
            run.started_at,
        )
    console.print(table)


@runs_app.command("show")
def runs_show(
    run_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    run = RunManager(root=state_dir).get_run(run_id)
    if run is None:
        console.print(f"[red]Run nao encontrada:[/red] {run_id}")
        raise typer.Exit(code=1)
    console.print(json.dumps(asdict(run), ensure_ascii=False, indent=2))


@runs_app.command("cancel")
def runs_cancel(
    run_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    manager = RunManager(root=state_dir)
    try:
        run = manager.cancel_run(run_id)
    except KeyError:
        console.print(f"[red]Run nao encontrada:[/red] {run_id}")
        raise typer.Exit(code=1)
    console.print(f"[green]Run[/green] {run.id} -> [bold]{run.status}[/bold]")


@runs_app.command("events")
def runs_events(
    run_id: str = typer.Argument(None, help="Filtra por run especifica; omitido = todas as runs."),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
    limit: int = typer.Option(50, "--limit", "-n", min=1, help="Maximo de eventos (ignorado apos a 1a leitura com --follow)."),
    follow: bool = typer.Option(False, "--follow", "-f", help="Continua exibindo novos eventos."),
    interval: float = typer.Option(1.0, "--interval", min=0.1, help="Intervalo de poll em segundos com --follow."),
):
    bus = EventBus(root=state_dir)
    seen: set[str] = set()

    def _print_new() -> None:
        events = bus.list_events(run_id=run_id, limit=limit if not seen else None)
        if not events and not seen and not follow:
            console.print(
                f"[yellow]Nenhum evento{f' para run: {run_id}' if run_id else ''}.[/yellow]"
            )
            return
        for event in events:
            if event.id in seen:
                continue
            seen.add(event.id)
            console.print(json.dumps(asdict(event), ensure_ascii=False))

    _print_new()
    while follow:
        time.sleep(interval)
        _print_new()
