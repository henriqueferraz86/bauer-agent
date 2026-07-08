"""Comando bauer models."""

from __future__ import annotations

from ..model_registry import ModelRegistryError
from rich.panel import Panel
from pathlib import Path
from rich.table import Table
from ..model_registry import load_registry
import typer

from ._common import console
from ._runtime import _build_client, _load_or_die

models_app = typer.Typer(help="Operacoes com models.yaml")


@models_app.command("test")
def models_test(
    model_name: str = typer.Argument(..., help="Nome do modelo (ex: qwen2.5-coder:7b)"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
):
    """Testa um modelo específico: disponibilidade, RAM, contexto e tool mode."""
    cfg, reg = _load_or_die(config, models)
    from ..machine_id import machine_summary
    from ..model_registry import contexto_seguro

    machine = machine_summary()
    ram_available = int(machine["ram_available_mb"])
    client = _build_client(cfg)

    alive, _ = client.is_alive()
    available = alive and client.has_model(model_name)
    info = reg.get(model_name)

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_row("Modelo:", f"[cyan]{model_name}[/cyan]")
    table.add_row("Ollama:", "[green]ativo[/green]" if alive else "[red]offline[/red]")
    table.add_row(
        "Disponivel:",
        "[green]sim[/green]" if available else f"[red]nao[/red] — rode: ollama pull {model_name}",
    )

    if info:
        safe_ctx = contexto_seguro(info, ram_available, cfg.runtime.safety_margin_mb)
        ram_ok = ram_available >= info.ram_base_mb + cfg.runtime.safety_margin_mb
        table.add_row("RAM necessaria:", f"~{info.ram_base_mb} MB")
        table.add_row("RAM disponivel:", f"{ram_available} MB")
        table.add_row(
            "RAM suficiente:",
            "[green]sim[/green]" if ram_ok else f"[red]nao[/red] — faltam {info.ram_base_mb - ram_available + cfg.runtime.safety_margin_mb} MB",
        )
        table.add_row("Contexto solicitado:", str(cfg.model.requested_context))
        table.add_row("Contexto seguro:", str(safe_ctx) if safe_ctx > 0 else "[red]0 — nao cabe na RAM[/red]")
        table.add_row("Tool mode:", "native" if info.supports_tools is True else "bridge")
        table.add_row("Profile:", info.ram_profile)
    else:
        table.add_row("[yellow]Aviso:[/yellow]", f"'{model_name}' nao esta em models.yaml — adicione para calculo de RAM.")

    modelfile_ctx = None
    if available:
        try:
            params = client.show_model(model_name)
            modelfile_ctx = params.num_ctx
            if modelfile_ctx:
                table.add_row("Modelfile num_ctx:", str(modelfile_ctx))
        except Exception:
            pass

    status = "pronto" if available and (info is None or contexto_seguro(info, ram_available, cfg.runtime.safety_margin_mb) > 0) else "nao pronto"
    color = "green" if status == "pronto" else "red"
    table.add_row("Status:", f"[{color}]{status}[/{color}]")

    console.print(Panel(table, title=f"bauer models test — {model_name}", border_style=color))


@models_app.command("list")
def models_list(
    models: Path = typer.Option(Path("models.yaml"), "--models", help="Caminho do models.yaml"),
):
    """Lista os modelos do models.yaml com seus perfis e contextos seguros."""
    try:
        reg = load_registry(models)
    except ModelRegistryError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)

    table = Table(title="Modelos conhecidos (models.yaml)")
    table.add_column("nome", style="cyan")
    table.add_column("provider")
    table.add_column("ram_base_mb", justify="right")
    table.add_column("ram_per_1k_ctx_mb", justify="right")
    table.add_column("max_context_safe", justify="right")
    table.add_column("tools")
    table.add_column("profile")

    for name in reg.names():
        info = reg.models[name]
        table.add_row(
            name,
            info.provider,
            str(info.ram_base_mb),
            f"{info.ram_per_1k_ctx_mb:g}",
            str(info.max_context_safe),
            str(info.supports_tools),
            info.ram_profile,
        )
    console.print(table)


