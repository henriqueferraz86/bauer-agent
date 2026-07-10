"""Formal skills registry commands."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import typer
from rich.table import Table

from ._common import console
from ..core.skills import SkillMarketplace, SkillMarketplaceError, SkillRegistry

skills_app = typer.Typer(help="Skill Registry formal: manifestos, capacidades e permissoes.")


@skills_app.command("insights")
def skills_insights_cmd(
    last: str = typer.Option("7d", "--last", help="Janela: 24h, 7d, 2w, 30d."),
    suggest_new: bool = typer.Option(False, "--suggest-new", help="Mostra candidatas a novas skills."),
    fmt: str = typer.Option("table", "--format", help="table | json"),
    state_dir: Path = typer.Option(Path("memory/runtime"), "--state-dir"),
) -> None:
    """Analisa uso, falhas, lentidao e sequencias repetidas de skills/tools."""
    from dataclasses import asdict
    from ..core.audit import build_skill_insights

    insights = build_skill_insights(
        state_dir,
        since=_parse_last(last),
        window_label=last,
        suggest_new=suggest_new,
    )
    if fmt == "json":
        console.file.write(json.dumps(asdict(insights), ensure_ascii=False, indent=2) + "\n")
        console.file.flush()
        return

    console.print(f"[bold]Skill Insights[/bold] - ultimos {last}")
    _print_skill_metrics("Mais usadas", insights.most_used, lambda item: f"{item.uses} usos")
    _print_skill_metrics(
        "Maior taxa de falha",
        insights.highest_failure_rate,
        lambda item: f"{item.failure_rate * 100:.1f}% ({item.failures}/{item.uses})",
    )
    _print_skill_metrics(
        "Mais lentas",
        insights.slowest,
        lambda item: f"{item.average_duration_ms or 0:.1f}ms",
    )
    if insights.repeated_sequences:
        console.print("\n[bold]Sequencias repetidas[/bold]")
        for item in insights.repeated_sequences:
            console.print(f"  {item.occurrences:>3}  {' -> '.join(item.tools)}")
    if insights.never_used:
        console.print("\n[bold]Nunca usadas[/bold]")
        for skill_id in insights.never_used:
            console.print(f"  - {skill_id}")
    if suggest_new and insights.suggestions:
        console.print("\n[bold]Sugestoes (exigem aprovacao humana)[/bold]")
        for item in insights.suggestions:
            console.print(f"  {item.suggested_id}: {item.reason}")


def _parse_last(value: str) -> datetime:
    match = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", value.lower())
    if not match:
        raise typer.BadParameter("Use formatos como 24h, 7d, 2w.")
    amount, unit = int(match.group(1)), match.group(2)
    delta = {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
        "w": timedelta(weeks=amount),
    }[unit]
    return datetime.now() - delta


def _print_skill_metrics(title, items, render) -> None:
    if not items:
        return
    console.print(f"\n[bold]{title}[/bold]")
    for item in items:
        console.print(f"  {item.skill_id}: {render(item)}")


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
