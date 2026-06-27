"""Comando bauer plugin."""

from __future__ import annotations

from pathlib import Path
from rich.table import Table
import typer

from ._common import _WORKSPACE_DIR, console

plugin_app = typer.Typer(help="Plugin manager — instala e lista plugins Bauer")


@plugin_app.command("list")
def plugin_list(
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
):
    """Lista plugins instalados (mostra versão e manifest quando disponível)."""
    from ..plugin_registry import PluginRegistry

    plugins = PluginRegistry(workspace).list_plugins()
    if not plugins:
        console.print("[dim]Nenhum plugin encontrado em workspace/.bauer/plugins ou ~/.bauer/plugins.[/dim]")
        console.print("[dim]Instale com: bauer plugin install <url>[/dim]")
        return
    table = Table(title="Plugins Bauer", show_lines=False)
    table.add_column("Plugin", style="cyan")
    table.add_column("Versão", style="dim")
    table.add_column("Enabled")
    table.add_column("Hooks")
    table.add_column("Manifest")
    table.add_column("Descrição")
    for p in plugins:
        table.add_row(
            p.name,
            p.version or "-",
            "[green]sim[/green]" if p.enabled else "[red]não[/red]",
            ", ".join(p.hooks) or "-",
            "[green]✓[/green]" if p.has_manifest else "[dim]-[/dim]",
            p.description or p.error or "-",
        )
    console.print(table)


@plugin_app.command("install")
def plugin_install(
    url: str = typer.Argument(..., help="URL para o arquivo .py do plugin (http/https)"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    force: bool = typer.Option(False, "--force", "-f", help="Sobrescreve se já instalado"),
):
    """Baixa e instala um plugin Bauer a partir de uma URL.

    Exemplo:
        bauer plugin install https://raw.githubusercontent.com/user/repo/main/my_plugin.py

    O Bauer também tenta baixar plugin.yaml adjacente (mesmo diretório na URL),
    que enriquece os metadados com versão, autor e hooks declarativos.
    """
    from ..plugin_registry import PluginRegistry, install_plugin

    reg = PluginRegistry(workspace)
    dest_dir = reg.install_dir()
    plugin_name = url.split("?")[0].rstrip("/").split("/")[-1].replace(".py", "")
    dest_file = dest_dir / f"{plugin_name}.py"

    if dest_file.exists() and not force:
        console.print(f"[yellow]Plugin '{plugin_name}' já instalado.[/yellow]")
        console.print("Use --force para sobrescrever.")
        raise typer.Exit(1)

    console.print(f"[dim]Instalando plugin de:[/dim] {url}")
    try:
        py_path, manifest_path = install_plugin(url, dest_dir)
    except ValueError as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(1) from exc
    except Exception as exc:
        console.print(f"[red]Erro ao baixar:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(f"[green]✓[/green] Plugin instalado: {py_path.name}")
    if manifest_path:
        console.print(f"[green]✓[/green] Manifest baixado: {manifest_path.name}")

    # Inspeciona e exibe informações do plugin
    info = reg._inspect(py_path)
    if info.error:
        console.print(f"[yellow]Aviso:[/yellow] plugin instalado mas com erro de parse: {info.error}")
    else:
        console.print(f"   Hooks:   {', '.join(info.hooks) or '(nenhum detectado)'}")
        if info.version:
            console.print(f"   Versão:  {info.version}")
        if info.description:
            console.print(f"   Descrição: {info.description}")


@plugin_app.command("remove")
def plugin_remove(
    name: str = typer.Argument(..., help="Nome do plugin (sem extensão .py)"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirma sem perguntar"),
):
    """Remove um plugin instalado (apaga .py e plugin.yaml se existirem)."""
    from ..plugin_registry import PluginRegistry

    reg = PluginRegistry(workspace)
    dest_dir = reg.install_dir()
    py_file = dest_dir / f"{name}.py"
    yaml_file = dest_dir / f"{name}.yaml"

    if not py_file.exists():
        console.print(f"[red]Plugin '{name}' não encontrado em {dest_dir}[/red]")
        raise typer.Exit(1)

    if not yes:
        confirm = typer.confirm(f"Remover plugin '{name}'?", default=False)
        if not confirm:
            console.print("[dim]Operação cancelada.[/dim]")
            raise typer.Exit(0)

    py_file.unlink()
    if yaml_file.exists():
        yaml_file.unlink()
    console.print(f"[green]✓[/green] Plugin '{name}' removido.")
