"""Commands for policy approval queue."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from ._common import console
from ..core.policy import ApprovalManager

approvals_app = typer.Typer(help="Gerencia aprovacoes pendentes de policy.")


@approvals_app.command("list")
def approvals_list_cmd(
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
    status: str = typer.Option("", "--status", help="pending | approved | denied"),
):
    manager = ApprovalManager(root=state_dir)
    records = manager.list(status=status or None)
    table = Table(title="Bauer Approvals", show_lines=False)
    table.add_column("ID", style="cyan")
    table.add_column("Status")
    table.add_column("Tool")
    table.add_column("Operation")
    table.add_column("Risk")
    table.add_column("Reason")
    for record in records:
        table.add_row(record.id, record.status, record.tool_name, record.operation, record.risk_level, record.reason)
    console.print(table)


@approvals_app.command("approve")
def approvals_approve_cmd(
    approval_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    manager = ApprovalManager(root=state_dir)
    try:
        record = manager.approve(approval_id)
    except KeyError:
        console.print(f"[red]Approval nao encontrado:[/red] {approval_id}")
        raise typer.Exit(code=1)
    console.print(f"[green]approved[/green] {record.id}")


@approvals_app.command("deny")
def approvals_deny_cmd(
    approval_id: str = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
):
    manager = ApprovalManager(root=state_dir)
    try:
        record = manager.deny(approval_id)
    except KeyError:
        console.print(f"[red]Approval nao encontrado:[/red] {approval_id}")
        raise typer.Exit(code=1)
    console.print(f"[red]denied[/red] {record.id}")
