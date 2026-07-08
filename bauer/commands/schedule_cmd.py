"""Commands for persistent runtime schedules."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer
import yaml
from rich.table import Table

from ._common import console
from ..core.runtime.scheduler import Scheduler

schedule_app = typer.Typer(help="Tarefas agendadas persistentes do runtime.")


@schedule_app.command("add")
def schedule_add_cmd(
    file: Path | None = typer.Option(None, "--file", "-f", help="YAML com TaskDefinition."),
    task_id: str = typer.Option("", "--id", help="ID da tarefa."),
    name: str = typer.Option("", "--name", help="Nome da tarefa."),
    agent_id: str = typer.Option("default", "--agent-id"),
    runtime_adapter: str = typer.Option("agno", "--runtime-adapter"),
    cron: str = typer.Option("", "--cron", help='Expressao cron, ex: "0 9 * * *".'),
    message: str = typer.Option("", "--message", help="Mensagem enviada ao agente."),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    scheduler = Scheduler(root=state_dir)
    if file is not None:
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            console.print("[red]YAML da tarefa deve ser um objeto.[/red]")
            raise typer.Exit(code=1)
        task = scheduler.add_task(raw)
    else:
        if not task_id:
            console.print("[red]Informe --id ou use --file.[/red]")
            raise typer.Exit(code=1)
        if not cron:
            console.print("[red]Informe --cron ou use --file.[/red]")
            raise typer.Exit(code=1)
        task = scheduler.add_task(
            {
                "id": task_id,
                "name": name or task_id,
                "agent_id": agent_id,
                "runtime_adapter": runtime_adapter,
                "schedule": {"type": "cron", "expression": cron},
                "input": {"message": message},
                "policy": {},
            }
        )
    console.print(f"[green]schedule added[/green] {task.id} next={task.next_run_at}")


@schedule_app.command("list")
def schedule_list_cmd(
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    tasks = Scheduler(root=state_dir).list_tasks()
    if not tasks:
        console.print("[yellow]Nenhuma tarefa agendada.[/yellow]")
        return
    table = Table(title="Scheduled Tasks", show_lines=False)
    table.add_column("id", style="cyan")
    table.add_column("status")
    table.add_column("agent")
    table.add_column("adapter")
    table.add_column("next", style="dim")
    table.add_column("runs", justify="right")
    for task in tasks:
        table.add_row(task.id, task.status, task.agent_id, task.runtime_adapter, task.next_run_at or "-", str(task.run_count))
    console.print(table)


@schedule_app.command("show")
def schedule_show_cmd(
    task_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    task = Scheduler(root=state_dir).get_task(task_id)
    if task is None:
        console.print(f"[red]Tarefa nao encontrada:[/red] {task_id}")
        raise typer.Exit(code=1)
    console.print(json.dumps(asdict(task), ensure_ascii=False, indent=2))


@schedule_app.command("run")
def schedule_run_cmd(
    task_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    try:
        result = Scheduler(root=state_dir).run_task(task_id, manual=True)
    except KeyError:
        console.print(f"[red]Tarefa nao encontrada:[/red] {task_id}")
        raise typer.Exit(code=1)
    console.print(json.dumps(result, ensure_ascii=False, indent=2))


@schedule_app.command("pause")
def schedule_pause_cmd(
    task_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    _status_update(task_id, "pause", state_dir)


@schedule_app.command("resume")
def schedule_resume_cmd(
    task_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    _status_update(task_id, "resume", state_dir)


@schedule_app.command("delete")
def schedule_delete_cmd(
    task_id: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes", "-y"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    if not yes and not typer.confirm(f"Remover tarefa agendada '{task_id}'?", default=False):
        raise typer.Exit(code=1)
    try:
        task = Scheduler(root=state_dir).delete_task(task_id)
    except KeyError:
        console.print(f"[red]Tarefa nao encontrada:[/red] {task_id}")
        raise typer.Exit(code=1)
    console.print(f"[green]schedule deleted[/green] {task.id}")


def _status_update(task_id: str, action: str, state_dir: Path) -> None:
    scheduler = Scheduler(root=state_dir)
    try:
        task = scheduler.pause_task(task_id) if action == "pause" else scheduler.resume_task(task_id)
    except KeyError:
        console.print(f"[red]Tarefa nao encontrada:[/red] {task_id}")
        raise typer.Exit(code=1)
    console.print(f"[green]schedule {action}[/green] {task.id} status={task.status}")
