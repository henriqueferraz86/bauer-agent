"""Comando bauer cron."""

from __future__ import annotations

from pathlib import Path
from rich.table import Table
import typer

from ._common import _PROJECT_WORKSPACE, console

cron_app = typer.Typer(help="Automacoes duraveis: agenda prompts como tasks READY")


@cron_app.command("create")
def cron_create_cmd(
    name: str = typer.Argument(..., help="Nome unico do job"),
    prompt: str = typer.Argument(..., help="Prompt a executar quando o job vencer"),
    schedule: str = typer.Option(..., "--schedule", "-s", help="every 30m | every 2h | daily 09:00 | at ISO | cron: */15 * * * *"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    assignee: str = typer.Option("", "--assignee", help="Agent/lane preferido para a task"),
    priority: str = typer.Option("medium", "--priority"),
    max_retries: int = typer.Option(2, "--max-retries"),
    max_runtime_seconds: int = typer.Option(0, "--max-runtime-seconds"),
    deliver: str = typer.Option("", "--deliver", help="Entrega outbox: channel:<name> ou plataforma:<target>"),
):
    """Cria uma automacao duravel que enfileira tasks no dispatcher."""
    from ..automation_store import AutomationStore

    metadata = {
        "assignee": assignee,
        "priority": priority,
        "max_retries": max(1, int(max_retries)),
    }
    if max_runtime_seconds > 0:
        metadata["max_runtime_seconds"] = int(max_runtime_seconds)
    if deliver:
        metadata["delivery"] = deliver
    try:
        job = AutomationStore(workspace).create_job(
            name=name,
            prompt=prompt,
            schedule=schedule,
            metadata=metadata,
        )
    except Exception as exc:
        console.print(f"[red]Erro criando cron job:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]cron criado[/green] [cyan]{job.name}[/cyan] "
        f"schedule={job.schedule_str} next={job.next_run_at}"
    )


@cron_app.command("list")
def cron_list_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    limit: int = typer.Option(50, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista automacoes configuradas."""
    import json as _json
    from dataclasses import asdict

    from ..automation_store import AutomationStore

    jobs = AutomationStore(workspace).list_jobs(limit=limit)
    if as_json:
        console.print(_json.dumps([asdict(job) for job in jobs], ensure_ascii=False, indent=2))
        return
    if not jobs:
        console.print("[dim]Nenhuma automacao cron configurada.[/dim]")
        return
    table = Table(title=f"Cron jobs - {workspace}", show_lines=False)
    table.add_column("Nome", style="cyan")
    table.add_column("Status")
    table.add_column("Schedule")
    table.add_column("Next", style="dim")
    table.add_column("Runs", justify="right")
    for job in jobs:
        table.add_row(job.name, job.status, job.schedule_str, job.next_run_at, str(job.run_count))
    console.print(table)


@cron_app.command("tick")
def cron_tick_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    max_jobs: int = typer.Option(10, "--max-jobs"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Executa um tick do scheduler: jobs vencidos viram tasks READY."""
    from ..automation_scheduler import AutomationScheduler

    result = AutomationScheduler(workspace).tick(max_jobs=max_jobs, dry_run=dry_run)
    console.print(
        "[bold]cron tick[/bold] "
        f"due={len(result.due)} queued={len(result.queued)} "
        f"skipped={len(result.skipped)} failed={len(result.failed)} dry={result.dry_run}"
    )
    for label, items in (
        ("due", result.due),
        ("queued", result.queued),
        ("skipped", result.skipped),
        ("failed", result.failed),
    ):
        if items:
            console.print(f"[dim]{label}:[/dim] {', '.join(items)}")


@cron_app.command("run")
def cron_run_cmd(
    name: str = typer.Argument(..., help="Nome ou job_id"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Enfileira uma automacao imediatamente, independente do next_run_at."""
    from ..automation_scheduler import AutomationScheduler

    try:
        run = AutomationScheduler(workspace).run_now(name, dry_run=dry_run)
    except Exception as exc:
        console.print(f"[red]Erro executando cron job:[/red] {exc}")
        raise typer.Exit(code=1)
    if dry_run:
        console.print(f"[yellow]dry-run[/yellow] cron run {name}")
        return
    console.print(f"[green]cron queued[/green] run={run.run_id} task={run.task_id}")  # type: ignore[union-attr]


@cron_app.command("pause")
def cron_pause_cmd(
    name: str = typer.Argument(..., help="Nome ou job_id"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Pausa uma automacao."""
    from ..automation_store import AutomationStore

    job = AutomationStore(workspace).update_job(name, status="paused")
    if job is None:
        console.print(f"[red]Cron job nao encontrado:[/red] {name}")
        raise typer.Exit(code=1)
    console.print(f"[yellow]cron paused[/yellow] {job.name}")


@cron_app.command("resume")
def cron_resume_cmd(
    name: str = typer.Argument(..., help="Nome ou job_id"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Reativa uma automacao pausada/completed, recalculando next_run_at se necessario."""
    from ..automation_store import AutomationStore, next_run_after

    store = AutomationStore(workspace)
    job = store.get_job(name)
    if job is None:
        console.print(f"[red]Cron job nao encontrado:[/red] {name}")
        raise typer.Exit(code=1)
    next_run = job.next_run_at or next_run_after(job.schedule)
    updated = store.update_job(job.job_id, status="active", next_run_at=next_run)
    console.print(f"[green]cron active[/green] {updated.name} next={updated.next_run_at}")  # type: ignore[union-attr]


@cron_app.command("delete")
def cron_delete_cmd(
    name: str = typer.Argument(..., help="Nome ou job_id"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Remove uma automacao."""
    from ..automation_store import AutomationStore

    if not yes and not typer.confirm(f"Remover cron job '{name}'?", default=False):
        console.print("[dim]Cancelado.[/dim]")
        return
    if not AutomationStore(workspace).delete_job(name):
        console.print(f"[red]Cron job nao encontrado:[/red] {name}")
        raise typer.Exit(code=1)
    console.print(f"[green]cron deleted[/green] {name}")


@cron_app.command("daemon")
def cron_daemon_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    interval: int = typer.Option(60, "--interval", help="Segundos entre ticks"),
    max_jobs: int = typer.Option(10, "--max-jobs"),
):
    """Loop simples do scheduler. Use junto com dispatch daemon para executar."""
    import time as _time

    from ..automation_scheduler import AutomationScheduler

    scheduler = AutomationScheduler(workspace)
    console.print(f"[green]Cron scheduler iniciado[/green] workspace={workspace} interval={interval}s")
    try:
        while True:
            result = scheduler.tick(max_jobs=max_jobs)
            if result.queued or result.failed:
                console.print(
                    f"[dim]tick[/dim] due={len(result.due)} "
                    f"queued={len(result.queued)} failed={len(result.failed)}"
                )
            _time.sleep(max(1, int(interval)))
    except KeyboardInterrupt:
        console.print("\n[dim]Cron scheduler encerrado.[/dim]")
