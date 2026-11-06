"""Comando bauer tools."""

from __future__ import annotations

from ..config_loader import ConfigError
from pathlib import Path
from ..tool_router import SandboxError
from rich.table import Table
from ..tool_router import ToolError
from ..config_loader import load_config
import typer

from ._common import _WORKSPACE_DIR, console
from ._runtime import _build_router

tools_app = typer.Typer(help="Tool Bridge — ferramentas do agente")


@tools_app.command("list")
def tools_list(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
):
    """Lista as tools disponíveis no Tool Bridge."""
    try:
        cfg = load_config(config)
    except ConfigError:
        cfg = None

    router = _build_router(cfg, workspace)

    shell_enabled = cfg and cfg.tools.shell_enabled
    shell_status = (
        f"[green]habilitado[/green] (safe_mode={'on' if cfg and cfg.tools.safe_mode else 'off'})"
        if shell_enabled
        else "[red]desabilitado[/red] (tools.shell_enabled: false)"
    )
    web_status = "[green]habilitado[/green]" if cfg and cfg.tools.web_enabled else "[red]desabilitado[/red]"
    table = Table(title="Tool Bridge — tools disponíveis")
    table.add_column("tool", style="cyan")
    table.add_column("descricao")
    for name in router.available_tools():
        info = router.tool_info(name)
        table.add_row(name, info["description"])
    console.print(table)
    console.print(f"\n[dim]Workspace: {workspace.resolve()} | Shell: {shell_status} | Web: {web_status}[/dim]")


@tools_app.command("plugins")
def tools_plugins(
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
):
    """Lista plugins de hooks descobertos sem importá-los."""
    from ..plugin_registry import PluginRegistry

    plugins = PluginRegistry(workspace).list_plugins()
    if not plugins:
        console.print("[dim]Nenhum plugin encontrado em workspace/.bauer/plugins ou ~/.bauer/plugins.[/dim]")
        return
    table = Table(title="Plugins Bauer", show_lines=False)
    table.add_column("Plugin", style="cyan")
    table.add_column("Enabled")
    table.add_column("Hooks")
    table.add_column("Descricao")
    table.add_column("Erro")
    for plugin in plugins:
        table.add_row(
            plugin.name,
            str(plugin.enabled),
            ", ".join(plugin.hooks) or "-",
            plugin.description,
            plugin.error,
        )
    console.print(table)


@tools_app.command("run")
def tools_run(
    action: str = typer.Argument(
        ...,
        help=(
            "JSON da action ou caminho para arquivo .json.\n\n"
            "Linux/Mac:  bauer tools run '{\"action\":\"list_dir\",\"args\":{\"path\":\".\"}}\'\n"
            "Windows:    Crie um arquivo e passe o caminho (evita problema de quoting):\n"
            "            bauer tools run action.json"
        ),
    ),
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
):
    """Executa uma tool action JSON diretamente (para debug e teste manual).

    Aceita JSON direto ou caminho para arquivo .json.
    No Windows, use um arquivo para evitar problemas de quoting do PowerShell.
    """
    if not workspace.exists():
        console.print(f"[yellow]Workspace '{workspace}' nao existe — criando.[/yellow]")
        workspace.mkdir(parents=True, exist_ok=True)

    # Se o argumento é um arquivo existente, lê o conteúdo.
    action_path = Path(action)
    if action_path.suffix == ".json" and action_path.exists():
        action_json = action_path.read_text(encoding="utf-8-sig").strip()  # utf-8-sig remove BOM do PowerShell
        console.print(f"[dim]Lendo action de {action_path}[/dim]")
    else:
        action_json = action

    try:
        cfg = load_config(config)
    except ConfigError:
        cfg = None

    router = _build_router(cfg, workspace)
    try:
        result = router.execute(action_json)
        console.print(result)
    except SandboxError as exc:
        console.print(f"[red]Sandbox bloqueou:[/red]\n{exc}")
        raise typer.Exit(code=1)
    except ToolError as exc:
        console.print(f"[red]Erro na tool:[/red]\n{exc}")
        raise typer.Exit(code=1)


@tools_app.command("capabilities")
def tools_capabilities(
    search: str = typer.Option("", "--search", "-s", help="Filtra por id, nome ou categoria"),
    state: str = typer.Option("", "--state", help="Filtra por estado (enabled, ready, needs_configuration, ...)"),
    category: str = typer.Option("", "--category", help="Filtra por categoria"),
    as_json: bool = typer.Option(False, "--json", help="Saída JSON"),
    coverage: bool = typer.Option(False, "--coverage", help="Matriz de cobertura do catálogo Agno instalado"),
    markdown: bool = typer.Option(False, "--markdown", help="Com --coverage: tabela Markdown"),
    diff: bool = typer.Option(False, "--diff", help="Diferença do SDK instalado vs. snapshot revisado"),
    write_snapshot: bool = typer.Option(False, "--write-snapshot", help="(dev) grava o snapshot revisado do SDK"),
):
    """Catálogo de capabilities Agno (plano 057): estado, requisitos e cobertura.

    Só lê metadados: não importa conectores, não instala nada e não altera config.
    """
    import json as _json

    from ..core.runtime.agno_capabilities import coverage as _coverage
    from ..core.runtime.agno_capabilities.catalog import catalog_entries

    if write_snapshot:
        console.print(f"snapshot gravado em {_coverage.write_snapshot()}")
        return
    if diff:
        typer.echo(_json.dumps(_coverage.snapshot_diff(), indent=2, ensure_ascii=False))
        return
    if coverage:
        matrix = _coverage.coverage_matrix()
        if markdown:
            typer.echo(_coverage.coverage_markdown(matrix))
        elif as_json:
            typer.echo(_json.dumps(matrix, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Agno {matrix['agno_version']}: "
                       + ", ".join(f"{k}={v}" for k, v in matrix["totals"].items()))
        return

    needle = search.strip().lower()
    rows = [
        row for row in catalog_entries()
        if (not needle or needle in f"{row['id']} {row['name']} {row['category']}".lower())
        and (not state or row["state"] == state)
        and (not category or row["category"].lower() == category.lower())
    ]
    if as_json:
        typer.echo(_json.dumps(rows, indent=2, ensure_ascii=False))
        return
    table = Table(title=f"Capabilities Agno ({len(rows)})")
    table.add_column("id", style="cyan")
    table.add_column("categoria")
    table.add_column("estado")
    table.add_column("ações / motivo")
    for row in rows:
        detail = ", ".join(row["actions"]) or row.get("reason") or ""
        table.add_row(row["id"], row["category"], row["state"], detail[:90])
    console.print(table)
