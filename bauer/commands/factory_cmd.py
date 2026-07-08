"""Comando bauer factory."""

from __future__ import annotations

import typer

from ._common import console

factory_app = typer.Typer(
    help="App Factory — Spec-Driven Development: transforma uma ideia em V1 com gates obrigatórios"
)


@factory_app.command("init")
def factory_init(
    idea: str = typer.Argument(..., help="Descrição da ideia/aplicação"),
    stack: str = typer.Option("", "--stack", "-s", help="Stack preferida (ex: FastAPI+React)"),
    path: str = typer.Option(".", "--path", "-p", help="Diretório do projeto"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Sobrescrever docs existentes"),
):
    """Inicia a governança da App Factory e cria os esqueletos dos docs."""
    from pathlib import Path as _P
    from .. import app_factory as af

    project = _P(path).resolve()
    project.mkdir(parents=True, exist_ok=True)
    ok, why = af.guard_reinit(project, idea=idea, overwrite=overwrite)
    if not ok:
        console.print(f"[red]Bloqueado:[/red] {why}")
        raise typer.Exit(code=1)
    res = af.init_project(project, idea=idea, stack=stack, overwrite=overwrite)
    console.print(f"[bold green]App Factory iniciada[/bold green] em [cyan]{project}[/cyan]")
    console.print(f"  Gate atual: [yellow]{res['gate']}[/yellow]")
    console.print(f"  {len(res['written'])} arquivo(s) criado(s).")
    console.print(
        "\n[dim]Próximo passo:[/dim] preencha os 7 docs em [cyan]docs/[/cyan] "
        "(comece por SPEC.md). A escrita de código fica bloqueada até o planejamento "
        "estar completo. Acompanhe com [bold]bauer factory status[/bold]."
    )


@factory_app.command("status")
def factory_status(
    path: str = typer.Option(".", "--path", "-p", help="Diretório do projeto"),
):
    """Mostra o gate atual, docs pendentes e o Delivery Score parcial."""
    from pathlib import Path as _P
    from .. import app_factory as af

    project = _P(path).resolve()
    st = af.status(project)
    if not st["governed"]:
        console.print("[yellow]Projeto não está sob governança da App Factory.[/yellow]")
        console.print("Use [bold]bauer factory init \"<ideia>\"[/bold] para iniciar.")
        raise typer.Exit(code=1)
    console.print(f"[bold]Gate:[/bold] [cyan]{st['gate']}[/cyan]")
    console.print(f"  Planejamento completo: {'sim' if st['planning_complete'] else 'não'}")
    if st["missing_planning_docs"]:
        console.print(f"  [yellow]Docs pendentes:[/yellow] {', '.join(st['missing_planning_docs'])}")
    sc = st.get("delivery_score") or {}
    if sc:
        console.print(f"  Delivery Score parcial: [bold]{sc.get('score')}/10[/bold] "
                      f"({sc.get('satisfied')}/{sc.get('total')} itens)")


@factory_app.command("score")
def factory_score(
    path: str = typer.Option(".", "--path", "-p", help="Diretório do projeto"),
):
    """Calcula o Delivery Score objetivo (0–10) da V1."""
    from pathlib import Path as _P
    from .. import app_factory as af

    project = _P(path).resolve()
    if not af.is_governed(project):
        console.print("[yellow]Projeto não governado — sem score.[/yellow]")
        raise typer.Exit(code=1)
    sc = af.delivery_score(project)
    status_txt = "[green]PRONTO[/green]" if sc["ready"] else "[yellow]NÃO pronto[/yellow]"
    console.print(f"[bold]Delivery Score:[/bold] {sc['score']}/10 — {status_txt} para V1")
    for item, ok in sc["checks"].items():
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
        console.print(f"  {mark} {item}")


@factory_app.command("gate")
def factory_gate(
    path: str = typer.Option(".", "--path", "-p", help="Diretório do projeto"),
):
    """Mostra o gate atual e o que falta para avançar."""
    from pathlib import Path as _P
    from .. import app_factory as af

    project = _P(path).resolve()
    gate = af.current_gate(project)
    if gate is None:
        console.print("[yellow]Projeto não governado.[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[bold]Gate atual:[/bold] [cyan]{gate.slug}[/cyan]")
    if gate < af.Gate.IMPLEMENTATION:
        missing = af.missing_planning_docs(project)
        console.print("  [yellow]Escrita de código bloqueada.[/yellow] "
                      f"Faltam: {', '.join(missing) or '(preencher SPEC)'}")
    else:
        console.print("  [green]Escrita de código liberada.[/green]")
