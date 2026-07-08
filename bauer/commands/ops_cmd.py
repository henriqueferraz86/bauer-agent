"""Comando bauer ops."""

from __future__ import annotations

from pathlib import Path
from rich.table import Table
import typer

from ._common import _PROJECT_WORKSPACE, console

ops_app = typer.Typer(help="Operacao do runtime: filas, lanes, claims e runs")


@ops_app.command("status")
def ops_status_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    limit: int = typer.Option(10, "--limit", help="Numero de runs/eventos recentes"),
    as_json: bool = typer.Option(False, "--json", help="Imprime JSON bruto para automacao"),
):
    """Mostra saude operacional: filas, lanes, claims ativos, runs e eventos."""
    import json as _json

    from ..ops_status import build_ops_status

    status = build_ops_status(workspace, limit=limit)
    if as_json:
        console.print(_json.dumps(status, ensure_ascii=False, indent=2), soft_wrap=True)
        return

    counts = status["status_counts"]
    summary = Table(title=f"Ops status - {workspace}", show_lines=False)
    summary.add_column("Status", style="cyan")
    summary.add_column("Qtd", justify="right")
    for name in ("READY", "IN_PROGRESS", "FAILED", "BLOCKED", "TODO", "DONE"):
        summary.add_row(name, str(counts.get(name, 0)))
    console.print(summary)

    lanes = status.get("lanes", [])
    if lanes:
        lane_table = Table(title="Lanes", show_lines=False)
        lane_table.add_column("Lane", style="cyan")
        lane_table.add_column("Agent")
        lane_table.add_column("Capacidade", justify="right")
        lane_table.add_column("Ready", justify="right")
        lane_table.add_column("Running", justify="right")
        lane_table.add_column("Failed", justify="right")
        lane_table.add_column("Blocked", justify="right")
        for lane in lanes:
            lane_table.add_row(
                str(lane.get("lane", "")),
                str(lane.get("agent", "")),
                str(lane.get("max_concurrent", "")),
                str(lane.get("ready", 0)),
                str(lane.get("running", 0)),
                str(lane.get("failed", 0)),
                str(lane.get("blocked", 0)),
            )
        console.print(lane_table)

    claims = status.get("active_claims", [])
    if claims:
        claim_table = Table(title="Claims ativos", show_lines=False)
        claim_table.add_column("Task", style="cyan")
        claim_table.add_column("Lane")
        claim_table.add_column("Run", style="dim")
        claim_table.add_column("PID", justify="right")
        claim_table.add_column("Alive")
        claim_table.add_column("Lease", justify="right")
        for claim in claims:
            lease = claim.get("claim_seconds_left")
            claim_table.add_row(
                str(claim.get("public_id", "")),
                str(claim.get("lane", "")),
                str(claim.get("run_id", "")),
                str(claim.get("worker_pid") or ""),
                str(claim.get("worker_alive")),
                "" if lease is None else f"{lease}s",
            )
        console.print(claim_table)
    else:
        console.print("[dim]Nenhum claim ativo.[/dim]")

    runs = status.get("recent_runs", [])
    if runs:
        run_table = Table(title="Runs recentes", show_lines=False)
        run_table.add_column("Run", style="dim")
        run_table.add_column("Task")
        run_table.add_column("Status")
        run_table.add_column("Lane")
        run_table.add_column("Heartbeat", style="dim")
        for run in runs:
            metadata = run.get("metadata", {}) or {}
            run_table.add_row(
                str(run.get("run_id", "")),
                str(run.get("task_id", "")),
                str(run.get("status", "")),
                str(metadata.get("lane", "")),
                str(run.get("heartbeat_at", "")),
            )
        console.print(run_table)

    orchestrations = status.get("recent_orchestrations", [])
    if orchestrations:
        orch_table = Table(title="Orquestracoes duraveis", show_lines=False)
        orch_table.add_column("Run", style="cyan")
        orch_table.add_column("Status")
        orch_table.add_column("Mode")
        orch_table.add_column("Steps", justify="right")
        orch_table.add_column("Objetivo")
        for run in orchestrations:
            orch_table.add_row(
                str(run.get("run_id", "")),
                str(run.get("status", "")),
                str(run.get("mode", "")),
                str(len(run.get("plan", []) or [])),
                str(run.get("objective", ""))[:70],
            )
        console.print(orch_table)

    automation_jobs = status.get("automation_jobs", [])
    if automation_jobs:
        cron_table = Table(title="Automacoes cron", show_lines=False)
        cron_table.add_column("Nome", style="cyan")
        cron_table.add_column("Status")
        cron_table.add_column("Schedule")
        cron_table.add_column("Next", style="dim")
        cron_table.add_column("Runs", justify="right")
        for job in automation_jobs:
            cron_table.add_row(
                str(job.get("name", "")),
                str(job.get("status", "")),
                str(job.get("schedule_str", "")),
                str(job.get("next_run_at", "")),
                str(job.get("run_count", 0)),
            )
        console.print(cron_table)

    events = status.get("recent_events", [])
    if events:
        event_table = Table(title="Eventos recentes", show_lines=False)
        event_table.add_column("ID", justify="right", style="dim")
        event_table.add_column("Task")
        event_table.add_column("Evento")
        event_table.add_column("Mensagem")
        for event in events:
            event_table.add_row(
                str(event.get("id", "")),
                str(event.get("task_id", "")),
                str(event.get("event_type", "")),
                str(event.get("message", ""))[:80],
            )
        console.print(event_table)


