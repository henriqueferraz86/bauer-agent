"""Comando bauer spec."""

from __future__ import annotations

from pathlib import Path
import typer

from ._common import _SPECS_DIR, console

spec_app = typer.Typer(help="Spec-Driven Development — contratos de features em YAML")


@spec_app.command("new")
def spec_new(
    spec_id: str = typer.Argument("", help="ID do spec — omita para modo entrevista"),
    specs_dir: Path = typer.Option(_SPECS_DIR, "--dir", help="Diretório de specs"),
):
    """Cria um novo spec interativamente (spec-driven development).

    Escreva o CONTRATO (purpose, behavior, ACs) antes de qualquer linha de código.

    Exemplo:
      bauer spec new orchestrator-dag
      bauer spec new
    """
    from ..spec_manager import Spec, SpecManager
    from ..spec_wizard import wizard_create_spec

    mgr = SpecManager(specs_dir)

    if spec_id:
        # ID fornecido — lança o wizard já com o id pre-preenchido
        if not Spec.valid_id(spec_id):
            console.print(f"[red]ID inválido:[/red] '{spec_id}'. Use letras minúsculas, números e hífens.")
            raise typer.Exit(code=1)

    wizard_create_spec(mgr)


@spec_app.command("list")
def spec_list(
    specs_dir: Path = typer.Option(_SPECS_DIR, "--dir"),
    status_filter: str = typer.Option("", "--status", "-s", help="Filtrar por status (draft/approved/implemented/...)"),
):
    """Lista todos os specs do projeto com status e resumo."""
    from ..spec_manager import SpecManager

    mgr = SpecManager(specs_dir)
    specs = mgr.list_specs()

    if not specs:
        console.print("[dim]Nenhum spec encontrado. Crie com: bauer spec new[/dim]")
        return

    if status_filter:
        specs = [s for s in specs if s.status == status_filter]
        if not specs:
            console.print(f"[dim]Nenhum spec com status '{status_filter}'.[/dim]")
            return

    from rich.table import Table
    table = Table(title=f"Specs ({len(specs)})", show_lines=False)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("title")
    table.add_column("status")
    table.add_column("v", style="dim", width=7)
    table.add_column("ACs", style="dim", width=4)
    table.add_column("purpose", style="dim")

    status_colors = {
        "draft": "dim",
        "review": "yellow",
        "approved": "blue",
        "implemented": "green",
        "deprecated": "red",
    }
    for s in specs:
        color = status_colors.get(s.status, "white")
        purpose_preview = s.purpose.split("\n")[0][:60] + ("…" if len(s.purpose) > 60 else "")
        table.add_row(
            s.id,
            s.title,
            f"[{color}]{s.status}[/{color}]",
            s.version,
            str(len(s.acceptance_criteria)),
            purpose_preview,
        )

    console.print(table)


@spec_app.command("show")
def spec_show(
    spec_id: str = typer.Argument(..., help="ID do spec"),
    specs_dir: Path = typer.Option(_SPECS_DIR, "--dir"),
    raw: bool = typer.Option(False, "--raw", help="Exibe YAML bruto"),
):
    """Exibe o spec completo formatado."""
    from ..spec_manager import SpecManager

    mgr = SpecManager(specs_dir)
    spec = mgr.get(spec_id)

    if not spec:
        console.print(f"[yellow]Spec '[cyan]{spec_id}[/cyan]' nao encontrado.[/yellow]")
        if typer.confirm(f"Criar o spec '{spec_id}' agora?", default=True):
            from ..spec_wizard import wizard_create_spec
            created = wizard_create_spec(mgr)
            if created is None:
                raise typer.Exit(code=0)
            spec = created
        else:
            console.print(f"[dim]Crie com: [bold]bauer spec new {spec_id}[/bold][/dim]")
            raise typer.Exit(code=1)

    if raw:
        import yaml
        from rich.syntax import Syntax
        console.print(Syntax(
            yaml.dump(spec.to_dict(), allow_unicode=True, sort_keys=False, default_flow_style=False),
            "yaml", theme="monokai",
        ))
    else:
        from rich.panel import Panel
        console.print(Panel(
            spec.to_context(compact=False),
            title=f"[bold cyan]{spec.id}[/bold cyan]",
            border_style="cyan",
        ))


@spec_app.command("status")
def spec_status_cmd(
    spec_id: str = typer.Argument(..., help="ID do spec"),
    new_status: str = typer.Argument(..., help="draft | review | approved | implemented | deprecated"),
    specs_dir: Path = typer.Option(_SPECS_DIR, "--dir"),
):
    """Atualiza o status de um spec.

    Exemplo:
      bauer spec status orchestrator-dag implemented
    """
    from ..spec_manager import SpecManager, _VALID_STATUSES

    if new_status not in _VALID_STATUSES:
        console.print(f"[red]Status inválido:[/red] '{new_status}'. Válidos: {', '.join(sorted(_VALID_STATUSES))}")
        raise typer.Exit(code=1)

    mgr = SpecManager(specs_dir)
    spec = mgr.get(spec_id)
    if not spec:
        console.print(f"[yellow]Spec '[cyan]{spec_id}[/cyan]' nao encontrado.[/yellow]")
        if typer.confirm(f"Criar o spec '{spec_id}' agora?", default=True):
            from ..spec_wizard import wizard_create_spec
            created = wizard_create_spec(mgr)
            if created is None:
                raise typer.Exit(code=0)
            spec = created
        else:
            raise typer.Exit(code=1)

    spec.status = new_status
    mgr.save(spec)
    console.print(f"[green]✓[/green] Spec [cyan]{spec_id}[/cyan] → status: [bold]{new_status}[/bold]")


@spec_app.command("delete")
def spec_delete(
    spec_id: str = typer.Argument(..., help="ID do spec"),
    specs_dir: Path = typer.Option(_SPECS_DIR, "--dir"),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Remove um spec."""
    from ..spec_manager import SpecManager

    mgr = SpecManager(specs_dir)
    if not mgr.get(spec_id):
        console.print(f"[red]Spec '[cyan]{spec_id}[/cyan]' nao encontrado.[/red]")
        console.print("[dim]Liste os specs: [bold]bauer spec list[/bold][/dim]")
        raise typer.Exit(code=1)

    if not force:
        if not typer.confirm(f"Remover spec '{spec_id}'?", default=False):
            console.print("[dim]Cancelado.[/dim]")
            return

    mgr.delete(spec_id)
    console.print(f"[green]✓[/green] Spec [cyan]{spec_id}[/cyan] removido.")


@spec_app.command("context")
def spec_context(
    query: str = typer.Argument("", help="Query para filtrar specs relevantes"),
    specs_dir: Path = typer.Option(_SPECS_DIR, "--dir"),
    compact: bool = typer.Option(True, "--compact/--full"),
):
    """Exibe o texto de contexto que seria injetado no agente.

    Útil para depurar o que o agente está recebendo como contratos do projeto.
    """
    from ..spec_manager import SpecManager

    mgr = SpecManager(specs_dir)
    ctx = mgr.specs_context(query=query, compact=compact)
    if not ctx:
        console.print("[dim]Nenhum spec aprovado/implementado encontrado.[/dim]")
    else:
        console.print(ctx)
