"""Comando bauer runtime (inclui subgrupo service)."""

from __future__ import annotations

from rich.panel import Panel
from pathlib import Path
from rich.table import Table
import typer

from ._common import _PROJECT_WORKSPACE, console

runtime_app = typer.Typer(help="Supervisor always-on: dispatcher, cron, outbox e kanban")

runtime_service_app = typer.Typer(
    help="Runtime como SERVICO do sistema (systemd/Task Scheduler) — sobe no boot, reinicia em crash"
)

runtime_app.add_typer(runtime_service_app, name="service")


def _runtime_supervise_args(
    *,
    workspace: Path,
    config: Path,
    models: Path,
    dispatcher: bool,
    cron: bool,
    outbox: bool,
    kanban: bool,
    dispatch_interval: int,
    cron_interval: int,
    outbox_interval: int,
    supervisor_interval: int,
    kanban_host: str,
    kanban_port: int,
    max_spawn: int,
    max_in_progress: int,
    max_jobs: int,
    delivery_limit: int,
) -> list[str]:
    args = [
        "--workspace", str(workspace),
        "--config", str(config),
        "--models", str(models),
        "--dispatch-interval", str(dispatch_interval),
        "--cron-interval", str(cron_interval),
        "--outbox-interval", str(outbox_interval),
        "--supervisor-interval", str(supervisor_interval),
        "--kanban-host", kanban_host,
        "--kanban-port", str(kanban_port),
        "--max-spawn", str(max_spawn),
        "--max-in-progress", str(max_in_progress),
        "--max-jobs", str(max_jobs),
        "--delivery-limit", str(delivery_limit),
    ]
    args.append("--dispatcher" if dispatcher else "--no-dispatcher")
    args.append("--cron" if cron else "--no-cron")
    args.append("--outbox" if outbox else "--no-outbox")
    args.append("--kanban" if kanban else "--no-kanban")
    return args


def _runtime_specs_table(specs) -> Table:
    table = Table(title="Bauer Runtime Services", show_lines=False)
    table.add_column("Service", style="cyan")
    table.add_column("Enabled")
    table.add_column("Restart")
    table.add_column("Command")
    for spec in specs:
        table.add_row(
            spec.name,
            "yes" if spec.enabled else "no",
            "yes" if spec.restart else "no",
            " ".join(spec.command),
        )
    return table


@runtime_app.command("start")
def runtime_start_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    background: bool = typer.Option(True, "--background/--foreground", help="Roda supervisor em background ou prende este terminal"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra o que seria iniciado sem subir processos"),
    dispatcher: bool = typer.Option(True, "--dispatcher/--no-dispatcher"),
    cron: bool = typer.Option(True, "--cron/--no-cron"),
    outbox: bool = typer.Option(True, "--outbox/--no-outbox"),
    kanban: bool = typer.Option(True, "--kanban/--no-kanban"),
    dispatch_interval: int = typer.Option(30, "--dispatch-interval"),
    cron_interval: int = typer.Option(60, "--cron-interval"),
    outbox_interval: int = typer.Option(30, "--outbox-interval"),
    supervisor_interval: int = typer.Option(5, "--supervisor-interval"),
    kanban_host: str = typer.Option("127.0.0.1", "--kanban-host"),
    kanban_port: int = typer.Option(8765, "--kanban-port"),
    max_spawn: int = typer.Option(1, "--max-spawn"),
    max_in_progress: int = typer.Option(1, "--max-in-progress"),
    max_jobs: int = typer.Option(10, "--max-jobs"),
    delivery_limit: int = typer.Option(20, "--delivery-limit"),
):
    """Sobe o runtime always-on: dispatcher, cron, outbox e kanban."""
    import json as _json

    from ..supervisor import RuntimeSupervisor

    supervisor = RuntimeSupervisor(workspace, config=config, models=models)
    specs = supervisor.build_service_specs(
        dispatcher=dispatcher,
        cron=cron,
        outbox=outbox,
        kanban=kanban,
        dispatch_interval=dispatch_interval,
        cron_interval=cron_interval,
        outbox_interval=outbox_interval,
        kanban_host=kanban_host,
        kanban_port=kanban_port,
        max_spawn=max_spawn,
        max_in_progress=max_in_progress,
        max_jobs=max_jobs,
        delivery_limit=delivery_limit,
    )
    if dry_run and not background:
        console.print(_runtime_specs_table(specs))
        return
    args = _runtime_supervise_args(
        workspace=workspace,
        config=config,
        models=models,
        dispatcher=dispatcher,
        cron=cron,
        outbox=outbox,
        kanban=kanban,
        dispatch_interval=dispatch_interval,
        cron_interval=cron_interval,
        outbox_interval=outbox_interval,
        supervisor_interval=supervisor_interval,
        kanban_host=kanban_host,
        kanban_port=kanban_port,
        max_spawn=max_spawn,
        max_in_progress=max_in_progress,
        max_jobs=max_jobs,
        delivery_limit=delivery_limit,
    )
    if background:
        result = supervisor.start_background(args, dry_run=dry_run)
        if dry_run:
            console.print(_runtime_specs_table(specs))
            console.print(_json.dumps(result, ensure_ascii=False, indent=2), soft_wrap=True)
            return
        console.print(f"[green]Runtime supervisor iniciado[/green] pid={result['pid']}")
        console.print(f"[dim]Logs: {result['log_path']}[/dim]")
        return

    console.print("[green]Runtime supervisor iniciado em foreground[/green] Ctrl+C para parar.")
    supervisor.run_forever(specs, supervisor_interval=supervisor_interval)


