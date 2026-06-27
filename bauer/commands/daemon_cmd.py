"""Comando bauer daemon (inclui subgrupo service)."""

from __future__ import annotations

from rich.panel import Panel
from pathlib import Path
from rich.table import Table
import typer

from ._common import console

daemon_app = typer.Typer(help="BauerDaemon — pool de workers autonomos que processam tasks do kanban")

daemon_service_app = typer.Typer(
    help="Daemon como SERVICO do sistema (systemd/Task Scheduler) — sobe no boot, reinicia em crash"
)

daemon_app.add_typer(daemon_service_app, name="service")


def _daemon_state_dir() -> "Path":
    """Return the default daemon state directory (~/.bauer/daemon)."""
    import os as _os
    home = _os.environ.get("BAUER_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".bauer"
    return base / "daemon"


def _daemon_log_path() -> "Path":
    return _daemon_state_dir() / "daemon.log"


def _daemon_pid_path() -> "Path":
    return _daemon_state_dir() / "daemon.pid"


def _read_daemon_pid() -> "int | None":
    pid_path = _daemon_pid_path()
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except Exception:
        return None


def _daemon_pid_alive(pid: int) -> bool:
    """Return True if a process with *pid* is currently running."""
    import psutil
    try:
        return psutil.pid_exists(pid)
    except Exception:
        pass
    # Fallback: os.kill(pid, 0) — works on Unix; skip on Windows
    import os as _os
    try:
        _os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False


@daemon_app.command("start")
def daemon_start_cmd(
    board: str = typer.Option("default", "--board", "-b", help="Kanban board slug"),
    workers: int = typer.Option(2, "--workers", "-w", help="Numero de workers paralelos"),
    budget_usd: float = typer.Option(5.0, "--budget-usd", help="Limite de custo USD por sessao"),
    budget_hours: float = typer.Option(1.0, "--budget-hours", help="Limite de tempo em horas"),
    max_llm_calls: int = typer.Option(200, "--max-llm-calls"),
    max_tool_calls: int = typer.Option(500, "--max-tool-calls"),
    poll_interval: float = typer.Option(5.0, "--poll-interval", help="Segundos de sleep quando sem task"),
    profile: str = typer.Option("low", "--profile", help="Perfil do modelo: low/medium/high"),
    headless_mode: str = typer.Option(
        "threshold", "--headless-mode",
        help="Modo de aprovacao: threshold | yolo | deny_all",
    ),
    detach: bool = typer.Option(True, "--detach/--foreground", help="Roda em background (detach) ou prende o terminal"),
    log_level: str = typer.Option("INFO", "--log-level", help="DEBUG | INFO | WARNING | ERROR"),
):
    """Inicia o BauerDaemon — pool de workers que executam tasks do kanban autonomamente."""
    import subprocess as _sp
    import sys as _sys
    import logging as _logging

    state_dir = _daemon_state_dir()

    if detach:
        # Check for existing alive daemon
        existing_pid = _read_daemon_pid()
        if existing_pid and _daemon_pid_alive(existing_pid):
            console.print(
                f"[yellow]Daemon ja rodando[/yellow] pid={existing_pid} "
                f"board={board}. Use [bold]bauer daemon status[/bold] para ver detalhes."
            )
            raise typer.Exit(code=0)

        state_dir.mkdir(parents=True, exist_ok=True)
        log_path = _daemon_log_path()
        log_handle = log_path.open("ab")

        cmd = [
            _sys.executable, "-m", "bauer.cli", "daemon", "_run",
            "--board", board,
            "--workers", str(workers),
            "--budget-usd", str(budget_usd),
            "--budget-hours", str(budget_hours),
            "--max-llm-calls", str(max_llm_calls),
            "--max-tool-calls", str(max_tool_calls),
            "--poll-interval", str(poll_interval),
            "--profile", profile,
            "--headless-mode", headless_mode,
            "--log-level", log_level,
        ]

        import os as _os
        popen_kwargs: dict = {
            "stdout": log_handle,
            "stderr": _sp.STDOUT,
            "stdin": _sp.DEVNULL,
            "close_fds": True,
        }
        if _os.name == "nt":
            popen_kwargs["creationflags"] = (
                getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(_sp, "DETACHED_PROCESS", 0)
            )
        else:
            popen_kwargs["start_new_session"] = True

        try:
            proc = _sp.Popen(cmd, **popen_kwargs)
        finally:
            log_handle.close()

        console.print(
            f"[green]BauerDaemon iniciado em background[/green] pid={proc.pid} "
            f"board={board} workers={workers} budget=${budget_usd:.2f}"
        )
        console.print(f"[dim]Log: {log_path}[/dim]")
        console.print("[dim]Use [bold]bauer daemon status[/bold] e [bold]bauer daemon logs[/bold] para monitorar.[/dim]")
        return

    # ── foreground mode ──────────────────────────────────────────────────────
    import asyncio as _asyncio
    _logging.basicConfig(
        level=getattr(_logging, log_level.upper(), _logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    )

    from ..daemon import BauerDaemon, DaemonConfig

    cfg = DaemonConfig(
        board=board,
        workers=workers,
        max_cost_usd=budget_usd,
        max_wall_seconds=int(budget_hours * 3600),
        max_llm_calls=max_llm_calls,
        max_tool_calls=max_tool_calls,
        poll_interval_seconds=poll_interval,
        profile=profile,
        headless_mode=headless_mode,
        state_dir=state_dir,
    )
    console.print(
        f"[green]BauerDaemon iniciando em foreground[/green] "
        f"board={board} workers={workers} budget=${budget_usd:.2f} Ctrl+C para parar."
    )
    exit_code = _asyncio.run(BauerDaemon(cfg).start())
    raise typer.Exit(code=exit_code)


@daemon_app.command("_run", hidden=True)
def daemon_run_internal_cmd(
    board: str = typer.Option("default", "--board"),
    workers: int = typer.Option(2, "--workers"),
    budget_usd: float = typer.Option(5.0, "--budget-usd"),
    budget_hours: float = typer.Option(1.0, "--budget-hours"),
    max_llm_calls: int = typer.Option(200, "--max-llm-calls"),
    max_tool_calls: int = typer.Option(500, "--max-tool-calls"),
    poll_interval: float = typer.Option(5.0, "--poll-interval"),
    profile: str = typer.Option("low", "--profile"),
    headless_mode: str = typer.Option("threshold", "--headless-mode"),
    log_level: str = typer.Option("INFO", "--log-level"),
):
    """Processo interno chamado por 'daemon start --detach'. Nao chamar diretamente."""
    import asyncio as _asyncio
    import logging as _logging

    _logging.basicConfig(
        level=getattr(_logging, log_level.upper(), _logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    )

    from ..daemon import BauerDaemon, DaemonConfig

    cfg = DaemonConfig(
        board=board,
        workers=workers,
        max_cost_usd=budget_usd,
        max_wall_seconds=int(budget_hours * 3600),
        max_llm_calls=max_llm_calls,
        max_tool_calls=max_tool_calls,
        poll_interval_seconds=poll_interval,
        profile=profile,
        headless_mode=headless_mode,
        state_dir=_daemon_state_dir(),
    )
    exit_code = _asyncio.run(BauerDaemon(cfg).start())
    raise typer.Exit(code=exit_code)


@daemon_app.command("stop")
def daemon_stop_cmd(
    force: bool = typer.Option(False, "--force", "-f", help="Envia SIGKILL apos timeout"),
    timeout: int = typer.Option(10, "--timeout", help="Segundos para aguardar parada graceful"),
):
    """Para o daemon em execucao (envia SIGTERM; aguarda parada graceful)."""
    import os as _os
    import signal as _signal
    import time as _time
    import subprocess as _sp

    def _kill_pid(pid: int, hard: bool = False) -> bool:
        """Kill a PID. Returns True if the signal was sent."""
        try:
            if _os.name == "nt":
                flags = ["/F"] if hard else []
                _sp.call(["taskkill", *flags, "/PID", str(pid)], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            else:
                sig = _signal.SIGKILL if hard else _signal.SIGTERM
                _os.kill(pid, sig)
            return True
        except (ProcessLookupError, OSError):
            return False

    def _cleanup_stale_sessions(reason: str = "daemon_stopped") -> int:
        """Mark running DB sessions as stopped; returns count of sessions cleaned."""
        state_db_path = _daemon_state_dir() / "daemon_state.db"
        if not state_db_path.exists():
            return 0
        from ..daemon import DaemonStateDB
        db = DaemonStateDB(state_db_path)
        sessions = db.get_running()
        count = 0
        for s in sessions:
            db.mark_stopped(s["id"], reason=reason)
            count += 1
        return count

    def _kill_db_session_pids(hard: bool = False) -> list[int]:
        """Kill PIDs of all running DB sessions. Returns list of killed PIDs."""
        state_db_path = _daemon_state_dir() / "daemon_state.db"
        if not state_db_path.exists():
            return []
        from ..daemon import DaemonStateDB
        db = DaemonStateDB(state_db_path)
        killed = []
        for s in db.get_running():
            pid = s.get("pid")
            if pid and _daemon_pid_alive(pid):
                _kill_pid(pid, hard=hard)
                killed.append(pid)
        return killed

    pid = _read_daemon_pid()

    # ── No daemon.pid: check DB for orphaned running sessions ─────────────────
    if pid is None:
        orphan_pids = _kill_db_session_pids(hard=force)
        cleaned = _cleanup_stale_sessions(reason="force_stop_orphan")
        if cleaned:
            console.print(
                f"[yellow]daemon.pid nao encontrado, mas {cleaned} sessao(oes) ativa(s) no DB.[/yellow]"
            )
            if orphan_pids:
                console.print(f"[green]PIDs encerrados: {orphan_pids}[/green]")
            else:
                console.print("[dim]PIDs ja estavam mortos — estado do DB corrigido.[/dim]")
            _daemon_pid_path().unlink(missing_ok=True)
        else:
            console.print("[yellow]Nenhum daemon.pid encontrado. Daemon nao esta rodando.[/yellow]")
        raise typer.Exit(code=0)

    # ── daemon.pid exists but process is already dead ─────────────────────────
    if not _daemon_pid_alive(pid):
        console.print(f"[yellow]PID {pid} nao esta mais ativo. Limpando estado.[/yellow]")
        _daemon_pid_path().unlink(missing_ok=True)
        _cleanup_stale_sessions(reason="stale_pid_cleanup")
        raise typer.Exit(code=0)

    # ── Send SIGTERM and wait ─────────────────────────────────────────────────
    console.print(f"[cyan]Enviando SIGTERM para pid={pid}...[/cyan]")
    _kill_pid(pid, hard=False)

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if not _daemon_pid_alive(pid):
            console.print(f"[green]Daemon encerrado graciosamente[/green] pid={pid}")
            _cleanup_stale_sessions(reason="graceful_stop")
            raise typer.Exit(code=0)
        _time.sleep(0.5)

    # ── Timeout: force kill or warn ───────────────────────────────────────────
    if force:
        console.print(f"[yellow]Timeout — enviando SIGKILL para pid={pid}[/yellow]")
        _kill_pid(pid, hard=True)
        _cleanup_stale_sessions(reason="force_kill")
        console.print("[green]Daemon encerrado forcosamente.[/green]")
    else:
        console.print(
            f"[yellow]Daemon ainda ativo apos {timeout}s. "
            f"Use --force para SIGKILL.[/yellow]"
        )
        raise typer.Exit(code=1)


@daemon_app.command("status")
def daemon_status_cmd(
    as_json: bool = typer.Option(False, "--json", help="Saida em JSON"),
    all_sessions: bool = typer.Option(False, "--all", "-a", help="Mostra todas as sessoes, nao so as ativas"),
):
    """Mostra estado atual do daemon (sessoes ativas, budget, workers)."""
    import json as _json
    import time as _time

    state_db_path = _daemon_state_dir() / "daemon_state.db"
    pid = _read_daemon_pid()
    alive = pid is not None and _daemon_pid_alive(pid)

    if not state_db_path.exists():
        if as_json:
            console.print(_json.dumps({"running": False, "pid": None, "sessions": []}, indent=2))
        else:
            console.print("[dim]Nenhuma sessao de daemon encontrada.[/dim]")
            console.print(f"[dim]Use [bold]bauer daemon start[/bold] para iniciar.[/dim]")
        raise typer.Exit(code=0)

    from ..daemon import DaemonStateDB

    db = DaemonStateDB(state_db_path)
    if all_sessions:
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(str(state_db_path), timeout=5.0)
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM daemon_sessions ORDER BY started_at DESC LIMIT 20"
        ).fetchall()
        sessions = [dict(r) for r in rows]
        conn.close()
    else:
        sessions = db.get_running()
        # Also include the latest if nothing is running
        if not sessions:
            latest = db.get_latest()
            if latest:
                sessions = [latest]

    if as_json:
        console.print(_json.dumps(
            {"running": alive, "pid": pid, "sessions": sessions},
            indent=2, ensure_ascii=False,
        ))
        raise typer.Exit(code=0)

    # Pretty table
    console.print(
        f"\n[bold]BauerDaemon[/bold]  "
        + ("[green]RODANDO[/green]" if alive else "[red]PARADO[/red]")
        + (f"  pid={pid}" if pid else "")
    )
    if not sessions:
        console.print("[dim]Nenhuma sessao registrada.[/dim]")
        raise typer.Exit(code=0)

    table = Table(show_lines=False, box=None)
    table.add_column("Sessao", style="cyan", no_wrap=True)
    table.add_column("Board")
    table.add_column("Workers", justify="right")
    table.add_column("Status")
    table.add_column("Uptime")
    table.add_column("Budget")
    table.add_column("Shutdown")

    now = _time.time()
    for s in sessions:
        uptime_s = now - (s.get("started_at") or now)
        uptime_str = (
            f"{int(uptime_s // 3600)}h{int(uptime_s % 3600 // 60)}m"
            if uptime_s >= 60 else f"{int(uptime_s)}s"
        )
        budget_info = ""
        if s.get("budget_json"):
            try:
                b = _json.loads(s["budget_json"])
                cost = b.get("cost_usd", 0)
                pct = b.get("cost_pct", 0)
                budget_info = f"${cost:.3f} ({pct:.0f}%)"
            except Exception:
                pass
        status_color = "green" if s.get("status") == "running" else "dim"
        table.add_row(
            s.get("id", "")[:24],
            s.get("board", ""),
            str(s.get("workers", "")),
            f"[{status_color}]{s.get('status', '')}[/{status_color}]",
            uptime_str,
            budget_info or "-",
            s.get("shutdown_reason") or "-",
        )
    console.print(table)
    console.print()


@daemon_app.command("logs")
def daemon_logs_cmd(
    lines: int = typer.Option(50, "--lines", "-n", help="Numero de linhas iniciais"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Segue o arquivo de log (tail -f)"),
):
    """Exibe o log do daemon (tail). Use --follow para acompanhar em tempo real."""
    import time as _time

    log_path = _daemon_log_path()
    if not log_path.exists():
        console.print(f"[dim]Nenhum log encontrado em {log_path}[/dim]")
        console.print("[dim]Inicie o daemon com [bold]bauer daemon start[/bold] primeiro.[/dim]")
        raise typer.Exit(code=0)

    # Print last N lines
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        console.print(f"[red]Erro ao ler log:[/red] {exc}")
        raise typer.Exit(code=1)

    for line in content[-lines:]:
        console.print(line)

    if not follow:
        return

    # Follow mode — poll for new content
    console.print(f"\n[dim]--- seguindo {log_path} (Ctrl+C para parar) ---[/dim]")
    offset = log_path.stat().st_size
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            while True:
                line = fh.readline()
                if line:
                    console.print(line, end="")
                else:
                    _time.sleep(0.25)
    except KeyboardInterrupt:
        console.print("\n[dim]Log encerrado.[/dim]")


def _daemon_service_cfg(
    board: str = "default",
    workers: int = 2,
    budget_usd: float = 5.0,
    budget_hours: float = 24.0,
) -> "ProcessServiceConfig":  # noqa: F821
    from bauer.process_service import ProcessServiceConfig, pid_reader_from_file
    return ProcessServiceConfig(
        service_name="bauer-daemon",
        task_name="BauerDaemon",
        description="Bauer Daemon — pool de workers autônomos (kanban tasks)",
        entry_args=[
            "daemon", "_run",
            "--board", board,
            "--workers", str(workers),
            "--budget-usd", str(budget_usd),
            "--budget-hours", str(budget_hours),
        ],
        log_file=Path("logs") / "daemon.log",
        pid_reader=pid_reader_from_file(
            lambda _: _daemon_pid_path(),
            keyword="daemon",
        ),
        cmdline_keyword="daemon",
    )


def _daemon_svc_manager(
    board: str = "default",
    workers: int = 2,
    budget_usd: float = 5.0,
    budget_hours: float = 24.0,
) -> "ProcessServiceManager":  # noqa: F821
    from bauer.process_service import ProcessServiceManager
    from ..paths import get_bauer_home
    return ProcessServiceManager(_daemon_service_cfg(board, workers, budget_usd, budget_hours), project_dir=get_bauer_home())


@daemon_service_app.command("install", help="Instala E inicia o daemon como serviço do sistema")
def daemon_service_install(
    board: str = typer.Option("default", "--board", "-b"),
    workers: int = typer.Option(2, "--workers", "-w"),
    budget_usd: float = typer.Option(5.0, "--budget-usd"),
    budget_hours: float = typer.Option(24.0, "--budget-hours"),
):
    try:
        msg = _daemon_svc_manager(board, workers, budget_usd, budget_hours).install()
        console.print(f"[green]✓[/green] {msg}")
        console.print("Acompanhe: [bold]bauer daemon service status[/bold] | [bold]bauer daemon service logs[/bold]")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@daemon_service_app.command("uninstall", help="Para e remove o serviço do daemon")
def daemon_service_uninstall():
    try:
        console.print(f"[green]✓[/green] {_daemon_svc_manager().uninstall()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@daemon_service_app.command("start", help="Inicia o serviço instalado do daemon")
def daemon_service_start():
    try:
        console.print(f"[green]✓[/green] {_daemon_svc_manager().start()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@daemon_service_app.command("stop", help="Para o serviço do daemon (mantém instalado)")
def daemon_service_stop():
    try:
        console.print(f"[green]✓[/green] {_daemon_svc_manager().stop()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@daemon_service_app.command("status", help="Estado do serviço: instalado, ativo, PID, uptime")
def daemon_service_status():
    from bauer.process_service import format_uptime
    st = _daemon_svc_manager().status()
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
    console.print(Panel(table, title="bauer-daemon", border_style="cyan"))
    if not st.installed:
        console.print("Instale com: [bold]bauer daemon service install[/bold]")


@daemon_service_app.command("logs", help="Últimas linhas de log do daemon")
def daemon_service_logs(
    lines: int = typer.Option(50, "--lines", "-n"),
):
    console.print(_daemon_svc_manager().logs(lines=lines))
