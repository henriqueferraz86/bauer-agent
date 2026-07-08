"""Formal skills registry commands."""

from __future__ import annotations

import json

import typer
from rich.table import Table

from ._common import console
from ..core.skills import SkillRegistry

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
