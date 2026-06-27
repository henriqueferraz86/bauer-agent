"""Comando bauer telegram."""

from __future__ import annotations

from pathlib import Path
import typer

from ._common import console
from ._runtime import _kill_bridge_processes

telegram_app = typer.Typer(help="Telegram Bridge — agente Bauer via Telegram")


@telegram_app.command("start", help="Inicia o canal Telegram (foreground)")
def telegram_start(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    """Sobe só o bridge Telegram. Para todos os canais use `bauer gateway start`."""
    from bauer.telegram_bridge import run_bridge

    console.print("[green]Telegram bridge — foreground (Ctrl+C para parar)[/green]")
    try:
        run_bridge(config)
    except RuntimeError as exc:
        console.print(f"[red]ERRO:[/red] {exc}")
        raise typer.Exit(code=1)


@telegram_app.command("stop", help="Para bridges Telegram em execução (inclusive antigos)")
def telegram_stop():
    killed = _kill_bridge_processes("telegram_bridge")
    if killed:
        console.print(f"[green]✓ {killed} processo(s) de bridge encerrado(s).[/green]")
    else:
        console.print("[yellow]Nenhum bridge Telegram em execução.[/yellow]")


@telegram_app.command("test", help="Valida o token do bot (getMe)")
def telegram_test(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    from bauer.channel_base import resolve_token
    from bauer.config_loader import load_config as _load_cfg

    cfg = _load_cfg(config)
    token = resolve_token(cfg.telegram.bot_token, "TELEGRAM_BOT_TOKEN")
    if not token:
        console.print("[red]Token ausente.[/red] Defina TELEGRAM_BOT_TOKEN no .env "
                      "ou rode `bauer gateway init`.")
        raise typer.Exit(code=1)
    import httpx
    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        bot = r.json().get("result", {})
        if r.json().get("ok"):
            console.print(f"[green]✓ Bot @{bot.get('username')} conectado.[/green]")
        else:
            console.print(f"[red]Token inválido:[/red] {r.json().get('description')}")
            raise typer.Exit(code=1)
    except httpx.HTTPError as exc:
        console.print(f"[red]Erro de rede:[/red] {exc}")
        raise typer.Exit(code=1)
