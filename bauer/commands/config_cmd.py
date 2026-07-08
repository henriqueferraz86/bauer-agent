"""Comando bauer config."""

from __future__ import annotations

from ..config_loader import ConfigError
from rich.panel import Panel
from pathlib import Path
from rich.table import Table
from ..config_loader import load_config
import typer
from ..config_loader import validate_config_file

from ._common import console

config_app = typer.Typer(help="Operacoes com config.yaml")


@config_app.command("validate")
def config_validate(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
):
    """Valida o config.yaml sem rodar diagnostico."""
    ok, msg = validate_config_file(config)
    if ok:
        console.print(f"[green]{msg}[/green]")
    else:
        console.print(f"[red]{msg}[/red]")
        raise typer.Exit(code=2)


@config_app.command("show")
def config_show(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    raw: bool = typer.Option(False, "--raw", help="Dump cru do dict validado"),
):
    """Dashboard da configuração: paths, providers, modelo, gateway, MCP."""
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)
    if raw:
        console.print(cfg.model_dump())
        return

    from bauer.config_admin import get_config_path, get_env_path
    from bauer.provider_profile import env_var_status, get_profile

    console.print(Panel.fit("⚙  Configuração do Bauer", style="cyan"))

    # ── Paths ──
    paths = Table(show_header=False, box=None, padding=(0, 1))
    paths.add_row("config.yaml:", f"[dim]{get_config_path(config)}[/dim]")
    paths.add_row(".env:", f"[dim]{get_env_path()}[/dim]")
    paths.add_row("workspace:", f"[dim]{Path(cfg.agent.workspace).resolve()}[/dim]")
    console.print(Panel(paths, title="◆ Paths", border_style="cyan", title_align="left"))

    # ── Modelo ativo ──
    model_tbl = Table(show_header=False, box=None, padding=(0, 1))
    prof = get_profile(cfg.model.provider)
    free = " 🆓" if prof and prof.is_free else ""
    model_tbl.add_row("Provider:", f"[cyan]{cfg.model.provider}[/cyan]{free}")
    model_tbl.add_row("Modelo:", f"[cyan]{cfg.model.name}[/cyan]")
    model_tbl.add_row("Contexto solicitado:", str(cfg.model.requested_context))
    model_tbl.add_row("Contexto mínimo:", str(cfg.model.minimum_context))
    console.print(Panel(model_tbl, title="◆ Modelo", border_style="cyan", title_align="left"))

    # ── Providers & API keys (✓/○ por env var) ──
    prov_tbl = Table(box=None, padding=(0, 1))
    prov_tbl.add_column("Provider", style="cyan")
    prov_tbl.add_column("Env var")
    prov_tbl.add_column("Status")
    for row in env_var_status():
        ok = row["set"]
        mark = "[green]✓ configurado[/green]" if ok else "[dim]○ não definido[/dim]"
        prov_tbl.add_row(row["display_name"], row["env_var"], mark)
    console.print(Panel(prov_tbl, title="◆ Providers & API Keys", border_style="cyan", title_align="left"))

    # ── Gateway / canais ──
    gw = Table(show_header=False, box=None, padding=(0, 1))
    gw.add_row("Telegram:", "[green]habilitado[/green]" if cfg.telegram.enabled else "[dim]desabilitado[/dim]")
    gw.add_row("Discord:", "[green]habilitado[/green]" if cfg.discord.enabled else "[dim]desabilitado[/dim]")
    gw.add_row("Outbox drain:", f"{cfg.gateway.outbox_drain_interval_s}s")
    console.print(Panel(gw, title="◆ Gateway", border_style="cyan", title_align="left"))

    # ── MCP servers ──
    servers = getattr(cfg.mcp, "servers", []) or []
    if servers:
        mcp_tbl = Table(box=None, padding=(0, 1))
        mcp_tbl.add_column("Nome", style="cyan")
        mcp_tbl.add_column("Tipo/Alvo")
        for s in servers:
            target = getattr(s, "url", None) or getattr(s, "command", "?")
            mcp_tbl.add_row(getattr(s, "name", "?"), str(target))
        console.print(Panel(mcp_tbl, title="◆ MCP Servers", border_style="cyan", title_align="left"))

    console.print(
        "[dim]bauer config set <chave> <valor>   ·   bauer config check   ·   "
        "bauer config edit[/dim]"
    )


@config_app.command("path", help="Mostra o caminho absoluto do config.yaml")
def config_path_cmd(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
):
    from bauer.config_admin import get_config_path
    console.print(str(get_config_path(config)))


@config_app.command("env-path", help="Mostra o caminho absoluto do .env")
def config_env_path_cmd():
    from bauer.config_admin import get_env_path
    console.print(str(get_env_path()))


