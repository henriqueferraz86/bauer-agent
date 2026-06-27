"""Comando bauer migrate."""

from __future__ import annotations

from rich.console import Console
from pathlib import Path
from rich.table import Table
import typer

from ._common import console

migrate_app = typer.Typer(help="Importa configuracoes e dados de outros agents (Hermes, OpenClaw)")


def _print_migration_result(result, console: Console) -> None:  # type: ignore[type-arg]
    """Exibe o resultado de uma migração com Rich."""
    from rich.rule import Rule

    prefix = "[dim][dry-run][/dim] " if result.dry_run else ""
    console.print()
    console.print(Rule(f"{prefix}Migração: [bold cyan]{result.source}[/bold cyan]"))

    if result.actions:
        console.print(f"\n[bold green]✓ Ações ({len(result.actions)}):[/bold green]")
        for a in result.actions:
            console.print(f"  [green]•[/green] {a}")

    if result.warnings:
        console.print(f"\n[bold yellow]⚠ Avisos ({len(result.warnings)}):[/bold yellow]")
        for w in result.warnings:
            console.print(f"  [yellow]•[/yellow] {w}")

    if result.errors:
        console.print(f"\n[bold red]✗ Erros ({len(result.errors)}):[/bold red]")
        for e in result.errors:
            console.print(f"  [red]•[/red] {e}")

    console.print()
    if result.ok:
        if result.dry_run:
            console.print(
                "[dim]Modo dry-run — nenhuma alteração foi feita. "
                "Execute sem [bold]--dry-run[/bold] para aplicar.[/dim]"
            )
        else:
            console.print("[green]Migração concluída com sucesso.[/green]")
    else:
        console.print("[red]Migração encerrada com erros.[/red]")
        raise typer.Exit(code=1)


