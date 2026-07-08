"""Comando  — cost tracker (custo USD e uso de tokens por sessao)."""

from __future__ import annotations

import typer

from ._common import console

cost_app = typer.Typer(help="Cost tracker — custo USD e uso de tokens")


@cost_app.command("status")
def cost_status(
    session_id: str = typer.Option("", "--session", help="Session ID específica"),
):
    """Mostra custo e uso de tokens da sessão."""
    from ..cost_tracker import CostTracker

    history = CostTracker.load_history(limit=200)
    if not history:
        console.print("[dim]Nenhum registro de custo encontrado.[/dim]")
        return

    # Agrupa por sessão
    sessions = {}
    for rec in history:
        sid = rec.get("session_id", "")
        if session_id and sid != session_id:
            continue
        if sid not in sessions:
            sessions[sid] = {"calls": 0, "tokens": 0, "cost": 0.0, "models": set()}
        sessions[sid]["calls"] += 1
        sessions[sid]["tokens"] += rec.get("total_tokens", 0)
        sessions[sid]["cost"] += rec.get("cost_usd", 0.0)
        sessions[sid]["models"].add(rec.get("model", ""))

    if not sessions:
        console.print("[dim]Nenhum registro encontrado para essa sessão.[/dim]")
        return

    from rich.table import Table
    tbl = Table(title="Custo por Sessão", show_header=True)
    tbl.add_column("Session", style="dim", width=16)
    tbl.add_column("Calls", style="cyan")
    tbl.add_column("Tokens", style="magenta")
    tbl.add_column("Custo USD", style="yellow")
    tbl.add_column("Modelos", style="dim", max_width=30)

    for sid, data in list(sessions.items())[-20:]:
        tbl.add_row(
            sid[:16],
            str(data["calls"]),
            f"{data['tokens']:,}",
            f"${data['cost']:.4f}",
            ", ".join(list(data["models"])[:3]),
        )
    console.print(tbl)


@cost_app.command("history")
def cost_history(
    limit: int = typer.Option(30, "--limit", "-n"),
    output_json: bool = typer.Option(False, "--json"),
):
    """Lista histórico detalhado de chamadas LLM com custo."""
    from ..cost_tracker import CostTracker
    import datetime

    history = CostTracker.load_history(limit=limit)
    if not history:
        console.print("[dim]Nenhum histórico de custo.[/dim]")
        return

    if output_json:
        import json as _json
        console.print(_json.dumps(history, indent=2))
        return

    from rich.table import Table
    tbl = Table(title="Histórico de Custo", show_header=True)
    tbl.add_column("Hora", style="dim")
    tbl.add_column("Modelo", style="cyan")
    tbl.add_column("Prompt", style="magenta")
    tbl.add_column("Compl.", style="blue")
    tbl.add_column("Custo", style="yellow")
    tbl.add_column("Session", style="dim", width=12)

    for rec in history[-limit:]:
        hora = datetime.datetime.fromtimestamp(rec.get("ts", 0)).strftime("%H:%M:%S")
        tbl.add_row(
            hora,
            rec.get("model", "")[:20],
            str(rec.get("prompt_tokens", 0)),
            str(rec.get("completion_tokens", 0)),
            f"${rec.get('cost_usd', 0):.5f}",
            rec.get("session_id", "")[:12],
        )
    console.print(tbl)
