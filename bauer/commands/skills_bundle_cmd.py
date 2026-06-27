"""Comando bauer skills_bundle."""

from __future__ import annotations

import typer

from ._common import console

skills_bundle_app = typer.Typer(help="Skill bundles — grupos de skills sob um único nome.")


@skills_bundle_app.command("list")
def bundle_list_cmd() -> None:
    """Lista bundles salvos em ~/.bauer/skill-bundles/."""
    from ..skill_bundles import get_default_bundle_manager
    from rich.table import Table

    mgr = get_default_bundle_manager()
    bundles = mgr.list_bundles()
    if not bundles:
        console.print("[yellow]Nenhum bundle criado. Use 'bauer skills-bundle new'.[/yellow]")
        raise typer.Exit()

    table = Table(title="Skill Bundles", show_lines=False, box=None)
    table.add_column("Slug", style="cyan", no_wrap=True)
    table.add_column("Nome", style="bold")
    table.add_column("Skills")
    table.add_column("Descrição")
    for b in bundles:
        table.add_row(b.slug, b.name, ", ".join(b.skills), b.description)
    console.print(table)


@skills_bundle_app.command("new")
def bundle_new_cmd(
    name: str = typer.Argument(..., help="Nome do bundle"),
    skills: list[str] = typer.Option([], "--skill", "-s", help="Skill slug (repetível)"),
    description: str = typer.Option("", "--desc", "-d", help="Descrição do bundle"),
    instruction: str = typer.Option("", "--instruction", "-i", help="Instrução extra"),
) -> None:
    """Cria um novo skill bundle."""
    from ..skill_bundles import SkillBundle, get_default_bundle_manager

    if not skills:
        console.print("[red]Informe ao menos uma skill com --skill <slug>[/red]")
        raise typer.Exit(1)

    mgr = get_default_bundle_manager()
    bundle = SkillBundle(
        name=name,
        description=description,
        skills=list(skills),
        instruction=instruction,
    )
    path = mgr.save(bundle)
    console.print(f"[green]✓[/green] Bundle '{bundle.slug}' salvo em {path}")
    console.print(f"  Skills: {', '.join(skills)}")


@skills_bundle_app.command("delete")
def bundle_delete_cmd(
    name: str = typer.Argument(..., help="Nome ou slug do bundle"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirmar sem perguntar"),
) -> None:
    """Remove um skill bundle."""
    from ..skill_bundles import get_default_bundle_manager

    mgr = get_default_bundle_manager()
    if not yes:
        typer.confirm(f"Remover bundle '{name}'?", abort=True)
    ok = mgr.delete(name)
    if ok:
        console.print(f"[green]✓[/green] Bundle '{name}' removido.")
    else:
        console.print(f"[red]Bundle '{name}' não encontrado.[/red]")
        raise typer.Exit(1)


@skills_bundle_app.command("show")
def bundle_show_cmd(
    name: str = typer.Argument(..., help="Nome ou slug do bundle"),
) -> None:
    """Mostra as skills de um bundle e seu conteúdo combinado."""
    from ..skill_bundles import get_default_bundle_manager

    mgr = get_default_bundle_manager()
    bundle = mgr.get(name)
    if bundle is None:
        console.print(f"[red]Bundle '{name}' não encontrado.[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]{bundle.name}[/bold]  [dim]{bundle.slug}[/dim]")
    if bundle.description:
        console.print(f"[dim]{bundle.description}[/dim]")
    console.print(f"Skills: [cyan]{', '.join(bundle.skills)}[/cyan]")
    if bundle.instruction:
        console.print(f"\nInstrução:\n{bundle.instruction}")