@migrate_app.command("hermes")
def migrate_hermes(
    hermes_dir: Path = typer.Option(
        None, "--hermes-dir", "-d",
        help="Diretório do Hermes Agent (padrão: ~/.hermes)",
    ),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    workspace: Path = typer.Option(Path("workspace"), "--workspace"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Apenas mostra o que seria feito"),
    no_config: bool = typer.Option(False, "--no-config", help="Pula migração de config.yaml e API keys"),
    no_history: bool = typer.Option(False, "--no-history", help="Pula importação do histórico"),
    no_agents: bool = typer.Option(False, "--no-agents", help="Pula criação de agent"),
    no_memory: bool = typer.Option(False, "--no-memory", help="Pula cópia de arquivos de memória (.md, sessões JSONL)"),
):
    """Importa configuracoes e historico do Hermes Agent para o Bauer.

    Migra:
      - config.yaml  (provider, modelo, host Ollama)
      - API keys do config.yaml e .env do Hermes → .env do Bauer
      - Historico de conversas → memory/sessions/hermes-*.jsonl
      - Toolsets → agent 'hermes-default' em agents.yaml
      - Arquivos de memória (MODEL_EXPERIENCE.md, SKILLS_LEARNED.md…)
      - Sessões JSONL do memory/sessions/ do Hermes
    """
    from ..migrate import HermesMigrator

    migrator = HermesMigrator(
        hermes_dir=hermes_dir,
        bauer_config=config,
        bauer_memory=workspace.parent / "memory",
        bauer_agents=agents_file,
    )

    # Mostra resumo do que foi encontrado
    summary = migrator.source_summary()
    if not summary.get("found"):
        console.print(
            f"[red]Hermes não encontrado em {migrator.hermes_dir}[/red]\n"
            f"[dim]Use --hermes-dir para especificar o caminho.[/dim]"
        )
        raise typer.Exit(code=1)

    table = Table(title="Hermes Agent — Dados encontrados", show_lines=False, box=None)
    table.add_column("Campo", style="cyan")
    table.add_column("Valor")
    table.add_row("Diretório", str(migrator.hermes_dir))
    table.add_row("Provider", summary.get("provider", "?"))
    table.add_row("Modelo", summary.get("model", "?"))
    table.add_row("Toolsets", ", ".join(summary.get("toolsets", [])) or "—")
    table.add_row("Providers extras", str(summary.get("provider_count", 0)))
    table.add_row("API key no config", "[green]sim[/green]" if summary.get("has_api_key") else "[dim]não[/dim]")
    table.add_row(".env encontrado", "[green]sim[/green]" if summary.get("has_env") else "[dim]não[/dim]")
    table.add_row("Sessões de histórico", str(summary.get("session_count", 0)))
    table.add_row("Mensagens no histórico", str(summary.get("total_messages", 0)))
    table.add_row("Sessões JSONL (memory/)", str(summary.get("jsonl_session_count", 0)))
    memory_files = summary.get("memory_files", [])
    table.add_row("Arquivos de memória", ", ".join(memory_files) or "—")
    console.print(table)
    console.print()

    if not dry_run:
        if not typer.confirm("Prosseguir com a migração?", default=True):
            console.print("[dim]Cancelado.[/dim]")
            return

    result = migrator.migrate(
        dry_run=dry_run,
        import_config=not no_config,
        import_history=not no_history,
        import_agents=not no_agents,
        import_memory=not no_memory,
    )
    _print_migration_result(result, console)


@migrate_app.command("openclaw")
def migrate_openclaw(
    settings: Path = typer.Option(
        None, "--settings", "-s",
        help="Caminho do settings.json do OpenClaw (padrão: ~/.openclaw/claw3d/settings.json)",
    ),
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    workspace: Path = typer.Option(Path("workspace"), "--workspace"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Apenas mostra o que seria feito"),
    no_config: bool = typer.Option(False, "--no-config", help="Pula migração de config.yaml"),
    no_auth: bool = typer.Option(False, "--no-auth", help="Pula importação de tokens"),
    no_tasks: bool = typer.Option(False, "--no-tasks", help="Pula importação de tasks"),
):
    """Importa perfis de conexao e tasks do OpenClaw para o Bauer.

    Migra:
      - Gateway profiles (URL + token) → ~/.bauer/auth.json
      - Provider ativo → config.yaml
      - Task board cards → workspace/TASKS.md
    """
    from ..migrate import OpenClawMigrator

    migrator = OpenClawMigrator(
        settings_path=settings,
        bauer_config=config,
        bauer_workspace=workspace,
    )

    # Mostra resumo
    summary = migrator.source_summary()
    if not summary.get("found"):
        console.print(
            f"[red]OpenClaw não encontrado em {migrator.settings_path}[/red]\n"
            f"[dim]Use --settings para especificar o caminho.[/dim]"
        )
        raise typer.Exit(code=1)

    table = Table(title="OpenClaw — Dados encontrados", show_lines=False, box=None)
    table.add_column("Campo", style="cyan")
    table.add_column("Valor")
    table.add_row("Settings", str(migrator.settings_path))
    table.add_row("Adapter ativo", summary.get("active_adapter", "?"))
    table.add_row("Floor ativo", summary.get("active_floor", "?"))
    table.add_row("Gateway profiles", str(summary.get("profile_count", 0)))
    table.add_row("Profiles", ", ".join(summary.get("profiles", [])) or "—")
    table.add_row("Task cards", str(summary.get("task_card_count", 0)))
    console.print(table)
    console.print()

    if not dry_run:
        if not typer.confirm("Prosseguir com a migração?", default=True):
            console.print("[dim]Cancelado.[/dim]")
            return

    result = migrator.migrate(
        dry_run=dry_run,
        import_config=not no_config,
        import_auth=not no_auth,
        import_tasks=not no_tasks,
    )
    _print_migration_result(result, console)


@migrate_app.callback(invoke_without_command=True)
def migrate_info(ctx: typer.Context) -> None:
    """Importa configuracoes e dados de outros agents para o Bauer."""
    if ctx.invoked_subcommand is not None:
        return

    from ..migrate import HermesMigrator, OpenClawMigrator

    table = Table(title="Fontes de migração disponíveis", show_lines=True)
    table.add_column("Fonte",    style="cyan", no_wrap=True)
    table.add_column("Comando")
    table.add_column("Status")
    table.add_column("O que importa")

    hm = HermesMigrator()
    h_found = hm.detect()
    h_status = "[green]encontrado[/green]" if h_found else "[dim]não encontrado[/dim]"
    table.add_row(
        "Hermes Agent",
        "bauer migrate hermes",
        h_status,
        "config, histórico de sessões, toolsets → agent",
    )

    oc = OpenClawMigrator()
    o_found = oc.detect()
    o_status = "[green]encontrado[/green]" if o_found else "[dim]não encontrado[/dim]"
    table.add_row(
        "OpenClaw",
        "bauer migrate openclaw",
        o_status,
        "gateway profiles → auth tokens, task board → TASKS.md",
    )

    console.print(table)
    console.print()
    console.print("[dim]Use [bold]--dry-run[/bold] para simular sem alterar nada.[/dim]")
