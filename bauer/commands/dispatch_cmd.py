"""Comando bauer dispatch."""

from __future__ import annotations

from pathlib import Path
from rich.table import Table
from ..workspace_manager_factory import get_workspace_manager
import typer

from ._common import _PROJECT_WORKSPACE, console

dispatch_app = typer.Typer(help="Dispatcher hibrido durable para tasks READY")


@dispatch_app.command("once")
def dispatch_once_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    max_spawn: int = typer.Option(1, "--max-spawn", help="Maximo de novas tasks neste tick"),
    max_in_progress: int = typer.Option(1, "--max-in-progress", help="Limite global de tasks em execucao"),
    claim_ttl_seconds: int = typer.Option(900, "--claim-ttl-seconds", help="TTL do claim"),
    stale_seconds: int = typer.Option(1800, "--stale-seconds", help="Sem heartbeat por este tempo vira stale"),
    max_retries: int = typer.Option(2, "--max-retries", help="Tentativas antes de FAILED"),
    foreground: bool = typer.Option(False, "--foreground", help="Executa worker neste processo e aguarda fim"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra o que seria claimed sem alterar"),
):
    """Executa um tick do dispatcher hibrido."""
    from ..task_dispatcher import TaskDispatcher

    dispatcher = TaskDispatcher(
        workspace,
        claim_ttl_seconds=claim_ttl_seconds,
        stale_seconds=stale_seconds,
        max_retries=max_retries,
    )
    result = dispatcher.dispatch_once(
        dry_run=dry_run,
        max_spawn=max_spawn,
        max_in_progress=max_in_progress,
        spawn_background=not foreground,
        config=config,
        models=models,
    )
    console.print(
        "[bold]dispatch once[/bold] "
        f"crashed={len(result.crashed)} reclaimed={len(result.reclaimed)} claimed={len(result.claimed)} "
        f"spawned={len(result.spawned)} completed={len(result.completed)} "
        f"failed={len(result.failed)} dry={len(result.dry_run)}"
    )
    for label, items in (
        ("crashed", result.crashed),
        ("reclaimed", result.reclaimed),
        ("claimed", result.claimed),
        ("spawned", result.spawned),
        ("completed", result.completed),
        ("failed", result.failed),
        ("dry", result.dry_run),
        ("skipped", result.skipped),
    ):
        if items:
            console.print(f"[dim]{label}:[/dim] {', '.join(items)}")


@dispatch_app.command("status")
def dispatch_status_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    limit: int = typer.Option(10, "--limit", help="Numero de runs/eventos recentes"),
):
    """Mostra fila, runs e eventos recentes do dispatcher."""
    from collections import Counter

    from ..kanban_store import KanbanStore

    wm = get_workspace_manager(workspace)
    store = KanbanStore(workspace)
    tasks = wm.list_tasks()
    counts = Counter(t.status for t in tasks)

    table = Table(title=f"Dispatcher status - {workspace}", show_lines=False)
    table.add_column("Status", style="cyan")
    table.add_column("Qtd", justify="right")
    for status in ("READY", "IN_PROGRESS", "FAILED", "DONE", "BLOCKED", "TODO"):
        table.add_row(status, str(counts.get(status, 0)))
    console.print(table)

    runs = store.list_runs(limit=limit)
    if runs:
        run_table = Table(title="Runs recentes", show_lines=False)
        run_table.add_column("Run", style="dim")
        run_table.add_column("Task")
        run_table.add_column("Status")
        run_table.add_column("Tent.", justify="right")
        run_table.add_column("Worker")
        run_table.add_column("Heartbeat", style="dim")
        for run in runs:
            run_table.add_row(
                run.run_id,
                run.task_id,
                run.status,
                str(run.attempt),
                str(run.worker_pid or run.runner or ""),
                run.heartbeat_at,
            )
        console.print(run_table)
    else:
        console.print("[dim]Nenhum task_run registrado ainda.[/dim]")

    events = store.list_events(limit=limit)
    if events:
        event_table = Table(title="Eventos recentes", show_lines=False)
        event_table.add_column("ID", justify="right", style="dim")
        event_table.add_column("Task")
        event_table.add_column("Evento")
        event_table.add_column("Ator")
        event_table.add_column("Mensagem")
        for event in events:
            event_table.add_row(
                str(event.id),
                event.task_id,
                event.event_type,
                event.actor,
                event.message[:80],
            )
        console.print(event_table)


