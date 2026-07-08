"""Local runtime worker commands."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from ._common import console
from ..core.runtime.resilience import WorkerRegistry
from ..core.runtime.scheduler import Scheduler

worker_app = typer.Typer(help="Worker local para executar tarefas agendadas.")


@worker_app.command("start")
def worker_start_cmd(
    worker_id: str = typer.Option("local-worker", "--worker-id"),
    interval_s: float = typer.Option(30.0, "--interval-s", min=0.1),
    once: bool = typer.Option(False, "--once", help="Executa um tick e sai."),
    max_tasks: int = typer.Option(10, "--max-tasks", min=1),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    scheduler = Scheduler(root=state_dir)
    console.print(f"[green]worker start[/green] id={worker_id} state_dir={state_dir} interval={interval_s}s")
    try:
        scheduler.start_worker(worker_id=worker_id, interval_s=interval_s, once=once, max_tasks_per_tick=max_tasks)
    except KeyboardInterrupt:
        console.print("\n[dim]worker stopped[/dim]")


@worker_app.command("status")
def worker_status_cmd(
    stale_after_s: int = typer.Option(90, "--stale-after-s", min=1),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    workers = WorkerRegistry(root=state_dir).list(stale_after_s=stale_after_s)
    if not workers:
        console.print("[yellow]Nenhum worker registrado.[/yellow]")
        return
    table = Table(title="Workers", show_lines=False)
    table.add_column("id", style="cyan")
    table.add_column("status")
    table.add_column("pid", justify="right")
    table.add_column("last_seen", style="dim")
    for worker in workers:
        table.add_row(
            worker["id"],
            worker["computed_status"],
            str(worker["pid"]),
            worker["last_seen_at"],
        )
    console.print(table)
