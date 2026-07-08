"""Comando bauer research."""

from __future__ import annotations

from pathlib import Path
from rich.table import Table
import typer

from ._common import _PROJECT_WORKSPACE, console

research_app = typer.Typer(help="Pesquisa e trajectories para avaliacao/treino")


@research_app.command("trajectory-add")
def research_trajectory_add_cmd(
    objective: str = typer.Argument(..., help="Objetivo ou tarefa da trajetória"),
    kind: str = typer.Option("manual", "--kind"),
    input_json: str = typer.Option("{}", "--input-json"),
    output_json: str = typer.Option("{}", "--output-json"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Registra uma trajetória JSONL append-only para avaliação/treino."""
    import json as _json

    from ..trajectory_store import TrajectoryStore

    try:
        input_data = _json.loads(input_json)
        output_data = _json.loads(output_json)
        if not isinstance(input_data, dict) or not isinstance(output_data, dict):
            raise ValueError("input-json/output-json devem ser objetos JSON")
    except Exception as exc:
        console.print(f"[red]JSON invalido:[/red] {exc}")
        raise typer.Exit(code=2)
    record = TrajectoryStore(workspace).append(
        kind=kind,
        objective=objective,
        input=input_data,
        output=output_data,
    )
    console.print(f"[green]trajectory registrada[/green] {record.trajectory_id}")


@research_app.command("trajectory-list")
def research_trajectory_list_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    limit: int = typer.Option(20, "--limit"),
    kind: str = typer.Option("", "--kind"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista trajetórias recentes."""
    import json as _json
    from dataclasses import asdict

    from ..trajectory_store import TrajectoryStore

    records = TrajectoryStore(workspace).list(limit=limit, kind=kind)
    if as_json:
        console.print(_json.dumps([asdict(record) for record in records], ensure_ascii=False, indent=2))
        return
    if not records:
        console.print("[dim]Nenhuma trajectory registrada.[/dim]")
        return
    table = Table(title=f"Trajectories - {workspace}", show_lines=False)
    table.add_column("ID", style="cyan")
    table.add_column("Kind")
    table.add_column("Objetivo")
    table.add_column("Criado", style="dim")
    for record in records:
        table.add_row(record.trajectory_id, record.kind, record.objective[:80], record.created_at)
    console.print(table)