@dispatch_app.command("reclaim")
def dispatch_reclaim_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    claim_ttl_seconds: int = typer.Option(900, "--claim-ttl-seconds"),
    stale_seconds: int = typer.Option(1800, "--stale-seconds"),
):
    """Detecta workers mortos e devolve claims stale para READY."""
    from ..task_dispatcher import TaskDispatcher

    dispatcher = TaskDispatcher(
        workspace,
        claim_ttl_seconds=claim_ttl_seconds,
        stale_seconds=stale_seconds,
    )
    crashed = dispatcher.detect_crashed_workers()
    reclaimed = dispatcher.reclaim_stale()
    console.print(
        f"[bold]dispatch reclaim[/bold] crashed={len(crashed)} reclaimed={len(reclaimed)}"
    )
    if crashed:
        console.print(f"[dim]crashed:[/dim] {', '.join(crashed)}")
    if reclaimed:
        console.print(f"[dim]reclaimed:[/dim] {', '.join(reclaimed)}")


@dispatch_app.command("cancel")
def dispatch_cancel_cmd(
    task_id: str = typer.Argument(..., help="ID da task"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    reason: str = typer.Option("cancelled by operator", "--reason", "-r"),
    terminate_worker: bool = typer.Option(False, "--terminate-worker", help="Tenta encerrar PID do worker"),
):
    """Cancela uma task em execucao e fecha o run como cancelled."""
    from ..task_dispatcher import TaskDispatcher

    task = TaskDispatcher(workspace).cancel_task(
        task_id,
        reason=reason,
        terminate_worker=terminate_worker,
    )
    console.print(f"[yellow]{task.id}[/yellow] -> [BLOCKED] {task.title}")


@dispatch_app.command("retry")
def dispatch_retry_cmd(
    task_id: str = typer.Argument(..., help="ID da task"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    reason: str = typer.Option("manual retry", "--reason", "-r"),
):
    """Retorna uma task FAILED/BLOCKED para READY."""
    from ..task_dispatcher import TaskDispatcher

    task = TaskDispatcher(workspace).retry_failed(task_id, reason=reason)
    console.print(f"[cyan]{task.id}[/cyan] -> [READY] {task.title}")


@dispatch_app.command("daemon")
def dispatch_daemon_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    interval: int = typer.Option(30, "--interval", help="Segundos entre ticks"),
    max_spawn: int = typer.Option(1, "--max-spawn"),
    max_in_progress: int = typer.Option(1, "--max-in-progress"),
    claim_ttl_seconds: int = typer.Option(900, "--claim-ttl-seconds"),
    stale_seconds: int = typer.Option(1800, "--stale-seconds"),
    max_retries: int = typer.Option(2, "--max-retries"),
):
    """Loop duravel do dispatcher. Ctrl+C para parar."""
    import time as _time
    from ..task_dispatcher import TaskDispatcher

    dispatcher = TaskDispatcher(
        workspace,
        claim_ttl_seconds=claim_ttl_seconds,
        stale_seconds=stale_seconds,
        max_retries=max_retries,
    )
    console.print(f"[green]Dispatcher iniciado[/green] workspace={workspace} interval={interval}s")
    dispatcher.record_daemon_started(
        interval=interval,
        max_spawn=max_spawn,
        max_in_progress=max_in_progress,
    )
    try:
        while True:
            result = dispatcher.watchdog_tick(
                max_spawn=max_spawn,
                max_in_progress=max_in_progress,
                config=config,
                models=models,
            )
            if result.crashed or result.reclaimed or result.claimed or result.spawned or result.failed:
                console.print(
                    f"[dim]tick[/dim] crashed={len(result.crashed)} reclaimed={len(result.reclaimed)} "
                    f"claimed={len(result.claimed)} spawned={len(result.spawned)} "
                    f"failed={len(result.failed)}"
                )
            _time.sleep(max(1, interval))
    except KeyboardInterrupt:
        dispatcher.record_daemon_stopped(reason="KeyboardInterrupt")
        console.print("\n[dim]Dispatcher encerrado.[/dim]")


@dispatch_app.command("worker")
def dispatch_worker_cmd(
    task_id: str = typer.Argument(..., help="ID da task"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    claim_id: str = typer.Option("", "--claim-id", help="Claim esperado"),
    claim_ttl_seconds: int = typer.Option(900, "--claim-ttl-seconds"),
    stale_seconds: int = typer.Option(1800, "--stale-seconds"),
):
    """Worker interno do dispatcher. Normalmente chamado por dispatch once/daemon."""
    from ..task_dispatcher import TaskDispatcher, TaskDispatcherError

    dispatcher = TaskDispatcher(
        workspace,
        claim_ttl_seconds=claim_ttl_seconds,
        stale_seconds=stale_seconds,
    )
    try:
        result = dispatcher.run_claimed_worker(
            task_id,
            claim_id=claim_id,
            config=config,
            models=models,
        )
    except TaskDispatcherError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if result.success:
        console.print(f"[green]Task {task_id} concluida.[/green]")
    else:
        console.print(f"[red]Task {task_id} falhou:[/red] {result.error}")
        raise typer.Exit(code=1)