@config_app.command("get", help="Lê um valor (chave pontilhada ou env var)")
def config_get_cmd(
    key: str = typer.Argument(..., help="Ex: model.name, runtime.safety_margin_mb, GROQ_API_KEY"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
):
    from bauer.config_admin import get_config_value, is_env_key, redact_secret

    value = get_config_value(key, config)
    if value is None:
        console.print(f"[dim](não definido: {key})[/dim]")
        raise typer.Exit(code=1)
    if is_env_key(key):
        console.print(redact_secret(str(value)))
    else:
        console.print(str(value))


@config_app.command("set", help="Define um valor — segredos vão pro .env, resto pro config.yaml")
def config_set_cmd(
    key: str = typer.Argument(..., help="Ex: model.name qwen2.5:7b  |  GROQ_API_KEY gsk_..."),
    value: str = typer.Argument(..., help="Valor (bool/int/float são convertidos)"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
):
    from bauer.config_admin import set_config_value

    try:
        dest, path = set_config_value(key, value, config)
    except KeyError as exc:
        console.print(f"[red]Chave inválida:[/red] {exc}")
        raise typer.Exit(code=2)
    where = ".env" if dest == "env" else "config.yaml"
    shown = "•••" if dest == "env" else value
    console.print(f"[green]✓[/green] {key} = {shown} → [dim]{path}[/dim] ({where})")
    if dest == "config":
        ok, msg = validate_config_file(config)
        if not ok:
            console.print(f"[yellow]⚠ config.yaml agora não valida:[/yellow] {msg}")


@config_app.command("unset", help="Remove um valor do config.yaml ou .env")
def config_unset_cmd(
    key: str = typer.Argument(...),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
):
    from bauer.config_admin import unset_config_value

    dest, removed = unset_config_value(key, config)
    if removed:
        console.print(f"[green]✓[/green] removido {key} ({'.env' if dest == 'env' else 'config.yaml'})")
    else:
        console.print(f"[dim]nada para remover: {key}[/dim]")


@config_app.command("edit", help="Abre o config.yaml no editor ($EDITOR/$VISUAL)")
def config_edit_cmd(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
):
    import subprocess

    from bauer.config_admin import find_editor

    if not config.exists():
        console.print(f"[red]config.yaml não existe:[/red] {config}")
        raise typer.Exit(code=1)
    editor = find_editor()
    if not editor:
        console.print(f"Nenhum editor encontrado. Edite manualmente: [dim]{config.resolve()}[/dim]")
        raise typer.Exit(code=1)
    console.print(f"Abrindo {config} em [cyan]{editor}[/cyan]…")
    subprocess.run([editor, str(config)])
    ok, msg = validate_config_file(config)
    color = "green" if ok else "red"
    console.print(f"[{color}]{msg}[/{color}]")


@config_app.command("check", help="Verifica env vars de providers (configuradas/faltando)")
def config_check_cmd(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
):
    from bauer.config_admin import env_status_rows

    # Carrega o .env primeiro para o status refletir o arquivo (não só o
    # ambiente do shell) — uma invocação fresca da CLI não tem o .env no env.
    try:
        from bauer.env_loader import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass

    rows = env_status_rows()
    if not rows:
        console.print("[yellow]Não consegui ler os profiles de provider.[/yellow]")
        raise typer.Exit(code=1)

    configured = [r for r in rows if r["set"]]
    missing = [r for r in rows if not r["set"]]

    table = Table(title="📋 Status de configuração — providers", show_lines=False)
    table.add_column("Provider", style="cyan")
    table.add_column("Env var")
    table.add_column("Status")
    for r in configured:
        table.add_row(r["display_name"], r["env_var"], "[green]✓ configurado[/green]")
    for r in missing:
        table.add_row(r["display_name"], r["env_var"], "[dim]○ não definido[/dim]")
    console.print(table)
    console.print(
        f"\n[green]{len(configured)}[/green] configurada(s), "
        f"[dim]{len(missing)} disponível(is) sem chave[/dim]. "
        "Defina com: [bold]bauer config set <ENV_VAR> <valor>[/bold]"
    )

    # Higiene: secrets_scanner aponta chaves coladas no config.yaml
    try:
        from bauer.config_admin import get_config_path
        from bauer.secrets_scanner import scan
        cfg_text = get_config_path(config).read_text(encoding="utf-8")
        result = scan(cfg_text, redact=False)
        if result.found:
            console.print(
                f"\n[yellow]⚠ {len(result.matches)} possível segredo embutido no "
                "config.yaml[/yellow] — mova para o .env com `bauer config set`."
            )
    except Exception:  # noqa: BLE001
        pass


@config_app.command("profile")
def config_profile(
    action: str = typer.Argument(..., help="Ação: create | list | use | delete"),
    profile: str = typer.Argument("", help="Nome do perfil (para create/use/delete)"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Gerencia perfis de configuração (dev/staging/prod).

    Exemplos:
      bauer config profile list
      bauer config profile create dev
      bauer config profile use dev
      bauer config profile delete dev
    """
    from ..config_profiles import (
        create_profile, delete_profile, get_active_profile,
        list_profiles, profile_path, set_active_profile,
    )

    if action == "list":
        profiles = list_profiles(config)
        active = get_active_profile()
        if not profiles:
            console.print("[dim]Nenhum perfil encontrado.[/dim]")
            return
        for p in profiles:
            marker = " [green]← ativo[/green]" if p == active else ""
            pp = profile_path(p, config)
            console.print(f"  [cyan]{p}[/cyan]{marker}  [dim]{pp}[/dim]")

    elif action == "create":
        if not profile:
            console.print("[red]Especifique o nome do perfil: bauer config profile create <nome>[/red]")
            raise typer.Exit(1)
        try:
            p = create_profile(profile, config, overwrite=force)
            console.print(f"[green]Perfil '{profile}' criado em {p}[/green]")
        except FileExistsError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)

    elif action == "use":
        if not profile:
            console.print("[red]Especifique o perfil: bauer config profile use <nome>[/red]")
            raise typer.Exit(1)
        if not profile_path(profile, config).exists():
            console.print(f"[red]Perfil '{profile}' não encontrado. Use 'bauer config profile create {profile}' primeiro.[/red]")
            raise typer.Exit(1)
        set_active_profile(profile)
        console.print(f"[green]Perfil ativo: {profile}[/green]")

    elif action == "delete":
        if not profile:
            console.print("[red]Especifique o perfil a remover.[/red]")
            raise typer.Exit(1)
        removed = delete_profile(profile, config)
        if removed:
            if get_active_profile() == profile:
                set_active_profile(None)
            console.print(f"[green]Perfil '{profile}' removido.[/green]")
        else:
            console.print(f"[yellow]Perfil '{profile}' não encontrado.[/yellow]")

    else:
        console.print(f"[red]Ação desconhecida: {action}. Use: create | list | use | delete[/red]")
        raise typer.Exit(1)


@config_app.command("diff")
def config_diff_cmd(
    profile_a: str = typer.Argument(..., help="Perfil ou caminho A (ex: 'dev' ou 'config.dev.yaml')"),
    profile_b: str = typer.Argument("", help="Perfil ou caminho B (default: config.yaml)"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
):
    """Exibe diff entre dois arquivos de config."""
    from ..config_profiles import config_diff, profile_path

    def _resolve(name: str) -> Path:
        p = Path(name)
        if p.exists():
            return p
        pp = profile_path(name, config)
        return pp

    path_a = _resolve(profile_a)
    path_b = _resolve(profile_b) if profile_b else config

    diff_lines = config_diff(path_a, path_b)
    if not diff_lines:
        console.print("[green]Arquivos idênticos.[/green]")
        return

    for line in diff_lines:
        if line.startswith("+") and not line.startswith("+++"):
            console.print(f"[green]{line}[/green]")
        elif line.startswith("-") and not line.startswith("---"):
            console.print(f"[red]{line}[/red]")
        elif line.startswith("@@"):
            console.print(f"[cyan]{line}[/cyan]")
        else:
            console.print(line)


@config_app.command("migrate")
def config_migrate_cmd(
    migration: str = typer.Argument("", help="Chave da migração (vazio = lista disponíveis)"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    apply: bool = typer.Option(False, "--apply", help="Aplica a migração (default: dry-run)"),
):
    """Executa migração de versão do config.yaml."""
    from ..config_profiles import list_migrations, run_migration

    if not migration:
        migrations = list_migrations()
        if not migrations:
            console.print("[dim]Nenhuma migração disponível.[/dim]")
            return
        for m in migrations:
            console.print(f"  [cyan]{m['key']}[/cyan]  {m['description']}")
        return

    changes = run_migration(migration, config_path=config, dry_run=not apply)
    for change in changes:
        console.print(f"  {'[green]Aplicado[/green]' if apply else '[yellow]Dry-run[/yellow]'}: {change}")

    if not apply:
        console.print("\n[dim]Use --apply para aplicar as mudanças.[/dim]")


@config_app.command("validate-full")
def config_validate_full(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
):
    """Validação aprofundada do config com Pydantic + verificações extras."""
    from ..config_profiles import validate_config

    errors = validate_config(config_path=config)
    if not errors:
        console.print(f"[green]✓ Config válido: {config}[/green]")
    else:
        console.print(f"[red]✗ {len(errors)} erro(s) encontrado(s):[/red]")
        for err in errors:
            console.print(f"  [red]• {err}[/red]")
        raise typer.Exit(code=2)
