"""Comando bauer serve (inclui subgrupo service)."""

from __future__ import annotations

from rich.panel import Panel
from pathlib import Path
from rich.table import Table
from ..logging_config import setup_logging
import typer

from ._common import _MEMORY_DIR, _RUNTIME_STATE_DEFAULT, _WORKSPACE_DIR, console
from ._runtime import _build_client, _build_router, _get_or_run_state, _load_or_die, _resolve_model_with_ram_check, _start_gateway_thread_cli, build_fallback_clients

serve_app = typer.Typer(
    invoke_without_command=True,
    help="Bauer Agent como servidor HTTP (REST + SSE) — ou 'serve service' para servico do sistema",
)

serve_service_app = typer.Typer(
    help="Serve como SERVICO do sistema (systemd/launchd/Task Scheduler) — sobe no boot, reinicia em crash"
)

serve_app.add_typer(serve_service_app, name="service")


@serve_app.callback()
def serve(
    ctx: typer.Context,
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    models: Path = typer.Option(Path("models.yaml"), "--models", help="Caminho do models.yaml"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(_RUNTIME_STATE_DEFAULT, "--state-file"),
    host: str = typer.Option("", "--host", help="Host de escuta (padrao: config serve.host)"),
    port: int = typer.Option(0, "--port", help="Porta (padrao: config serve.port)"),
    model: str = typer.Option("", "--model", help="Sobrescreve modelo do config"),
    api_key: str = typer.Option("", "--api-key", help="Sobrescreve serve.api_key do config"),
    sessions_dir: Path = typer.Option(Path("memory/sessions"), "--sessions-dir"),
    gateway_port: int = typer.Option(
        0, "--gateway-port", "-g",
        help="Porta do gateway WebSocket Claw3D (0 = desabilitado). Ex: --gateway-port 18789",
    ),
):
    """Inicia o Bauer Agent como servidor HTTP (REST + SSE).

    Requer: pip install 'bauer-agent[server]'

    Endpoints disponiveis apos iniciar:
      GET  /health        — liveness
      GET  /status        — modelo e tools
      POST /chat          — envia mensagem, recebe resposta
      GET  /stream        — resposta em tempo real (SSE)
      GET  /sessions      — lista sessoes (requer auth)
      GET  /docs          — documentacao interativa (Swagger)

    Use 'bauer serve service install' para rodar como servico do sistema.
    """
    if ctx.invoked_subcommand is not None:
        return
    try:
        from ..server import create_app, run_server
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    cfg, reg = _load_or_die(config, models)
    setup_logging(cfg.logging.level, cfg.logging.file)

    state = _get_or_run_state(cfg, reg, state_file)

    if not state.get("ollama_alive"):
        console.print("[red]Ollama offline.[/red] Verifique se o Ollama esta rodando.")
        raise typer.Exit(code=1)

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)

    _client = _build_client(cfg)

    model_name = model or _resolve_model_with_ram_check(
        state["configured_model"], reg, _client,
        state["ram_available_mb"], cfg.runtime.safety_margin_mb, _MEMORY_DIR,
    )
    if not _client.has_model(model_name):
        console.print(
            f"[red]Modelo '{model_name}' nao encontrado no Ollama.[/red]\n"
            f"Rode: [bold]ollama pull {model_name}[/bold]"
        )
        raise typer.Exit(code=1)

    applied_context = state["context"]["applied"]
    router = _build_router(cfg, workspace)

    from ..agent import _build_system_prompt
    system_prompt = _build_system_prompt(router)

    # Fallback de provider (429/5xx) — paridade com o CLI `bauer agent`.
    try:
        _fallback_clients = build_fallback_clients(cfg)
    except Exception:  # noqa: BLE001 — best-effort, nunca impede o serve de subir
        _fallback_clients = []

    serve_host = host or cfg.serve.host
    serve_port = port or cfg.serve.port
    serve_key = api_key or cfg.serve.api_key

    fastapi_app = create_app(
        model_name=model_name,
        applied_context=applied_context,
        router=router,
        client=_client,
        system_prompt=system_prompt,
        sessions_dir=sessions_dir,
        api_key=serve_key,
        rate_limit_requests=cfg.serve.rate_limit_requests,
        rate_limit_window_s=cfg.serve.rate_limit_window_s,
        rate_limit_per_key=cfg.serve.rate_limit_per_key,
        cors_origins=list(cfg.serve.cors_origins) or None,
        enable_gzip=cfg.serve.enable_gzip,
        enable_access_log=cfg.serve.enable_access_log,
        config_path=config,
        fallback_clients=_fallback_clients,
    )

    auth_status = "[green]habilitada[/green]" if serve_key else "[yellow]desabilitada[/yellow]"
    base_url = f"http://{serve_host}:{serve_port}"
    console.print(f"\n[bold]Bauer Agent Server[/bold] — {model_name}")
    console.print(f"  HTTP:       {base_url}")
    console.print(f"  Docs:       {base_url}/docs")
    console.print(f"  Auth:       {auth_status}")
    console.print(f"[dim]  Config:     {config.resolve()}[/dim]")
    console.print(f"  Tools:      {', '.join(router.available_tools())}")
    console.print(f"[dim]  OpenAI-compat:  POST {base_url}/v1/chat/completions[/dim]")

    # Gateway WebSocket opcional
    if gateway_port > 0:
        _start_gateway_thread_cli(
            bauer_url=base_url,
            host=serve_host or "0.0.0.0",
            port=gateway_port,
            api_key=serve_key,
            console=console,
        )
    else:
        console.print(
            "[dim]  Claw3D Gateway: desabilitado (use --gateway-port 18789 para ativar)[/dim]"
        )

    console.print()
    pid_file = workspace / ".bauer_serve" / "serve.pid"
    run_server(fastapi_app, host=serve_host, port=serve_port, pid_file=pid_file)


