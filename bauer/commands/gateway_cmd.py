"""Comando bauer gateway (inclui subgrupo service)."""

from __future__ import annotations

from rich.panel import Panel
from pathlib import Path
from rich.table import Table
import sys
import typer

from ._common import console
from ._runtime import _kill_bridge_processes

gateway_app = typer.Typer(help="Bauer Gateway — todos os canais de chat + entrega do outbox")

gateway_service_app = typer.Typer(
    help="Gateway como SERVIÇO do sistema (systemd/Task Scheduler) — sobe no boot, reinicia em crash"
)

gateway_app.add_typer(gateway_service_app, name="service")


def _gateway_pid_file(workspace: Path) -> Path:
    return workspace / ".bauer_gateway" / "gateway.pid"


def _gateway_workspace(config: Path) -> Path:
    try:
        from bauer.config_loader import load_config as _load_cfg
        return Path(_load_cfg(config).agent.workspace)
    except Exception:
        return Path("workspace")


def _gateway_start_background(config: Path) -> None:
    """Sobe o gateway destacado (detach) e libera o terminal. Log em arquivo."""
    import os as _os
    import subprocess as _sp

    workspace = _gateway_workspace(config)
    pid_file = _gateway_pid_file(workspace)

    # Já rodando? Não sobe outro.
    if pid_file.exists():
        try:
            import psutil
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            if psutil.pid_exists(pid):
                console.print(
                    f"[yellow]Gateway já rodando[/yellow] pid={pid}. "
                    f"Use [bold]bauer gateway status[/bold] ou [bold]bauer gateway stop[/bold]."
                )
                return
        except Exception:
            pass  # PID stale — segue e sobe um novo

    log_path = workspace / ".bauer_gateway" / "gateway.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab")

    # O filho roda a versão FOREGROUND (sem --background) e escreve o próprio PID.
    cmd = [
        sys.executable, "-m", "bauer.cli", "gateway", "start",
        "--config", str(Path(config).resolve()),
    ]
    popen_kwargs: dict = {
        "stdout": log_handle,
        "stderr": _sp.STDOUT,
        "stdin": _sp.DEVNULL,
        "close_fds": True,
        "cwd": str(Path(__file__).resolve().parent.parent.parent),
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

    # O filho grava o próprio pid no pid_file; aguarda um instante e lê o real.
    import time as _time
    real_pid = proc.pid
    for _ in range(20):
        _time.sleep(0.1)
        try:
            real_pid = int(pid_file.read_text(encoding="utf-8").strip())
            break
        except Exception:
            continue

    console.print(
        f"[green]✓ Bauer Gateway iniciado em background[/green] pid={real_pid}"
    )
    console.print(f"[dim]Log:[/dim] {log_path}")
    console.print(
        "[dim]Monitorar: [/dim][bold]bauer gateway status[/bold]"
        "[dim]  ·  Parar: [/dim][bold]bauer gateway stop[/bold]"
    )


@gateway_app.command("stop", help="Para o Bauer Gateway em execução")
def gateway_stop(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    from bauer.config_loader import load_config as _load_cfg

    try:
        workspace = Path(_load_cfg(config).agent.workspace)
    except Exception:
        workspace = Path("workspace")
    pid_file = _gateway_pid_file(workspace)
    killed = 0
    if pid_file.exists():
        try:
            import psutil
            pid = int(pid_file.read_text().strip())
            psutil.Process(pid).terminate()
            killed += 1
        except Exception:
            pass
        pid_file.unlink(missing_ok=True)
    killed += _kill_bridge_processes(
        "gateway_runtime", "telegram_bridge", "discord_bridge", "slack_bridge"
    )
    if killed:
        console.print(f"[green]✓ Gateway parado ({killed} processo(s)).[/green]")
    else:
        console.print("[yellow]Nenhum gateway em execução.[/yellow]")


@gateway_app.command("start", help="Inicia todos os canais habilitados + outbox pump")
def gateway_start(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    background: bool = typer.Option(
        False, "--background", "-b",
        help="Roda em background (detach) e libera o terminal",
    ),
):
    """Bauer Gateway: canais do config.yaml (telegram/discord) + entrega do outbox.

    Use --background / -b para rodar destacado (log em workspace/.bauer_gateway/
    gateway.log). Pare com `bauer gateway stop`.
    """
    if background:
        _gateway_start_background(config)
        return

    import logging
    import os

    from bauer.gateway_runtime import BauerGatewayRuntime

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    runtime = BauerGatewayRuntime.from_config(config)
    if not runtime.bridges:
        console.print(
            "[yellow]Nenhum canal habilitado.[/yellow] Habilite telegram/discord/slack no "
            "config.yaml ou rode [bold]bauer gateway init[/bold]."
        )
    names = ", ".join(b.name for b in runtime.bridges) or "nenhum canal"
    console.print(f"[green]Bauer Gateway no ar[/green] — {names} + outbox pump. Ctrl+C para parar.")
    pid_file = _gateway_pid_file(Path(runtime.workspace))
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    try:
        runtime.start(block=True)
    finally:
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass


@gateway_app.command("status", help="Status dos canais e do outbox")
def gateway_status(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    from bauer.channel_base import resolve_token
    from bauer.config_loader import load_config as _load_cfg
    from bauer.gateway_outbox import GatewayOutbox

    cfg = _load_cfg(config)
    table = Table(title="Bauer Gateway", show_lines=False)
    table.add_column("Canal")
    table.add_column("Habilitado")
    table.add_column("Token")
    table.add_column("Allowlist")

    tg_token = resolve_token(cfg.telegram.bot_token, "TELEGRAM_BOT_TOKEN")
    dc_token = resolve_token(cfg.discord.bot_token, "DISCORD_BOT_TOKEN")
    sl_token = resolve_token(cfg.slack.bot_token, "SLACK_BOT_TOKEN")
    sl_app_token = resolve_token(cfg.slack.app_token, "SLACK_APP_TOKEN")
    table.add_row(
        "telegram",
        "[green]sim[/green]" if cfg.telegram.enabled else "[dim]não[/dim]",
        "[green]ok[/green]" if tg_token else "[red]ausente[/red]",
        f"{len(cfg.telegram.allowed_users)} usuários"
        + (" [yellow](allow_all!)[/yellow]" if cfg.telegram.allow_all else ""),
    )
    table.add_row(
        "discord",
        "[green]sim[/green]" if cfg.discord.enabled else "[dim]não[/dim]",
        "[green]ok[/green]" if dc_token else "[red]ausente[/red]",
        f"{len(cfg.discord.allowed_users)} usuários"
        + (" [yellow](allow_all!)[/yellow]" if cfg.discord.allow_all else ""),
    )
    table.add_row(
        "slack",
        "[green]sim[/green]" if cfg.slack.enabled else "[dim]não[/dim]",
        "[green]ok[/green]" if (sl_token and sl_app_token) else "[red]ausente[/red]",
        f"{len(cfg.slack.allowed_users)} usuários"
        + (" [yellow](allow_all!)[/yellow]" if cfg.slack.allow_all else ""),
    )
    console.print(table)

    workspace = Path(cfg.agent.workspace)
    try:
        pending = len(GatewayOutbox(workspace).pending(limit=100))
        console.print(f"Outbox: [bold]{pending}[/bold] mensagem(ns) pendente(s)")
    except Exception:
        console.print("Outbox: [dim]vazio/indisponível[/dim]")

    # Estado real do processo (PID vivo? uptime? memória?) — não só PID file
    from bauer.gateway_service import format_uptime, read_process_status
    pid, uptime, mem = read_process_status(Path.cwd())
    if pid is not None:
        console.print(
            f"Runtime: [green]ativo[/green] — PID {pid}, "
            f"uptime {format_uptime(uptime or 0)}, {mem:.0f}MB"
        )
    else:
        console.print(
            "Runtime: [dim]parado[/dim] — `bauer gateway start` (foreground) "
            "ou `bauer gateway service install` (serviço)"
        )


def _service_manager():
    from bauer.gateway_service import GatewayServiceManager
    return GatewayServiceManager()  # usa ~/.bauer/ como working dir


@gateway_service_app.command("install", help="Instala E inicia o serviço (systemd/Task Scheduler)")
def gateway_service_install():
    try:
        msg = _service_manager().install()
        console.print(f"[green]✓[/green] {msg}")
        console.print("Acompanhe: [bold]bauer gateway service status[/bold] | logs: [bold]bauer gateway service logs[/bold]")
    except RuntimeError as exc:
        console.print(f"[red]ERRO:[/red] {exc}")
        raise typer.Exit(code=1)


@gateway_service_app.command("uninstall", help="Para e remove o serviço")
def gateway_service_uninstall():
    try:
        console.print(f"[green]✓[/green] {_service_manager().uninstall()}")
    except RuntimeError as exc:
        console.print(f"[red]ERRO:[/red] {exc}")
        raise typer.Exit(code=1)


@gateway_service_app.command("start", help="Inicia o serviço instalado")
def gateway_service_start():
    try:
        console.print(f"[green]✓[/green] {_service_manager().start()}")
    except RuntimeError as exc:
        console.print(f"[red]ERRO:[/red] {exc}")
        raise typer.Exit(code=1)


@gateway_service_app.command("stop", help="Para o serviço (a tarefa/unit continua instalada)")
def gateway_service_stop():
    try:
        console.print(f"[green]✓[/green] {_service_manager().stop()}")
    except RuntimeError as exc:
        console.print(f"[red]ERRO:[/red] {exc}")
        raise typer.Exit(code=1)


@gateway_service_app.command("status", help="Estado do serviço: instalado, ativo, PID, uptime, memória")
def gateway_service_status():
    from bauer.gateway_service import SERVICE_NAME, TASK_NAME, format_uptime

    mgr = _service_manager()
    st = mgr.status()
    name = SERVICE_NAME if st.platform == "systemd" else TASK_NAME
    table = Table(title=f"Bauer Gateway Service — {name}", show_lines=False)
    table.add_column("Campo")
    table.add_column("Valor")
    table.add_row("Plataforma", st.platform)
    table.add_row("Instalado", "[green]sim[/green]" if st.installed else "[red]não[/red]")
    table.add_row(
        "Inicia no boot/logon",
        "[green]sim[/green]" if st.enabled else "[dim]não[/dim]",
    )
    table.add_row(
        "Em execução",
        "[green]sim[/green]" if st.running else "[red]não[/red]",
    )
    if st.pid is not None:
        table.add_row("PID", str(st.pid))
        table.add_row("Uptime", format_uptime(st.uptime_s or 0))
        table.add_row("Memória", f"{st.memory_mb:.0f} MB" if st.memory_mb else "—")
    if st.detail:
        table.add_row("Obs", st.detail)
    console.print(table)
    if not st.installed:
        console.print("Instale com: [bold]bauer gateway service install[/bold]")


@gateway_service_app.command("logs", help="Últimas linhas de log do gateway")
def gateway_service_logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Quantidade de linhas"),
):
    console.print(_service_manager().logs(lines=lines))


@gateway_app.command("init", help="Wizard interativo: configura Telegram/Discord/Slack")
def gateway_init(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    """Configura canais passo a passo: token, validação live, allowlist, .env."""
    import os
    import time

    import httpx
    import yaml as _yaml
    from rich.prompt import Confirm, Prompt

    console.print(Panel.fit("[bold]Bauer Gateway — setup de canais[/bold]\n"
                            "Tokens vão para o [cyan].env[/cyan]; o resto para o config.yaml."))

    if not config.exists():
        console.print(f"[red]{config} não encontrado.[/red]")
        raise typer.Exit(code=1)
    raw = _yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    env_lines: list[str] = []

    def _ask_token(label: str, env_var: str) -> str:
        """Pede um token com colagem funcionando.

        password=True (getpass) bloqueia paste em vários terminais Windows —
        usuário relatou não conseguir colar. Entrada visível resolve; quem
        já exportou a env var nem precisa digitar.
        """
        existing = os.environ.get(env_var, "").strip()
        if existing:
            masked = existing[:6] + "…" + existing[-4:] if len(existing) > 12 else "***"
            if Confirm.ask(f"{env_var} já está definido ({masked}). Usar esse?", default=True):
                return existing
        console.print("[dim](entrada visível para permitir colar — Ctrl+V / botão direito)[/dim]")
        return Prompt.ask(label, default="").strip()

    # ── Telegram ───────────────────────────────────────────────────────────
    if Confirm.ask("Configurar [bold]Telegram[/bold]?", default=True):
        console.print("\nCrie um bot com o [bold]@BotFather[/bold] no Telegram e copie o token.")
        token = _ask_token("Token do bot", "TELEGRAM_BOT_TOKEN")
        bot_name = ""
        if token:
            try:
                r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
                if r.json().get("ok"):
                    bot_name = r.json()["result"].get("username", "")
                    console.print(f"[green]✓ Token válido — bot @{bot_name}[/green]")
                else:
                    console.print(f"[red]Token rejeitado:[/red] {r.json().get('description')}")
                    token = ""
            except httpx.HTTPError as exc:
                console.print(f"[yellow]Não validou (rede): {exc} — salvando assim mesmo.[/yellow]")
        allowed: list[int] = list((raw.get("telegram") or {}).get("allowed_users", []))
        if token and Confirm.ask(
            "Descobrir seu user id agora? (envie /start ao bot)", default=True
        ):
            console.print(f"[cyan]Aguardando mensagem para @{bot_name} (60s)…[/cyan]")
            deadline = time.time() + 60
            found: set[int] = set()
            offset = 0
            while time.time() < deadline and not found:
                try:
                    r = httpx.get(
                        f"https://api.telegram.org/bot{token}/getUpdates",
                        params={"timeout": 10, "offset": offset + 1}, timeout=20,
                    )
                    for up in r.json().get("result", []):
                        offset = max(offset, up.get("update_id", 0))
                        uid = ((up.get("message") or {}).get("from") or {}).get("id")
                        uname = ((up.get("message") or {}).get("from") or {}).get("username", "?")
                        if uid:
                            found.add(int(uid))
                            console.print(f"[green]✓ Detectado: @{uname} (id {uid})[/green]")
                except httpx.HTTPError:
                    time.sleep(2)
            allowed = sorted(set(allowed) | found)
            if not found:
                console.print("[yellow]Nenhuma mensagem recebida — adicione seu id depois "
                              "em telegram.allowed_users.[/yellow]")
        if token:
            env_lines.append(f"TELEGRAM_BOT_TOKEN={token}")
        tg = raw.setdefault("telegram", {})
        tg["enabled"] = True
        tg["allowed_users"] = allowed

    # ── Discord ────────────────────────────────────────────────────────────
    if Confirm.ask("\nConfigurar [bold]Discord[/bold]?", default=False):
        console.print(
            "\n1. https://discord.com/developers/applications → New Application → Bot\n"
            "2. Copie o token; habilite [bold]MESSAGE CONTENT INTENT[/bold] na aba Bot\n"
            "3. Convide o bot: OAuth2 → URL Generator → scope 'bot' → Send Messages"
        )
        token = _ask_token("Token do bot", "DISCORD_BOT_TOKEN")
        if token:
            try:
                r = httpx.get(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": f"Bot {token}"}, timeout=10,
                )
                if r.status_code == 200:
                    console.print(f"[green]✓ Token válido — bot {r.json().get('username')}[/green]")
                else:
                    console.print(f"[red]Token rejeitado (HTTP {r.status_code}).[/red]")
                    token = ""
            except httpx.HTTPError as exc:
                console.print(f"[yellow]Não validou (rede): {exc} — salvando assim mesmo.[/yellow]")
        user_id = Prompt.ask(
            "Seu user id do Discord (Configurações → Avançado → Modo desenvolvedor → "
            "botão direito no seu nome → Copiar ID)", default=""
        ).strip()
        if token:
            env_lines.append(f"DISCORD_BOT_TOKEN={token}")
        dc = raw.setdefault("discord", {})
        dc["enabled"] = True
        if user_id:
            existing = set(dc.get("allowed_users", []))
            existing.add(user_id)
            dc["allowed_users"] = sorted(existing)

    # ── Slack ──────────────────────────────────────────────────────────────
    if Confirm.ask("\nConfigurar [bold]Slack[/bold]?", default=False):
        console.print(
            "\n1. https://api.slack.com/apps → Create New App\n"
            "2. Socket Mode (menu lateral) → habilitar → gera o App-Level "
            "Token ([bold]xapp-…[/bold], escopo connections:write)\n"
            "3. OAuth & Permissions → Bot Token Scopes: chat:write, im:history, "
            "im:read, channels:history, app_mentions:read → Install to Workspace "
            "gera o Bot Token ([bold]xoxb-…[/bold])\n"
            "4. Event Subscriptions → habilitar → inscreva message.im e app_mention"
        )
        bot_token = _ask_token("Bot Token (xoxb-…)", "SLACK_BOT_TOKEN")
        app_token = _ask_token("App-Level Token (xapp-…)", "SLACK_APP_TOKEN")
        if bot_token:
            try:
                r = httpx.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {bot_token}"}, timeout=10,
                )
                data = r.json()
                if data.get("ok"):
                    console.print(f"[green]✓ Token válido — bot {data.get('user')}[/green]")
                else:
                    console.print(f"[red]Token rejeitado:[/red] {data.get('error')}")
                    bot_token = ""
            except httpx.HTTPError as exc:
                console.print(f"[yellow]Não validou (rede): {exc} — salvando assim mesmo.[/yellow]")
        user_id = Prompt.ask(
            "Seu user id do Slack (perfil → ⋮ → Copy member ID)", default=""
        ).strip()
        if bot_token:
            env_lines.append(f"SLACK_BOT_TOKEN={bot_token}")
        if app_token:
            env_lines.append(f"SLACK_APP_TOKEN={app_token}")
        sl = raw.setdefault("slack", {})
        sl["enabled"] = True
        if user_id:
            existing = set(sl.get("allowed_users", []))
            existing.add(user_id)
            sl["allowed_users"] = sorted(existing)

    # ── Persistência ───────────────────────────────────────────────────────
    if env_lines:
        env_path = Path(".env")
        current = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        new_content = current.rstrip("\n")
        for line in env_lines:
            key = line.split("=", 1)[0]
            if f"{key}=" in current:
                console.print(f"[yellow]{key} já existe no .env — não sobrescrevi.[/yellow]")
                continue
            new_content += ("\n" if new_content else "") + line
        env_path.write_text(new_content + "\n", encoding="utf-8")
        console.print(f"[green]✓ Tokens gravados no {env_path}[/green]")

    config.write_text(
        _yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    console.print(f"[green]✓ Seções atualizadas no {config}[/green]")
    console.print("\nPróximo passo: [bold]bauer gateway start[/bold]")
