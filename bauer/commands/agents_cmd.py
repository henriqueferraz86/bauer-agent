"""CLI for persistent runtime subagents."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.table import Table

from ._common import console

agents_app = typer.Typer(help="Subagentes persistentes do runtime")


def _manager(state_dir: Path):
    from ..core.runtime import AgentManager

    return AgentManager(root=state_dir)


@agents_app.command("running")
def agents_running_cmd(
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    """Lista agentes queued, running e paused."""
    table = Table(title="Bauer Agents", show_lines=False)
    table.add_column("Process", style="cyan", no_wrap=True)
    table.add_column("Agent")
    table.add_column("Status")
    table.add_column("Parent")
    for process in _manager(state_dir).supervisor.running():
        table.add_row(
            process.id,
            process.agent_id,
            process.status,
            process.parent_agent_id or "-",
        )
    console.print(table)


@agents_app.command("tree")
def agents_tree_cmd(
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    """Mostra a árvore pai/filho dos agentes persistidos."""
    console.print(json.dumps(_manager(state_dir).supervisor.tree(), ensure_ascii=False, indent=2))


@agents_app.command("inspect")
def agents_inspect_cmd(
    process_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    """Inspeciona estado, sessão, budget e permissões de um processo."""
    process = _manager(state_dir).inspect_agent(process_id)
    if process is None:
        console.print(f"[red]Agent process nao encontrado:[/red] {process_id}")
        raise typer.Exit(code=1)
    console.print(json.dumps(process.__dict__, ensure_ascii=False, indent=2))


@agents_app.command("kill")
def agents_kill_cmd(
    process_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    """Cancela um subagente persistido."""
    try:
        process = _manager(state_dir).cancel_agent(process_id)
    except KeyError as exc:
        console.print(f"[red]Agent process nao encontrado:[/red] {process_id}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]{process.status}[/green] {process.id}")