def _serve_pid_path(project_dir: "Path") -> "Path":
    """PID file do servidor HTTP — workspace/.bauer_serve/serve.pid."""
    ws: "Path" = Path("workspace")
    try:
        from ..config_loader import load_config
        cfg = load_config(project_dir / "config.yaml")
        loaded = Path(cfg.agent.workspace)
        ws = loaded if loaded.is_absolute() else project_dir / loaded
    except Exception:  # noqa: BLE001
        pass
    return ws / ".bauer_serve" / "serve.pid"


def _serve_service_cfg() -> "ProcessServiceConfig":  # noqa: F821
    from bauer.process_service import ProcessServiceConfig, pid_reader_from_file
    return ProcessServiceConfig(
        service_name="bauer-serve",
        task_name="BauerServe",
        description="Bauer Agent HTTP Server — REST API e interface web",
        entry_args=["serve"],
        log_file=Path("logs") / "serve.log",
        pid_reader=pid_reader_from_file(_serve_pid_path, keyword="server"),
        cmdline_keyword="serve",
    )


def _serve_svc_manager() -> "ProcessServiceManager":  # noqa: F821
    from bauer.process_service import ProcessServiceManager
    return ProcessServiceManager(_serve_service_cfg())


@serve_service_app.command("install", help="Instala E inicia o servidor HTTP como serviço do sistema")
def serve_service_install():
    try:
        msg = _serve_svc_manager().install()
        console.print(f"[green]✓[/green] {msg}")
        console.print(
            "Acesse: [bold]http://localhost:8000[/bold]  |  "
            "Acompanhe: [bold]bauer serve service status[/bold]"
        )
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@serve_service_app.command("uninstall", help="Para e remove o serviço do servidor HTTP")
def serve_service_uninstall():
    try:
        console.print(f"[green]✓[/green] {_serve_svc_manager().uninstall()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@serve_service_app.command("start", help="Inicia o serviço instalado do servidor HTTP")
def serve_service_start():
    try:
        console.print(f"[green]✓[/green] {_serve_svc_manager().start()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@serve_service_app.command("stop", help="Para o servidor HTTP (mantém instalado)")
def serve_service_stop():
    try:
        console.print(f"[green]✓[/green] {_serve_svc_manager().stop()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@serve_service_app.command("status", help="Estado do serviço: instalado, ativo, PID, uptime")
def serve_service_status():
    from bauer.process_service import format_uptime
    st = _serve_svc_manager().status()
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
    console.print(Panel(table, title="bauer-serve", border_style="yellow"))
    if not st.installed:
        console.print("Instale com: [bold]bauer serve service install[/bold]")


@serve_service_app.command("logs", help="Últimas linhas de log do servidor HTTP")
def serve_service_logs(
    lines: int = typer.Option(50, "--lines", "-n"),
):
    console.print(_serve_svc_manager().logs(lines=lines))
