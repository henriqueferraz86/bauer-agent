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
    from ..plugin_manager import PluginManager
    from ..plugin_registry import PluginRegistry

    managed = PluginManager().list_plugins()
    plugins = PluginRegistry(workspace).list_plugins()
    if not plugins and not managed:
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
    for p in managed:
        table.add_row(
            p.manifest.id,
            p.manifest.version,
            "[green]sim[/green]" if p.enabled else "[red]não[/red]",
            ", ".join(p.manifest.permissions) or "-",
            "[green]✓[/green] gerenciado",
            p.error or p.manifest.name,
        )
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


@plugin_app.command("search")
def plugin_search(
    query: str = typer.Argument(..., help="Texto para buscar por id, nome ou capability"),
):
    """Busca plugins instalados no registry local."""
    from ..plugin_manager import PluginManager

    matches = PluginManager().search(query)
    if not matches:
        console.print(f"[dim]Nenhum plugin local corresponde a: {query}[/dim]")
        return
    for plugin in matches:
        state = "enabled" if plugin.enabled else "disabled"
        console.print(f"{plugin.manifest.id}\t{plugin.manifest.version}\t{state}\t{plugin.manifest.name}")


@plugin_app.command("info")
def plugin_info(
    plugin_id: str = typer.Argument(..., help="ID do plugin"),
):
    """Exibe manifesto e estado de um plugin gerenciado."""
    from ..plugin_manager import PluginManager, PluginManagerError

    try:
        plugin = PluginManager().get(plugin_id)
    except PluginManagerError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(plugin.to_dict())


@plugin_app.command("install")
def plugin_install(
    url: str = typer.Argument(..., help="Diretório/arquivo local, URL Git ou URL .py"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    force: bool = typer.Option(False, "--force", "-f", help="Sobrescreve se já instalado"),
):
    """Baixa e instala um plugin Bauer a partir de uma URL.

    Exemplo:
        bauer plugin install https://raw.githubusercontent.com/user/repo/main/my_plugin.py

    O Bauer também tenta baixar plugin.yaml adjacente (mesmo diretório na URL),
    que enriquece os metadados com versão, autor e hooks declarativos.
    """
    from ..plugin_manager import PluginManager, PluginManagerError
    from ..plugin_registry import PluginRegistry, install_plugin

    source_path = Path(url).expanduser()
    managed_source = source_path.exists() or url.endswith(".git") or url.startswith("git@")
    if managed_source:
        try:
            plugin = PluginManager().install(url, force=force)
        except PluginManagerError as exc:
            console.print(f"[red]Erro:[/red] {exc}")
            raise typer.Exit(1) from exc
        console.print(f"[green]✓[/green] Plugin gerenciado instalado: {plugin.manifest.id} v{plugin.manifest.version}")
        return

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


@plugin_app.command("enable")
def plugin_enable(plugin_id: str = typer.Argument(..., help="ID do plugin")):
    """Valida e habilita um plugin gerenciado."""
    from ..plugin_manager import PluginManager, PluginManagerError

    try:
        plugin = PluginManager().enable(plugin_id)
    except PluginManagerError as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Plugin habilitado:[/green] {plugin.manifest.id}")


@plugin_app.command("disable")
def plugin_disable(plugin_id: str = typer.Argument(..., help="ID do plugin")):
    """Desabilita um plugin gerenciado sem removê-lo."""
    from ..plugin_manager import PluginManager, PluginManagerError

    try:
        plugin = PluginManager().disable(plugin_id)
    except PluginManagerError as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[yellow]Plugin desabilitado:[/yellow] {plugin.manifest.id}")


@plugin_app.command("uninstall")
def plugin_uninstall(
    plugin_id: str = typer.Argument(..., help="ID do plugin"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirma sem perguntar"),
):
    """Remove um plugin gerenciado e sua entrada do registry."""
    from ..plugin_manager import PluginManager, PluginManagerError

    if not yes and not typer.confirm(f"Remover plugin gerenciado '{plugin_id}'?", default=False):
        console.print("[dim]Operação cancelada.[/dim]")
        return
    try:
        PluginManager().uninstall(plugin_id)
    except PluginManagerError as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Plugin removido:[/green] {plugin_id}")


@plugin_app.command("update")
def plugin_update(
    plugin_id: str = typer.Argument(..., help="ID do plugin a atualizar"),
    source: str = typer.Argument(..., help="Diretório, arquivo local ou URL Git da nova versão"),
):
    """Atualiza um plugin preservando a versão anterior."""
    from ..plugin_manager import PluginManager, PluginManagerError

    try:
        plugin = PluginManager().update(source)
    except PluginManagerError as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(1) from exc
    if plugin.manifest.id != plugin_id.strip().lower():
        console.print(
            f"[red]Erro:[/red] source pertence a '{plugin.manifest.id}', "
            f"não a '{plugin_id}'."
        )
        raise typer.Exit(1)
    console.print(f"[green]Plugin atualizado:[/green] {plugin.manifest.id} v{plugin.manifest.version}")


@plugin_app.command("rollback")
def plugin_rollback(plugin_id: str = typer.Argument(..., help="ID do plugin")):
    """Restaura a versão anterior conhecida do plugin."""
    from ..plugin_manager import PluginManager, PluginManagerError

    try:
        plugin = PluginManager().rollback(plugin_id)
    except PluginManagerError as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Rollback concluído:[/green] {plugin.manifest.id} v{plugin.manifest.version}")


@plugin_app.command("reload")
def plugin_reload(plugin_id: str = typer.Argument(..., help="ID do plugin")):
    """Valida e prepara a versão ativa para o próximo ciclo de carga."""
    from ..plugin_manager import PluginManager, PluginManagerError

    try:
        plugin = PluginManager().reload(plugin_id)
    except PluginManagerError as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]Plugin validado para reload:[/green] {plugin.manifest.id} v{plugin.manifest.version}")


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
