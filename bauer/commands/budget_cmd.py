"""Budget and autonomy commands."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from ._common import console
from ..core.runtime.autonomy import AUTONOMY_MODES, BudgetManager

budget_app = typer.Typer(help="Budget de autonomia do runtime.")
autonomy_app = typer.Typer(help="Modo de autonomia do runtime.")
continuous_app = typer.Typer(help="Observação contínua segura de alvos configurados.")
autonomy_app.add_typer(continuous_app, name="continuous")


@budget_app.command("status")
def budget_status_cmd(
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    status = BudgetManager(root=state_dir).status()
    profile = status["profile"]
    table = Table(title=f"Budget Runtime - autonomy={profile['mode']}", show_lines=False)
    table.add_column("scope", style="cyan")
    table.add_column("used", justify="right")
    table.add_column("limit", justify="right")
    table.add_column("remaining", justify="right")
    for scope in ("daily", "weekly", "monthly"):
        item = status[scope]
        table.add_row(
            scope,
            f"${item['used_usd']:.4f}",
            "-" if item["limit_usd"] is None else f"${item['limit_usd']:.4f}",
            "-" if item["remaining_usd"] is None else f"${item['remaining_usd']:.4f}",
        )
    console.print(table)


@budget_app.command("set")
def budget_set_cmd(
    period: str = typer.Argument(..., help="daily | weekly | monthly"),
    amount: float = typer.Argument(...),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    key = {
        "daily": "daily_budget_usd",
        "weekly": "weekly_budget_usd",
        "monthly": "monthly_budget_usd",
    }.get(period.strip().lower())
    if key is None:
        console.print("[red]Periodo invalido:[/red] use daily, weekly ou monthly")
        raise typer.Exit(code=1)
    profile = BudgetManager(root=state_dir).set_profile(**{key: amount})
    console.print(f"[green]budget set[/green] {period}=${amount:.4f} mode={profile.mode}")


@autonomy_app.command("set")
def autonomy_set_cmd(
    mode: str = typer.Argument(..., help="manual | supervised | autonomous | locked"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    normalized = mode.strip().lower()
    if normalized not in AUTONOMY_MODES:
        console.print("[red]Modo invalido:[/red] manual, supervised, autonomous ou locked")
        raise typer.Exit(code=1)
    BudgetManager(root=state_dir).set_profile(mode=normalized)
    console.print(f"[green]autonomy[/green] mode={normalized}")


def _continuous_manager(config_path: Path, state_dir: Path):
    from ..config_loader import ContinuousAutonomySection, load_config
    from ..continuous_autonomy import ContinuousAutonomy

    try:
        config = load_config(config_path).continuous_autonomy
    except Exception as exc:  # noqa: BLE001 - status must remain available
        console.print(f"[yellow]Configuração não carregada:[/yellow] {exc}")
        config = ContinuousAutonomySection()
    return ContinuousAutonomy(root=state_dir, config=config)


@continuous_app.command("status")
def continuous_status_cmd(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    import json

    console.print(json.dumps(_continuous_manager(config, state_dir).status(), ensure_ascii=False, indent=2))


@continuous_app.command("start")
def continuous_start_cmd(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
    foreground: bool = typer.Option(False, "--foreground/--detach"),
) -> None:
    manager = _continuous_manager(config, state_dir)
    if not bool(getattr(manager.config, "enabled", False)):
        console.print("[yellow]Autonomia contínua está desabilitada em continuous_autonomy.enabled.[/yellow]")
        raise typer.Exit(code=2)
    if not any(target.enabled for target in manager._targets):
        console.print("[yellow]Nenhum alvo habilitado em continuous_autonomy.targets.[/yellow]")
        raise typer.Exit(code=2)
    if not foreground:
        command = [sys.executable, "-m", "bauer.cli", "autonomy", "continuous", "start",
                   "--foreground", "--config", str(config), "--state-dir", str(state_dir)]
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)
        console.print("[green]Autonomia contínua iniciada em segundo plano.[/green]")
        return
    try:
        manager.start()
        console.print("[green]Observando alvos configurados. Ctrl+C para parar.[/green]")
        while manager.status()["state"]["state"] in {"starting", "running"}:
            time.sleep(0.5)
    except KeyboardInterrupt:
        manager.stop()
    finally:
        manager.close()


@continuous_app.command("stop")
def continuous_stop_cmd(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    manager = _continuous_manager(config, state_dir)
    console.print(manager.stop()["state"])


@continuous_app.command("delegate")
def continuous_delegate_cmd(
    kind: str = typer.Argument(..., help="application | bauer_improvement"),
    message: str = typer.Argument(...),
    workspace: Path = typer.Option(Path.cwd(), "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
) -> None:
    from ..continuous_autonomy import DelegationRecord, ContinuousAutonomy, create_worktree_for_delegation

    if kind not in {"application", "bauer_improvement"}:
        console.print("[red]Tipo inválido:[/red] application ou bauer_improvement")
        raise typer.Exit(code=1)
    delegation_id = f"delegation-{int(time.time())}"
    isolated, branch = create_worktree_for_delegation(workspace, delegation_id)
    run = subprocess.run([sys.executable, "-m", "bauer.cli", "run", message], cwd=isolated)
    manager = ContinuousAutonomy(root=Path("memory/runtime"), config=None)
    manager.record_delegation(DelegationRecord(
        id=delegation_id, kind=kind, message=message, run_id=f"cli-{delegation_id}",
        workspace=str(isolated), branch=branch, status="completed" if run.returncode == 0 else "failed",
    ))
    console.print(f"[yellow]Branch isolada para revisão:[/yellow] {branch}")
    raise typer.Exit(code=run.returncode)
