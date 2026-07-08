"""Formal skills registry commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.table import Table

from ._common import console
from ..core.skills import SkillMarketplace, SkillMarketplaceError, SkillRegistry

skills_app = typer.Typer(help="Skill Registry formal: manifestos, capacidades e permissoes.")


@skills_app.command("validate")
def skills_validate_cmd() -> None:
    registry = SkillRegistry()
    valid, errors = registry.validate_all()
    if errors:
        for error in errors:
            console.print(f"[red]erro[/red] {error}")
        raise typer.Exit(code=1)
    console.print(f"[green]ok[/green] {len(valid)} skills com manifesto valido")


@skills_app.command("inspect")
def skills_inspect_cmd(skill_id: str = typer.Argument(...)) -> None:
    registry = SkillRegistry()
    manifest = registry.get(skill_id)
    if manifest is None:
        console.print(f"[red]Skill nao encontrada:[/red] {skill_id}")
        raise typer.Exit(code=1)
    console.print(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2))


@skills_app.command("capabilities")
def skills_capabilities_cmd() -> None:
    registry = SkillRegistry()
    table = Table(title="Skill Capabilities", show_lines=False)
    table.add_column("Capability", style="cyan")
    table.add_column("Skills")
    for capability, skill_ids in registry.capabilities().items():
        table.add_row(capability, ", ".join(skill_ids))
    console.print(table)


@skills_app.command("find")
def skills_find_cmd(capability: str = typer.Argument(...)) -> None:
    registry = SkillRegistry()
    matches = registry.find_by_capability(capability)
    if not matches:
        console.print(f"[yellow]Nenhuma skill com capability:[/yellow] {capability}")
        raise typer.Exit(code=1)
    table = Table(title=f"Skills for {capability}", show_lines=False)
    table.add_column("ID", style="cyan")
    table.add_column("Risk")
    table.add_column("Permissions")
    table.add_column("Description")
    for manifest in matches:
        table.add_row(manifest.id, manifest.risk, ", ".join(manifest.permissions), manifest.description)
    console.print(table)


@skills_app.command("package")
def skills_package_cmd(package_dir: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Inspeciona um skill-package local e calcula hash."""
    try:
        info = SkillMarketplace().package(package_dir)
    except Exception as exc:
        console.print(f"[red]skill package invalido:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(json.dumps(info.to_dict(), ensure_ascii=False, indent=2), soft_wrap=True)


@skills_app.command("install")
def skills_install_cmd(
    package_dir: Path = typer.Argument(..., exists=True, file_okay=False),
    yes: bool = typer.Option(False, "--yes", "-y", help="Aprova a instalacao e permissoes exibidas"),
    force: bool = typer.Option(False, "--force", "-f", help="Sobrescreve skill instalada"),
) -> None:
    """Instala skill-package local no marketplace do Bauer."""
    market = SkillMarketplace()
    try:
        preview = market.package(package_dir)
    except Exception as exc:
        console.print(f"[red]skill package invalido:[/red] {exc}")
        raise typer.Exit(code=1)

    table = Table(title=f"Install Skill {preview.id}", show_lines=False)
    table.add_column("Field", style="cyan")
    table.add_column("Value")
    table.add_row("Version", preview.version)
    table.add_row("Risk", preview.risk)
    table.add_row("Permissions", ", ".join(preview.permissions))
    table.add_row("Hash", preview.package_hash)
    console.print(table)
    if not yes:
        console.print("[yellow]Instalacao requer aprovacao explicita: rode novamente com --yes[/yellow]")
        raise typer.Exit(code=1)
    try:
        installed = market.install(package_dir, yes=True, force=force)
    except (SkillMarketplaceError, ValueError) as exc:
        console.print(f"[red]erro ao instalar:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]installed[/green] {installed.id} {installed.version}")


@skills_app.command("uninstall")
def skills_uninstall_cmd(skill_id: str = typer.Argument(...)) -> None:
    """Remove skill instalada localmente."""
    try:
        removed = SkillMarketplace().uninstall(skill_id)
    except SkillMarketplaceError as exc:
        console.print(f"[red]erro ao remover:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]uninstalled[/green] {removed.id}")


@skills_app.command("index")
def skills_index_cmd() -> None:
    """Mostra indice local de skills instaladas."""
    index = SkillMarketplace().index()
    console.print(json.dumps(index, ensure_ascii=False, indent=2), soft_wrap=True)