@runtime_app.command("supervise", hidden=True)
def runtime_supervise_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    dispatcher: bool = typer.Option(True, "--dispatcher/--no-dispatcher"),
    cron: bool = typer.Option(True, "--cron/--no-cron"),
    outbox: bool = typer.Option(True, "--outbox/--no-outbox"),
    kanban: bool = typer.Option(True, "--kanban/--no-kanban"),
    dispatch_interval: int = typer.Option(30, "--dispatch-interval"),
    cron_interval: int = typer.Option(60, "--cron-interval"),
    outbox_interval: int = typer.Option(30, "--outbox-interval"),
    supervisor_interval: int = typer.Option(5, "--supervisor-interval"),
    kanban_host: str = typer.Option("127.0.0.1", "--kanban-host"),
    kanban_port: int = typer.Option(8765, "--kanban-port"),
    max_spawn: int = typer.Option(1, "--max-spawn"),
    max_in_progress: int = typer.Option(1, "--max-in-progress"),
    max_jobs: int = typer.Option(10, "--max-jobs"),
    delivery_limit: int = typer.Option(20, "--delivery-limit"),
):
    """Processo interno que supervisiona os servicos do runtime."""
    from ..supervisor import RuntimeSupervisor

    supervisor = RuntimeSupervisor(workspace, config=config, models=models)
    specs = supervisor.build_service_specs(
        dispatcher=dispatcher,
        cron=cron,
        outbox=outbox,
        kanban=kanban,
        dispatch_interval=dispatch_interval,
        cron_interval=cron_interval,
        outbox_interval=outbox_interval,
        kanban_host=kanban_host,
        kanban_port=kanban_port,
        max_spawn=max_spawn,
        max_in_progress=max_in_progress,
        max_jobs=max_jobs,
        delivery_limit=delivery_limit,
    )
    supervisor.run_forever(specs, supervisor_interval=supervisor_interval)


