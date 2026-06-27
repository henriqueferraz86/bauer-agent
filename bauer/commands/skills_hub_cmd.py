"""Comando bauer skills_hub."""

from __future__ import annotations

import typer

from ._common import console

skills_hub_app = typer.Typer(help="Skills Hub — catálogo built-in de skills curadas.")


@skills_hub_app.command("list")
def hub_list_cmd(
    category: str | None = typer.Option(None, "--category", "-c", help="Filtrar por categoria"),
) -> None:
    """Lista todas as skills disponíveis no hub built-in."""
    from ..skills_hub import get_default_hub
    from rich.table import Table

    hub = get_default_hub()
    skills = hub.list_skills(category=category)
    if not skills:
        console.print("[yellow]Nenhuma skill encontrada.[/yellow]")
        raise typer.Exit()

    table = Table(title=f"Skills Hub ({len(skills)} skills)", show_lines=False, box=None)
    table.add_column("Slug", style="cyan", no_wrap=True)
    table.add_column("Nome", style="bold")
    table.add_column("Categoria", style="dim")
    table.add_column("Descrição")
    for s in skills:
        table.add_row(s.slug, s.name, s.category, s.description)
    console.print(table)


@skills_hub_app.command("search")
def hub_search_cmd(
    query: str = typer.Argument(..., help="Termos de busca"),
) -> None:
    """Busca skills no hub por nome e descrição."""
    from ..skills_hub import get_default_hub
    from rich.table import Table

    hub = get_default_hub()
    results = hub.search(query)
    if not results:
        console.print("[yellow]Nenhuma skill encontrada.[/yellow]")
        raise typer.Exit()

    table = Table(title=f"Resultados para '{query}'", show_lines=False, box=None)
    table.add_column("Slug", style="cyan", no_wrap=True)
    table.add_column("Categoria", style="dim")
    table.add_column("Descrição")
    for s in results:
        table.add_row(s.slug, s.category, s.description)
    console.print(table)


@skills_hub_app.command("install")
def hub_install_cmd(
    slug: str = typer.Argument(..., help="Slug da skill a instalar"),
) -> None:
    """Instala uma skill do hub em ~/.bauer/skills/."""
    from ..skills_hub import get_default_hub

    hub = get_default_hub()
    ok = hub.install(slug)
    if ok:
        console.print(f"[green]✓[/green] Skill '{slug}' instalada em ~/.bauer/skills/")
    else:
        console.print(f"[red]Skill '{slug}' não encontrada no hub.[/red]")
        raise typer.Exit(1)


@skills_hub_app.command("show")
def hub_show_cmd(
    slug: str = typer.Argument(..., help="Slug da skill"),
) -> None:
    """Exibe o conteúdo de uma skill do hub."""
    from ..skills_hub import get_default_hub

    hub = get_default_hub()
    content = hub.read_content(slug)
    if content is None:
        console.print(f"[red]Skill '{slug}' não encontrada.[/red]")
        raise typer.Exit(1)
    console.print(content)


@skills_hub_app.command("categories")
def hub_categories_cmd() -> None:
    """Lista as categorias de skills disponíveis."""
    from ..skills_hub import get_default_hub

    hub = get_default_hub()
    cats = hub.categories()
    if not cats:
        console.print("[yellow]Nenhuma categoria encontrada.[/yellow]")
        raise typer.Exit()
    for c in cats:
        console.print(f"  [cyan]{c}[/cyan]")
