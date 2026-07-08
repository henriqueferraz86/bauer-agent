"""Budget and autonomy commands."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.table import Table

from ._common import console
from ..core.runtime.autonomy import AUTONOMY_MODES, BudgetManager

budget_app = typer.Typer(help="Budget de autonomia do runtime.")
autonomy_app = typer.Typer(help="Modo de autonomia do runtime.")


@budget_app.command("status")
def budget_status_cmd(
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    status = BudgetManager(root=state_dir).status()
    profile = status["profile"]
    table = Table(title=f"Budget Runtime - autonomy={profile['mode']}", show_lines=False)
    table.add_column("scope", style="cyan")
    table.add_column("used", justify="right")
    table.add_column("limit", justify="right")
    table.add_column("remaining", justify="right")
    for scope in ("daily", "weekly", "monthly"):
        item = status[scope]
        table.add_row(
            scope,
            f"${item['used_usd']:.4f}",
            "-" if item["limit_usd"] is None else f"${item['limit_usd']:.4f}",
            "-" if item["remaining_usd"] is None else f"${item['remaining_usd']:.4f}",
        )
    console.print(table)


@budget_app.command("set")
def budget_set_cmd(
    period: str = typer.Argument(..., help="daily | weekly | monthly"),
    amount: float = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    key = {
        "daily": "daily_budget_usd",
        "weekly": "weekly_budget_usd",
        "monthly": "monthly_budget_usd",
    }.get(period.strip().lower())
    if key is None:
        console.print("[red]Periodo invalido:[/red] use daily, weekly ou monthly")
        raise typer.Exit(code=1)
    profile = BudgetManager(root=state_dir).set_profile(**{key: amount})
    console.print(f"[green]budget set[/green] {period}=${amount:.4f} mode={profile.mode}")


@autonomy_app.command("set")
def autonomy_set_cmd(
    mode: str = typer.Argument(..., help="manual | supervised | autonomous | locked"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    normalized = mode.strip().lower()
    if normalized not in AUTONOMY_MODES:
        console.print("[red]Modo invalido:[/red] manual, supervised, autonomous ou locked")
        raise typer.Exit(code=1)
    BudgetManager(root=state_dir).set_profile(mode=normalized)
    console.print(f"[green]autonomy[/green] mode={normalized}")
