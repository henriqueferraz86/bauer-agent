"""Commands for inspecting runtime sessions."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import typer
from rich.table import Table

from ..core.runtime.session_manager import SessionManager
from ._common import console

sessions_app = typer.Typer(help="Lista e inspeciona sessoes formais do runtime.")


@sessions_app.command("list")
def sessions_list(
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    manager = SessionManager(root=state_dir)
    sessions = manager.list_sessions()
    if not sessions:
        console.print("[yellow]Nenhuma sessao registrada.[/yellow]")
        return

    table = Table(title="Sessions")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("user", no_wrap=True)
    table.add_column("company", no_wrap=True)
    table.add_column("agent", no_wrap=True)
    table.add_column("updated", style="dim")
    for session in sessions:
        table.add_row(
            session.id,
            session.user_id,
            session.company_id or "",
            session.agent_id,
            session.updated_at,
        )
    console.print(table)


@sessions_app.command("show")
def sessions_show(
    session_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    session = SessionManager(root=state_dir).get_session(session_id)
    if session is None:
        console.print(f"[red]Sessao nao encontrada:[/red] {session_id}")
        raise typer.Exit(code=1)
    console.print(json.dumps(asdict(session), ensure_ascii=False, indent=2))