@models_app.command("catalog")
def models_catalog(
    provider: str = typer.Option("", "--provider", "-p", help="Filtrar por provider (ex: openai, anthropic)"),
    capability: str = typer.Option("", "--capability", "-c", help="Filtrar por capability (tools, vision, reasoning)"),
    max_cost: float = typer.Option(0.0, "--max-cost", help="Custo máximo USD/M tokens de input (0 = sem filtro)"),
    limit: int = typer.Option(20, "--limit", "-n", help="Número máximo de modelos a exibir"),
):
    """Lista modelos do catálogo dinâmico models.dev com filtros."""
    try:
        from ..models_dev import catalog_models
    except ImportError:
        console.print("[red]models_dev não disponível.[/red]")
        raise typer.Exit(code=1)

    prov_filter = provider.strip() or None
    cap_filter = capability.strip() or None
    cost_filter = max_cost if max_cost > 0 else None

    results = catalog_models(
        provider=prov_filter,
        capability=cap_filter,
        max_cost_per_m=cost_filter,
    )

    if not results:
        console.print("[yellow]Nenhum modelo encontrado com os filtros aplicados.[/yellow]")
        console.print("[dim]Dica: tente sem filtros ou com filtros menos restritivos.[/dim]")
        return

    results = results[:limit]

    table = Table(title=f"Catálogo models.dev ({len(results)} modelos)")
    table.add_column("provider", style="cyan", no_wrap=True)
    table.add_column("modelo", style="bold")
    table.add_column("ctx", justify="right")
    table.add_column("$in/M", justify="right")
    table.add_column("capabilities")

    for m in results:
        ctx = f"{m['context_window']:,}" if m["context_window"] else "—"
        cost_in = f"${m['cost_in']:.2f}" if m["cost_in"] else "—"
        caps = " ".join(f"[green]{c}[/green]" for c in m["capabilities"]) or "[dim]—[/dim]"
        table.add_row(m["provider"], m["id"], ctx, cost_in, caps)

    console.print(table)
    if len(results) == limit:
        console.print(f"[dim]Mostrando {limit} de muitos resultados. Use --limit N para mais.[/dim]")


@models_app.command("set-fallbacks")
def models_set_fallbacks(
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra os modelos sem salvar no config."),
    config_path: "Path | None" = typer.Option(None, "--config", help="Caminho alternativo para config.yaml"),
):
    """Preenche fallback_models no config.yaml com todos os modelos gratuitos disponíveis.

    Consulta o catálogo models.dev + OpenRouter + lista curada (Groq, GitHub)
    e salva todos os pares (provider, name) em model.fallback_models.
    O modelo primário configurado é automaticamente excluído da lista.
    """
    try:
        from ..models_dev import free_models_for_fallback
    except ImportError:
        console.print("[red]models_dev não disponível.[/red]")
        raise typer.Exit(code=1)

    # Resolve config path
    from ..paths import config_path as _cfg_path_fn
    cfg_path = config_path or _cfg_path_fn()
    if not cfg_path.exists():
        console.print(f"[red]Config não encontrado: {cfg_path}[/red]")
        raise typer.Exit(code=1)

    # Lê config atual para saber o modelo primário (evitar duplicata no fallback)
    import yaml as _yaml
    raw = _yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    primary_provider = (raw.get("model") or {}).get("provider", "")
    primary_name = (raw.get("model") or {}).get("name", "")

    console.print("[bold]Consultando catálogo de modelos gratuitos…[/bold] [dim](pode levar alguns segundos)[/dim]")

    with console.status("[cyan]Baixando catálogo…[/cyan]"):
        free_list = free_models_for_fallback(
            skip_provider=primary_provider,
            skip_name=primary_name,
        )

    if not free_list:
        console.print("[yellow]Nenhum modelo gratuito encontrado no catálogo.[/yellow]")
        raise typer.Exit(code=1)

    # Exibe prévia
    table = Table(title=f"{len(free_list)} modelos gratuitos para fallback")
    table.add_column("provider", style="cyan", no_wrap=True)
    table.add_column("modelo", style="bold")
    for m in free_list:
        table.add_row(m["provider"], m["name"])
    console.print(table)

    if dry_run:
        console.print("[yellow]--dry-run: nada salvo.[/yellow]")
        return

    # Salva no config.yaml
    raw.setdefault("model", {})
    raw["model"]["fallback_models"] = free_list
    # Remove campo legado se presente
    raw["model"].pop("fallback_providers", None)
    cfg_path.write_text(_yaml.dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")

    console.print(f"\n[green]✓[/green] {len(free_list)} modelos salvos em [bold]{cfg_path}[/bold]")
    console.print("[dim]O switch automático usará essa lista quando o modelo primário falhar.[/dim]")
