"""Comandos `bauer perf` — baseline de performance (Fase 12, Sprint 33).

Read-only: agrega runs + duração de tools já persistidas. Não otimiza, só mede.
  bauer perf report [--last 24h] [--format table|json] [--output f]
  bauer perf run <run_id> [--format table|json]
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
from rich.table import Table

from ._common import console

perf_app = typer.Typer(help="Baseline de performance: latência por run e gargalos.")

_DEFAULT_STATE_DIR = Path("memory/runtime")


def _parse_last(last: str) -> "datetime | None":
    # UTC-aware: os timestamps das runs são UTC; usar naive local erraria o
    # corte da janela pelo offset do fuso.
    if not last:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", last.lower())
    if not m:
        raise typer.BadParameter("Use formatos como 24h, 7d, 30m, 2w.")
    n, unit = int(m.group(1)), m.group(2)
    delta = {"m": timedelta(minutes=n), "h": timedelta(hours=n),
             "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
    return datetime.now(timezone.utc) - delta


def _emit_json(payload: dict, output: "Path | None") -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        console.print(f"[green]Salvo em[/green] {output}")
    else:
        console.file.write(text + "\n")
        console.file.flush()


def _ms(v: "float | None") -> str:
    return "-" if v is None else f"{v / 1000:.2f}s"


@perf_app.command("report")
def perf_report(
    last: str = typer.Option("", "--last", help="Janela: 24h, 7d, 30m, 2w (vazio = tudo)."),
    fmt: str = typer.Option("table", "--format", help="table | json"),
    output: Path = typer.Option(None, "--output", help="Salva JSON no arquivo."),
    state_dir: Path = typer.Option(_DEFAULT_STATE_DIR, "--state-dir"),
):
    """Relatório de performance das runs (latência + gargalo por tool)."""
    from ..core.performance import build_perf_report

    report = build_perf_report(state_dir, since=_parse_last(last), window_label=last or "all")

    if fmt == "json" or output is not None:
        _emit_json(asdict(report), output)
        return

    console.print(f"[bold]Bauer Performance Report[/bold] — janela: {report.window}\n")
    t = Table(show_header=False, box=None)
    t.add_row("Runs (terminais)", str(report.runs_total))
    t.add_row("Duração média", _ms(report.avg_wall_ms))
    t.add_row("p50 / p95", f"{_ms(report.p50_wall_ms)} / {_ms(report.p95_wall_ms)}")
    if report.tool_time_share is not None:
        tool_pct = report.tool_time_share * 100
        t.add_row("Tempo em tools", f"{tool_pct:.1f}%  ({_ms(report.total_tool_ms)})")
        t.add_row("Modelo + overhead", f"{100 - tool_pct:.1f}%  (não instrumentado por fase)")
    console.print(t)

    if report.slowest:
        console.print("\n[bold]Runs mais lentas[/bold]")
        for run_id, wall in report.slowest:
            console.print(f"  {_ms(wall):>8}  {run_id}")
    if report.top_tools:
        console.print("\n[bold]Tools que mais consomem tempo[/bold]")
        for tt_ in report.top_tools:
            console.print(f"  {_ms(tt_.total_ms):>8}  {tt_.tool}  [dim]({tt_.calls}x)[/dim]")
    console.print(
        f"\n[dim]Fases ainda não medidas por instrumentação: "
        f"{', '.join(report.unmeasured_phases)}. "
        f"'Modelo + overhead' é o wall-clock fora das tools.[/dim]"
    )


@perf_app.command("run")
def perf_run(
    run_id: str = typer.Argument(...),
    fmt: str = typer.Option("table", "--format", help="table | json"),
    state_dir: Path = typer.Option(_DEFAULT_STATE_DIR, "--state-dir"),
):
    """Breakdown de performance de uma run específica."""
    from ..core.performance import run_perf

    perf = run_perf(state_dir, run_id)
    if perf is None:
        console.print(f"[red]Run não encontrada:[/red] {run_id}")
        raise typer.Exit(code=1)

    if fmt == "json":
        _emit_json(asdict(perf), None)
        return

    console.print(f"[bold]Perf da run[/bold] {perf.run_id}  [dim]({perf.status})[/dim]")
    console.print(f"  wall-clock:   {_ms(perf.wall_ms)}")
    console.print(f"  em tools:     {_ms(perf.tool_ms)}  [dim]({perf.tool_calls} calls)[/dim]")
    console.print(f"  modelo+overhead: {_ms(perf.non_tool_ms)}  [dim](proxy — não instrumentado)[/dim]")
    if perf.tools:
        console.print("\n[bold]Por tool[/bold]")
        for t_ in perf.tools:
            console.print(f"  {_ms(t_.total_ms):>8}  {t_.tool}  [dim]({t_.calls}x)[/dim]")
