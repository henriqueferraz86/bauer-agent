"""Comando do diagnóstico de maturidade."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.table import Table

from ..maturity import build_maturity_report
from ._common import console

maturity_app = typer.Typer(help="Mede a maturidade estrutural do código do Bauer.")


@maturity_app.callback(invoke_without_command=True)
def maturity(
    json_output: bool = typer.Option(False, "--json", help="Emite o relatório como JSON."),
    root: Path = typer.Option(Path("."), "--root", help="Raiz do repositório."),
) -> None:
    report = build_maturity_report(root)
    if json_output:
        console.print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return
    table = Table(title=f"Bauer Maturity — {report.score:.1f}/10")
    table.add_column("Dimensão", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Evidência")
    for item in report.dimensions:
        table.add_row(item.name, f"{item.score:.1f}", "; ".join(item.evidence))
    console.print(table)
    console.print(f"Nível: {report.level}")
    if report.score < 9.0:
        console.print("[yellow]Bloqueios:[/yellow]")
        for item in report.dimensions:
            for blocker in item.blockers:
                console.print(f"  - {blocker}")
