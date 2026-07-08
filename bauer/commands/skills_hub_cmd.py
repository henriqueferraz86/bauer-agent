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


@skills_hub_app.command("stats")
def hub_stats_cmd() -> None:
    """Telemetria de uso das skills (Nível 1: só observação, nenhuma ação).

    Mostra quantas vezes cada skill disparou e a cara do desfecho + 👍/👎.
    AVISO: sinal fraco por turno; NÃO julgue skill por poucos usos nem por
    taxa de sucesso crua (skills disparam em tarefas mais difíceis).
    """
    import time as _t
    from rich.table import Table
    from ..skill_stats import load_stats

    stats = load_stats()
    if not stats:
        console.print(
            "[dim]Nenhum uso de skill registrado ainda. Rode o [bold]bauer agent[/bold] "
            "e faça pedidos que casem com skills — os números aparecem aqui.[/dim]"
        )
        raise typer.Exit()

    table = Table(title=f"Uso de skills ({len(stats)})", show_lines=False, box=None)
    table.add_column("Skill", style="cyan", no_wrap=True)
    table.add_column("Usos", justify="right")
    table.add_column("bom", style="green", justify="right")
    table.add_column("ruim", style="red", justify="right")
    table.add_column("neutro", style="dim", justify="right")
    table.add_column("👍/👎", justify="right")
    table.add_column("último uso", style="dim")
    now = _t.time()
    for name, r in sorted(stats.items(), key=lambda kv: kv[1].get("uses", 0), reverse=True):
        last = r.get("last_used", 0) or 0
        ago = "—" if not last else f"{int((now-last)/3600)}h" if now-last < 86400 else f"{int((now-last)/86400)}d"
        table.add_row(
            name, str(r.get("uses", 0)),
            str(r.get("good", 0)), str(r.get("bad", 0)), str(r.get("neutral", 0)),
            f"{r.get('thumbs_up', 0)}/{r.get('thumbs_down', 0)}", ago,
        )
    console.print(table)
    console.print(
        "[dim]Sinal fraco por turno (a skill é 1 fator entre vários). Só o agregado "
        "sobre muitos usos significa algo — e skills disparam em tarefas mais difíceis, "
        "então taxa de sucesso baixa ≠ skill ruim.[/dim]"
    )


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