@ops_app.command("migrations")
def ops_migrations_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    apply: bool = typer.Option(True, "--apply/--list-only", help="Registra baseline se ainda nao existir"),
):
    """Mostra/aplica ledger de schema migrations dos sidecars Bauer."""
    from ..schema_migrations import MigrationLedger, ensure_level8_migrations

    records = ensure_level8_migrations(workspace) if apply else MigrationLedger(workspace).list_records()
    if not records:
        console.print("[dim]Nenhuma migration registrada.[/dim]")
        return
    table = Table(title=f"Schema migrations - {workspace}", show_lines=False)
    table.add_column("Store", style="cyan")
    table.add_column("Version", justify="right")
    table.add_column("Name")
    table.add_column("Applied", style="dim")
    for record in records:
        table.add_row(record.store, str(record.version), record.name, record.applied_at)
    console.print(table)


@ops_app.command("watch")
def ops_watch_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    interval: float = typer.Option(2.0, "--interval"),
    iterations: int = typer.Option(0, "--iterations", help="0 = infinito"),
):
    """TUI simples de observabilidade operacional com auto-refresh."""
    import time as _time

    from rich.live import Live
    from rich.panel import Panel as _Panel

    from ..ops_status import build_ops_status

    def _render():
        status = build_ops_status(workspace, limit=5)
        counts = status["status_counts"]
        lines = [
            f"Workspace: {status['workspace']}",
            f"READY={counts.get('READY', 0)} IN_PROGRESS={counts.get('IN_PROGRESS', 0)} "
            f"FAILED={counts.get('FAILED', 0)} BLOCKED={counts.get('BLOCKED', 0)}",
            f"Claims ativos: {len(status.get('active_claims', []))}",
            f"Automacoes: {len(status.get('automation_jobs', []))} | "
            f"Outbox: {len(status.get('gateway_outbox', []))} | "
            f"Orquestracoes: {len(status.get('recent_orchestrations', []))}",
            f"Atualizado: {status['generated_at']}",
        ]
        return _Panel("\n".join(lines), title="Bauer Ops Watch", border_style="cyan")

    count = 0
    with Live(_render(), refresh_per_second=1, console=console) as live:
        while True:
            _time.sleep(max(0.5, float(interval)))
            live.update(_render())
            count += 1
            if iterations and count >= iterations:
                break
