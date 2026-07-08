"""Commands for inspecting runtime runs."""

from __future__ import annotations

import json
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
    run_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    events = EventBus(root=state_dir).list_events(run_id=run_id)
    if not events:
        console.print(f"[yellow]Nenhum evento para run:[/yellow] {run_id}")
        return
    for event in events:
        console.print(json.dumps(asdict(event), ensure_ascii=False))
