"""Comando `bauer home` — diretório home do Bauer (~/.bauer/)."""

from __future__ import annotations

import typer
from pathlib import Path

from ._common import console

home_app = typer.Typer(help="Diretório home do Bauer (~/.bauer/)")


@home_app.command("path")
def home_path():
    """Mostra o caminho do diretório home do Bauer (~/.bauer/ ou $BAUER_HOME)."""
    from ..paths import get_bauer_home
    console.print(str(get_bauer_home()))


@home_app.command("status")
def home_status():
    """Mostra o estado atual do diretório home — quais arquivos existem."""
    from ..paths import get_bauer_home, config_path, memory_dir, runtime_state_path, logs_dir, workspace_dir

    bh = get_bauer_home()
    console.print(f"\n[bold cyan]Bauer Home:[/bold cyan] {bh}\n")

    checks = [
        ("config.yaml",          config_path()),
        (".env",                  bh / ".env"),
        (".runtime_state.json",   runtime_state_path()),
        ("memory/",               memory_dir()),
        ("logs/",                 logs_dir()),
        ("workspace/",            workspace_dir()),
    ]
    for label, path in checks:
        exists = path.exists()
        icon = "[green]✓[/green]" if exists else "[dim]·[/dim]"
        console.print(f"  {icon}  {label:<28} [dim]{path}[/dim]")
    console.print()


@home_app.command("migrate")
def home_migrate(
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra o que seria movido sem mover"),
):
    """Migra config.yaml e .runtime_state.json soltos na home do usuário para ~/.bauer/.

    Move apenas se o arquivo de destino ainda não existir (não sobrescreve).
    """
    import shutil
    from ..paths import get_bauer_home, config_path, runtime_state_path

    bh = get_bauer_home()
    user_home = Path.home()

    candidates = [
        (user_home / "config.yaml",          config_path()),
        (user_home / ".runtime_state.json",   runtime_state_path()),
        (user_home / ".env",                  bh / ".env"),
    ]

    moved_any = False
    for src, dst in candidates:
        if not src.exists():
            continue
        if dst.exists():
            console.print(f"[dim]Ignorado (destino já existe):[/dim] {src.name}")
            continue
        console.print(f"[cyan]{'[dry-run] ' if dry_run else ''}Mover:[/cyan] {src}  →  {dst}")
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        moved_any = True

    if not moved_any:
        console.print("[dim]Nada para migrar.[/dim]")
    elif not dry_run:
        console.print("\n[green]Migração concluída.[/green]")
        console.print("[dim]Reinstale os serviços para aplicar o novo working dir:[/dim]")
        console.print("[dim]  bauer gateway service uninstall && bauer gateway service install[/dim]")
        console.print("[dim]  bauer runtime service uninstall && bauer runtime service install[/dim]")