@runtime_app.command("status")
def runtime_status_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Mostra estado do supervisor e dos servicos supervisionados."""
    import json as _json

    from ..supervisor import RuntimeSupervisor

    status = RuntimeSupervisor(workspace).status().to_public_dict()
    if as_json:
        console.print(_json.dumps(status, ensure_ascii=False, indent=2), soft_wrap=True)
        return
    table = Table(title=f"Bauer Runtime - {workspace}", show_lines=False)
    table.add_column("Service", style="cyan")
    table.add_column("State")
    table.add_column("PID", justify="right")
    table.add_column("Alive")
    table.add_column("Restarts", justify="right")
    table.add_column("Log")
    console.print(
        f"[bold]Supervisor:[/bold] state={status['state']} "
        f"pid={status.get('supervisor_pid') or '-'} alive={status.get('supervisor_alive')}"
    )
    for service in status.get("services", []):
        table.add_row(
            str(service.get("name", "")),
            str(service.get("state", "")),
            str(service.get("pid") or "-"),
            "yes" if service.get("alive") else "no",
            str(service.get("restarts", 0)),
            str(service.get("log_path", "")),
        )
    console.print(table)


@runtime_app.command("stop")
def runtime_stop_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    no_terminate: bool = typer.Option(False, "--no-terminate", help="Apenas escreve STOP; nao envia SIGTERM"),
):
    """Solicita parada do supervisor e encerra servicos supervisionados."""
    from ..supervisor import RuntimeSupervisor

    status = RuntimeSupervisor(workspace).request_stop(terminate=not no_terminate)
    console.print(f"[green]Stop solicitado[/green] state={status.get('state')} workspace={workspace}")


@runtime_app.command("restart")
def runtime_restart_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    dispatcher: bool = typer.Option(True, "--dispatcher/--no-dispatcher"),
    cron: bool = typer.Option(True, "--cron/--no-cron"),
    outbox: bool = typer.Option(True, "--outbox/--no-outbox"),
    kanban: bool = typer.Option(True, "--kanban/--no-kanban"),
):
    """Para o runtime atual e sobe um novo supervisor em background."""
    from ..supervisor import RuntimeSupervisor

    supervisor = RuntimeSupervisor(workspace, config=config, models=models)
    if not dry_run:
        supervisor.request_stop(terminate=True)
    args = _runtime_supervise_args(
        workspace=workspace,
        config=config,
        models=models,
        dispatcher=dispatcher,
        cron=cron,
        outbox=outbox,
        kanban=kanban,
        dispatch_interval=30,
        cron_interval=60,
        outbox_interval=30,
        supervisor_interval=5,
        kanban_host="127.0.0.1",
        kanban_port=8765,
        max_spawn=1,
        max_in_progress=1,
        max_jobs=10,
        delivery_limit=20,
    )
    result = supervisor.start_background(args, dry_run=dry_run)
    console.print(f"[green]Runtime restart solicitado[/green] pid={result.get('pid') or '-'}")


@runtime_app.command("logs")
def runtime_logs_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    service: str = typer.Option("supervisor", "--service", "-s"),
    lines: int = typer.Option(80, "--lines", "-n"),
):
    """Mostra as ultimas linhas de log do supervisor ou de um servico."""
    from ..supervisor import RuntimeSupervisor, tail_log

    supervisor = RuntimeSupervisor(workspace)
    status = supervisor.status().to_public_dict()
    if service == "supervisor":
        log_path = Path(status["runtime_dir"]) / "logs" / "supervisor.log"
    else:
        matches = [svc for svc in status.get("services", []) if svc.get("name") == service]
        if not matches:
            console.print(f"[red]Servico nao encontrado:[/red] {service}")
            raise typer.Exit(code=1)
        log_path = Path(str(matches[0].get("log_path", "")))
    for line in tail_log(log_path, lines=lines):
        console.print(line)


def _runtime_service_cfg(workspace: "Path | None" = None) -> "ProcessServiceConfig":  # noqa: F821
    from bauer.process_service import ProcessServiceConfig, pid_reader_from_supervisor_json
    ws = workspace or _PROJECT_WORKSPACE

    def _ws_fn(project_dir: "Path") -> "Path":
        p = Path(ws)
        return p if p.is_absolute() else project_dir / p

    entry_args = ["runtime", "supervise", "--workspace", str(ws)]
    return ProcessServiceConfig(
        service_name="bauer-runtime",
        task_name="BauerRuntime",
        description="Bauer Runtime — supervisor always-on (dispatcher, cron, outbox)",
        entry_args=entry_args,
        log_file=Path(ws) / ".bauer_runtime" / "logs" / "supervisor.log",
        pid_reader=pid_reader_from_supervisor_json(_ws_fn),
        cmdline_keyword="supervise",
    )


def _runtime_svc_manager(workspace: "Path | None" = None) -> "ProcessServiceManager":  # noqa: F821
    from bauer.process_service import ProcessServiceManager
    from ..paths import get_bauer_home
    return ProcessServiceManager(_runtime_service_cfg(workspace), project_dir=get_bauer_home())


@runtime_service_app.command("install", help="Instala E inicia o runtime como serviço do sistema")
def runtime_service_install(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    try:
        msg = _runtime_svc_manager(workspace).install()
        console.print(f"[green]✓[/green] {msg}")
        console.print("Acompanhe: [bold]bauer runtime service status[/bold] | [bold]bauer runtime service logs[/bold]")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@runtime_service_app.command("uninstall", help="Para e remove o serviço do runtime")
def runtime_service_uninstall(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    try:
        console.print(f"[green]✓[/green] {_runtime_svc_manager(workspace).uninstall()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@runtime_service_app.command("start", help="Inicia o serviço instalado do runtime")
def runtime_service_start(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    try:
        console.print(f"[green]✓[/green] {_runtime_svc_manager(workspace).start()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@runtime_service_app.command("stop", help="Para o serviço do runtime (mantém instalado)")
def runtime_service_stop(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    try:
        console.print(f"[green]✓[/green] {_runtime_svc_manager(workspace).stop()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@runtime_service_app.command("status", help="Estado do serviço: instalado, ativo, PID, uptime")
def runtime_service_status(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    from bauer.process_service import format_uptime
    st = _runtime_svc_manager(workspace).status()
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="dim", width=14)
    table.add_column()
    table.add_row("Plataforma", st.platform)
    table.add_row("Instalado", "[green]sim[/green]" if st.installed else "[red]não[/red]")
    table.add_row("Ativo", "[green]sim[/green]" if st.running else "[red]não[/red]")
    if st.pid:
        table.add_row("PID", str(st.pid))
    if st.uptime_s is not None:
        table.add_row("Uptime", format_uptime(st.uptime_s))
    if st.memory_mb is not None:
        table.add_row("Memória", f"{st.memory_mb:.0f} MB")
    if st.detail:
        table.add_row("Obs", st.detail)
    console.print(Panel(table, title="bauer-runtime", border_style="blue"))
    if not st.installed:
        console.print("Instale com: [bold]bauer runtime service install[/bold]")


@runtime_service_app.command("logs", help="Últimas linhas de log do runtime supervisor")
def runtime_service_logs(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    lines: int = typer.Option(50, "--lines", "-n"),
):
    console.print(_runtime_svc_manager(workspace).logs(lines=lines))
