"""Comando `bauer discord` — bridge do agente Bauer via Discord."""

from __future__ import annotations

import typer
from pathlib import Path

from ._common import console

discord_app = typer.Typer(help="Discord Bridge — agente Bauer via Discord")


@discord_app.command("start", help="Inicia o canal Discord (foreground)")
def discord_start(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    """Sobe só o bridge Discord. Para todos os canais use `bauer gateway start`."""
    from bauer.discord_bridge import run_bridge

    console.print("[green]Discord bridge — foreground (Ctrl+C para parar)[/green]")
    try:
        run_bridge(config)
    except RuntimeError as exc:
        console.print(f"[red]ERRO:[/red] {exc}")
        raise typer.Exit(code=1)
