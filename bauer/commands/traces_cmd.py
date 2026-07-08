"""Comando `bauer traces` — replay e análise de sessões (OTEL)."""

from __future__ import annotations

import typer

from ._common import console

traces_app = typer.Typer(help="Traces OTEL — replay e análise de sessões")


@traces_app.command("list")
def traces_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Número de traces"),
    session_id: str = typer.Option("", "--session", help="Filtrar por session_id"),
):
    """Lista traces OTEL gravados localmente."""
    from ..otel import list_traces, load_spans
    import datetime

    if session_id:
        spans = load_spans(session_id=session_id, limit=limit * 20)
        traces = {}
        for s in spans:
            tid = s.get("trace_id", "")
            if tid not in traces:
                traces[tid] = {"trace_id": tid, "root_name": s.get("name"), "start_ns": s.get("start_ns")}
        items = list(traces.values())
    else:
        items = list_traces(limit=limit)

    if not items:
        console.print("[dim]Nenhum trace encontrado.[/dim]")
        return

    from rich.table import Table
    tbl = Table(title="Traces OTEL", show_header=True)
    tbl.add_column("Trace ID", style="dim", width=18)
    tbl.add_column("Root Span", style="cyan")
    tbl.add_column("Session", style="magenta")
    tbl.add_column("Início", style="dim")

    for item in items[:limit]:
        ts = item.get("start_ns", 0)
        hora = datetime.datetime.fromtimestamp(ts / 1e9).strftime("%H:%M:%S") if ts else "—"
        tbl.add_row(
            str(item.get("trace_id", ""))[:16],
            str(item.get("root_name", ""))[:40],
            str(item.get("session_id") or "")[:20],
            hora,
        )
    console.print(tbl)


@traces_app.command("show")
def traces_show(
    trace_id: str = typer.Argument(..., help="Trace ID a mostrar"),
    output_json: bool = typer.Option(False, "--json", help="Emite JSON"),
):
    """Mostra todos os spans de um trace específico."""
    from ..otel import load_spans

    spans = load_spans(trace_id=trace_id, limit=500)
    if not spans:
        console.print(f"[yellow]Trace {trace_id[:16]} não encontrado.[/yellow]")
        raise typer.Exit(1)

    if output_json:
        import json as _json
        console.print(_json.dumps(spans, indent=2))
        return

    from rich.table import Table
    tbl = Table(title=f"Trace {trace_id[:16]}", show_header=True)
    tbl.add_column("Span", style="cyan", width=20)
    tbl.add_column("Kind", style="magenta")
    tbl.add_column("Duration", style="yellow")
    tbl.add_column("Status", style="green")
    tbl.add_column("Attributes", style="dim", max_width=40)

    for s in spans:
        dur = f"{s.get('duration_ms', 0):.1f}ms" if s.get("duration_ms") else "—"
        attrs = ", ".join(f"{k}={v}" for k, v in (s.get("attributes") or {}).items() if k != "service")[:40]
        status_style = "red" if s.get("status") == "error" else "green"
        tbl.add_row(
            s.get("name", "")[:20],
            s.get("kind", ""),
            dur,
            f"[{status_style}]{s.get('status', '')}[/{status_style}]",
            attrs,
        )
    console.print(tbl)
