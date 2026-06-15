"""CLI do Bauer Agent (Typer).

Comandos da Fase 1:
  bauer doctor
  bauer config validate / show
  bauer models list

Comandos da Fase 2:
  bauer chat
"""

from __future__ import annotations

import sys
from pathlib import Path

# Garante UTF-8 no stdout/stderr do Windows (evita UnicodeEncodeError
# com caracteres fora do cp1252 — ex: emojis ou caixa do modelo).
if sys.platform == "win32":
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

import typer
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from .agent import run_agent_session
from .ascii_intro import play_intro
from .model_router import ModelRouter, Route, RouterConfig
from .orchestrator import MAX_STEPS, AgentOrchestrator, OrchestratorConfig
from .chat import run_chat_session
from .config_loader import ConfigError, load_config, validate_config_file
from .logging_config import setup_logging
from .memory_manager import MemoryManager
from .model_registry import ModelRegistryError, load_registry
from .ollama_client import OllamaClient
from .preflight import run_doctor
from .runtime_state import read_state, write_state
from .shell_runner import ShellRunner
from .tool_router import SandboxError, ToolError, ToolRouter
from .workspace_manager import WorkspaceError, WorkspaceManager

app = typer.Typer(
    add_completion=False,
    help="Bauer Agent — runtime adaptativo para LLMs locais.",
)

config_app = typer.Typer(help="Operacoes com config.yaml")
models_app = typer.Typer(help="Operacoes com models.yaml")
memory_app = typer.Typer(help="Operacoes com memoria Markdown")
tools_app = typer.Typer(help="Tool Bridge — ferramentas do agente")
project_app = typer.Typer(help="Gerenciamento de projeto (PROJECT.md)")
task_app = typer.Typer(help="Gerenciamento de tarefas (TASKS.md)")
dispatch_app = typer.Typer(help="Dispatcher hibrido durable para tasks READY")
ops_app = typer.Typer(help="Operacao do runtime: filas, lanes, claims e runs")
runtime_app = typer.Typer(help="Supervisor always-on: dispatcher, cron, outbox e kanban")
cron_app = typer.Typer(help="Automacoes duraveis: agenda prompts como tasks READY")
research_app = typer.Typer(help="Pesquisa e trajectories para avaliacao/treino")
learning_app = typer.Typer(help="Adaptive Learning Engine — recomendacoes e reset")
auth_app = typer.Typer(help="Autenticacao com providers cloud (OAuth/API Key)")
orchestrate_app = typer.Typer(help="Orquestrador de agents — tarefas complexas em varios passos")
agent_app = typer.Typer(
    invoke_without_command=True,
    help="Agente interativo — use sem sub-comando para chat, ou: create/list/run/delete.",
)
spec_app = typer.Typer(help="Spec-Driven Development — contratos de features em YAML")
company_app = typer.Typer(help="Gestao multi-empresa — namespaces isolados por empresa")
migrate_app = typer.Typer(help="Importa configuracoes e dados de outros agents (Hermes, OpenClaw)")
boards_app = typer.Typer(help="Multi-board kanban — cada projeto pode ter seu proprio store SQLite")
daemon_app = typer.Typer(help="BauerDaemon — pool de workers autonomos que processam tasks do kanban")
daemon_service_app = typer.Typer(
    help="Daemon como SERVICO do sistema (systemd/Task Scheduler) — sobe no boot, reinicia em crash"
)
daemon_app.add_typer(daemon_service_app, name="service")

runtime_service_app = typer.Typer(
    help="Runtime como SERVICO do sistema (systemd/Task Scheduler) — sobe no boot, reinicia em crash"
)
runtime_app.add_typer(runtime_service_app, name="service")

serve_app = typer.Typer(
    invoke_without_command=True,
    help="Bauer Agent como servidor HTTP (REST + SSE) — ou 'serve service' para servico do sistema",
)
serve_service_app = typer.Typer(
    help="Serve como SERVICO do sistema (systemd/launchd/Task Scheduler) — sobe no boot, reinicia em crash"
)
serve_app.add_typer(serve_service_app, name="service")

telegram_app = typer.Typer(help="Telegram Bridge — agente Bauer via Telegram")
discord_app = typer.Typer(help="Discord Bridge — agente Bauer via Discord")
gateway_app = typer.Typer(help="Bauer Gateway — todos os canais de chat + entrega do outbox")
gateway_service_app = typer.Typer(
    help="Gateway como SERVIÇO do sistema (systemd/Task Scheduler) — sobe no boot, reinicia em crash"
)
gateway_app.add_typer(gateway_service_app, name="service")

plugin_app = typer.Typer(help="Plugin manager — instala e lista plugins Bauer")
app.add_typer(plugin_app, name="plugin")

app.add_typer(config_app, name="config")
app.add_typer(models_app, name="models")
app.add_typer(memory_app, name="memory")
app.add_typer(tools_app, name="tools")
app.add_typer(project_app, name="project")
app.add_typer(task_app, name="task")
app.add_typer(dispatch_app, name="dispatch")
app.add_typer(ops_app, name="ops")
app.add_typer(runtime_app, name="runtime")
app.add_typer(cron_app, name="cron")
app.add_typer(research_app, name="research")
app.add_typer(learning_app, name="learning")
app.add_typer(auth_app, name="auth")
app.add_typer(orchestrate_app, name="orchestrate")
app.add_typer(agent_app, name="agent")
app.add_typer(spec_app, name="spec")
app.add_typer(company_app, name="company")
app.add_typer(migrate_app, name="migrate")
app.add_typer(boards_app, name="boards")
app.add_typer(daemon_app, name="daemon")
app.add_typer(serve_app, name="serve")
app.add_typer(telegram_app, name="telegram")
app.add_typer(discord_app, name="discord")
app.add_typer(gateway_app, name="gateway")

# legacy_windows=False: usa ANSI codes em vez de Win32 API (suporta Unicode/UTF-8)
console = Console(highlight=False, legacy_windows=False)


# --- helpers ----------------------------------------------------------------


def _load_or_die(config_path: Path, models_path: Path):
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        console.print(f"[red]Erro de config:[/red]\n{exc}")
        raise typer.Exit(code=2)
    # models.yaml ausente → load_registry retorna registry vazio (fresh install);
    # models.yaml inválido → ModelRegistryError vira typer.Exit (erro real do usuário).
    try:
        reg = load_registry(models_path)
    except ModelRegistryError as exc:
        console.print(f"[red]Erro em models.yaml:[/red]\n{exc}")
        raise typer.Exit(code=2)
    return cfg, reg


def _get_or_run_state(cfg, reg, state_file: Path) -> dict:
    """Lê o runtime_state.json; roda o doctor se ausente ou se config relevante mudou.

    Para providers Ollama: re-executa se modelo ou host mudou.
    Para providers cloud (opencode, openai, openrouter…): re-executa apenas se o
    provider mudou — trocar o modelo cloud nao requer re-checagem local.
    """
    from .preflight import _CLOUD_CONTEXT_DEFAULTS, _CLOUD_CONTEXT_FALLBACK

    state = read_state(state_file)
    is_ollama = cfg.model.provider == "ollama"

    # Para cloud, o doctor aplica max(requested_context, padrão_do_provider).
    # A comparação de stale precisa usar esse mesmo valor efetivo — caso contrário
    # o state armazenado (65536) nunca bate com cfg.requested_context (4096) e o
    # doctor re-roda em loop, OU (com a comparação antiga) nunca re-roda quando
    # o provider cloud foi configurado pela primeira vez.
    if is_ollama:
        effective_ctx = cfg.model.requested_context
    else:
        cloud_default = _CLOUD_CONTEXT_DEFAULTS.get(cfg.model.provider, _CLOUD_CONTEXT_FALLBACK)
        effective_ctx = max(cfg.model.requested_context, cloud_default)

    stale = (
        state is None
        or state.get("configured_provider", "ollama") != cfg.model.provider
        or (is_ollama and state.get("configured_model") != cfg.model.name)
        or (is_ollama and state.get("ollama_host") != cfg.ollama.host)
        or state.get("context", {}).get("requested") != effective_ctx
    )
    if stale:
        if state is not None:
            console.print(
                "[yellow]Config mudou — re-executando doctor...[/yellow]"
            )
        report = run_doctor(cfg, reg, state_file)
        write_state(report.state, state_file)
        state = report.state.to_dict()
    return state


# --- comandos ---------------------------------------------------------------


@app.command()
def doctor(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    models: Path = typer.Option(Path("models.yaml"), "--models", help="Caminho do models.yaml"),
    state_file: Path = typer.Option(
        Path(".runtime_state.json"),
        "--state-file",
        help="Onde gravar o runtime_state",
    ),
    check_providers: bool = typer.Option(
        False, "--providers", "-p", help="Verifica conectividade de todos os providers autenticados",
    ),
):
    """Diagnostico completo do ambiente. Gera .runtime_state.json."""
    cfg, reg = _load_or_die(config, models)
    setup_logging(cfg.logging.level, cfg.logging.file)

    report = run_doctor(cfg, reg, state_file)
    path = write_state(report.state, state_file)

    color = {
        "ok": "green",
        "ok_with_adjustments": "yellow",
        "blocked": "red",
    }.get(report.state.status, "white")

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_row("Status:", f"[{color}]{report.state.status}[/{color}]")
    table.add_row("Maquina:", report.state.machine_id)
    table.add_row(
        "Ollama:",
        f"{'ativo' if report.state.ollama_alive else 'offline'} ({report.state.ollama_host})",
    )
    table.add_row(
        "Modelo:",
        f"{report.state.configured_model} ({'encontrado' if report.state.model_available else 'ausente'})",
    )
    table.add_row(
        "Contexto:",
        f"requested={report.state.context.requested} -> applied={report.state.context.applied} ({report.state.context.reason})",
    )
    table.add_row("Tool mode:", report.state.tool_mode)
    table.add_row(
        "RAM:",
        f"{report.state.ram_available_mb} MB disponiveis / {report.state.ram_total_mb} MB totais",
    )
    table.add_row("Profile:", report.state.profile)

    console.print(Panel(table, title="Bauer Doctor", border_style=color))

    if report.findings:
        console.print("\n[bold]Notas:[/bold]")
        for line in report.findings:
            console.print(f"  * {line}")

    console.print(f"\n[dim]Runtime state salvo em {path}[/dim]")

    # --- verificação de providers autenticados -----------------------------------
    if check_providers:
        _doctor_check_providers()

    if report.state.status == "blocked":
        raise typer.Exit(code=1)


def _doctor_check_providers() -> None:
    """Verifica conectividade de todos os providers com token salvo."""
    from .auth import AuthManager
    from .openai_client import OpenAIClient

    # Mapa provider -> (host, chat_path)
    _PROVIDER_HOSTS: dict[str, tuple[str, str]] = {
        "openai":      ("https://api.openai.com",           "/v1/chat/completions"),
        "groq":        ("https://api.groq.com",             "/openai/v1/chat/completions"),
        "mistral":     ("https://api.mistral.ai",           "/v1/chat/completions"),
        "xai":         ("https://api.x.ai",                 "/v1/chat/completions"),
        "together":    ("https://api.together.xyz",         "/v1/chat/completions"),
        "deepseek":    ("https://api.deepseek.com",         "/v1/chat/completions"),
        "openrouter":  ("https://openrouter.ai",            "/api/v1/chat/completions"),
        "github":      ("https://models.inference.ai.azure.com", "/chat/completions"),
        "copilot":     ("https://api.githubcopilot.com",    "/chat/completions"),
        "anthropic":   ("https://api.anthropic.com",        "/v1/messages"),
        "gemini":      ("https://generativelanguage.googleapis.com", "/v1beta/openai/chat/completions"),
    }

    auth = AuthManager()
    prov_table = Table(title="Providers — conectividade", show_lines=True)
    prov_table.add_column("Provider", style="cyan", no_wrap=True)
    prov_table.add_column("Auth", width=8)
    prov_table.add_column("Conectividade")

    found_any = False
    for provider, (host, _) in _PROVIDER_HOSTS.items():
        token = auth.store.load(provider) or auth.store.load(f"{provider}-api")
        has_auth = bool(token)
        if not has_auth:
            continue
        found_any = True
        auth_str = "[green]✓[/green]"
        try:
            client = OpenAIClient(host=host, api_key=token or "", timeout_seconds=10)
            alive, reason = client.is_alive()
            conn_str = "[green]online[/green]" if alive else f"[red]offline — {reason[:60]}[/red]"
        except Exception as exc:
            conn_str = f"[red]erro: {str(exc)[:60]}[/red]"

        prov_table.add_row(provider, auth_str, conn_str)

    if not found_any:
        console.print(
            "\n[dim]Nenhum provider autenticado encontrado. "
            "Use [bold]bauer auth login[/bold] para autenticar.[/dim]"
        )
        return

    console.print()
    console.print(prov_table)


@app.command("init")
def init_cmd(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c", help="Caminho do config.yaml"),
    env: Path = typer.Option(Path(".env"), "--env", help="Caminho do .env para gravar API keys"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Sobrescrever config existente sem confirmacao"),
):
    """Wizard de primeiro uso — configura provider, modelo e workspace interativamente."""
    from .init_wizard import run_init_wizard
    ok = run_init_wizard(
        config_path=config,
        env_path=env,
        force=yes,
    )
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def status(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    state_file: Path = typer.Option(
        Path(".runtime_state.json"),
        "--state-file",
        help="Arquivo de runtime_state",
    ),
):
    """Dashboard de status do Bauer Agent — modelo, provider, contexto, memoria."""
    import json as _json
    import time as _time

    from rich.columns import Columns
    from rich.panel import Panel as RPanel

    # --- Runtime state -----------------------------------------------------------
    runtime_info: dict = {}
    if state_file.exists():
        try:
            runtime_info = _json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # --- Config ------------------------------------------------------------------
    try:
        cfg = load_config(config)
        provider = cfg.model.provider
        model_name = cfg.model.name
    except Exception:
        provider = runtime_info.get("model", {}).get("provider", "?")
        model_name = runtime_info.get("configured_model", "?")

    # --- Sessões ativas ----------------------------------------------------------
    session_count = 0
    try:
        try:
            from .sqlite_session_store import SqliteSessionStore
            ss = SqliteSessionStore()
        except Exception:
            from .session_store import SessionStore
            ss = SessionStore()
        session_count = len(ss.list_sessions())
    except Exception:
        pass

    # --- Memória -----------------------------------------------------------------
    mem_entries = 0
    try:
        from .memory_manager import MemoryManager
        mm = MemoryManager("memory")
        for _, _, entries in mm.list_files():
            mem_entries += entries
    except Exception:
        pass

    # --- Stats de performance ---------------------------------------------------
    perf_summary: str = "n/a"
    try:
        from .memory_manager import MemoryManager as _MM
        _mm2 = _MM("memory")
        _exp_file = _mm2.memory_dir / "MODEL_EXPERIENCE.md"
        if _exp_file.exists():
            _lines = _exp_file.read_text(encoding="utf-8").splitlines()
            _sections = [l for l in _lines if l.startswith("## [")]
            if _sections:
                perf_summary = f"Ultima sessao: {_sections[-1].lstrip('## ').strip()[:60]}"
    except Exception:
        pass

    # --- Auth tokens -------------------------------------------------------------
    auth_providers: list[str] = []
    try:
        from .auth import AuthManager
        auth = AuthManager()
        _ALL_PROVIDERS = [
            "openai", "groq", "mistral", "xai", "together", "deepseek",
            "openrouter", "github", "copilot", "anthropic", "gemini", "azure",
        ]
        for p in _ALL_PROVIDERS:
            if auth.store.load(p) or auth.store.load(f"{p}-api"):
                auth_providers.append(p)
    except Exception:
        pass

    # --- Build display -----------------------------------------------------------
    status_color = "green"
    status_str = "ok"
    if runtime_info.get("status") == "blocked":
        status_color = "red"
        status_str = "blocked"
    elif runtime_info.get("status") == "ok_with_adjustments":
        status_color = "yellow"
        status_str = "ok_with_adjustments"

    model_panel = RPanel(
        f"[bold]{model_name}[/bold]\n"
        f"Provider: [cyan]{provider}[/cyan]\n"
        f"Status: [{status_color}]{status_str}[/{status_color}]\n"
        f"Contexto: {runtime_info.get('context', {}).get('applied', 'n/a')} tokens",
        title="[bold]Modelo[/bold]",
        border_style="cyan",
    )

    auth_panel = RPanel(
        "\n".join(f"[green]✓[/green] {p}" for p in auth_providers) or "[dim]nenhum[/dim]",
        title="[bold]Providers auth[/bold]",
        border_style="cyan",
    )

    mem_panel = RPanel(
        f"Entradas: [bold]{mem_entries}[/bold]\n"
        f"Sessoes: [bold]{session_count}[/bold]\n"
        f"{perf_summary}",
        title="[bold]Memoria & Sessoes[/bold]",
        border_style="cyan",
    )

    console.print(Rule("[bold]Bauer Status[/bold]"))
    console.print(Columns([model_panel, auth_panel, mem_panel], equal=True, expand=True))
    console.print(f"\n[dim]Para diagnostico completo: [bold]bauer doctor --providers[/bold][/dim]")


@app.command()
def model(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
):
    """Seletor interativo de provider e modelo — igual ao 'hermes model'.

    Lista providers (Ollama, OpenRouter, OpenAI, Groq, Custom) e modelos disponíveis.
    Salva a escolha no config.yaml e a API key no .env automaticamente.
    """
    from .model_switcher import run_model_switcher
    run_model_switcher(config)


@app.command()
def chat(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    models: Path = typer.Option(Path("models.yaml"), "--models", help="Caminho do models.yaml"),
    state_file: Path = typer.Option(
        Path(".runtime_state.json"),
        "--state-file",
        help="Runtime state (gerado pelo doctor)",
    ),
    model: str = typer.Option("", "--model", help="Sobrescreve o modelo do config"),
    pick: bool = typer.Option(False, "--pick", help="Mostra lista de modelos para escolher"),
):
    """Chat interativo com modelo local via Ollama."""
    cfg, reg = _load_or_die(config, models)
    setup_logging(cfg.logging.level, cfg.logging.file)

    state = _get_or_run_state(cfg, reg, state_file)

    if not state.get("ollama_alive"):
        console.print(
            "[red]Ollama offline.[/red]\n"
            "Verifique se o Ollama esta rodando e rode [bold]bauer doctor[/bold]."
        )
        raise typer.Exit(code=1)

    client = _build_client(cfg)
    applied_context = state["context"]["applied"]
    is_ollama_chat = cfg.model.provider == "ollama"
    if is_ollama_chat and hasattr(client, "num_ctx"):
        client.num_ctx = applied_context
    if is_ollama_chat and hasattr(client, "think"):
        client.think = cfg.model.think

    if model:
        model_name = model
    elif pick:
        model_name = _pick_model(client, state["configured_model"])
    else:
        model_name = _resolve_model_with_ram_check(
            state["configured_model"], reg, client,
            state["ram_available_mb"], cfg.runtime.safety_margin_mb, _MEMORY_DIR,
        )

    if not client.has_model(model_name):
        console.print(
            f"[red]Modelo '{model_name}' nao encontrado.[/red]\n"
            f"Rode: [bold]ollama pull {model_name}[/bold]"
        )
        raise typer.Exit(code=1)

    run_chat_session(client, model_name, applied_context, console)


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

    from bauer.config_admin import get_config_path, get_env_path, redact_secret
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


@models_app.command("test")
def models_test(
    model_name: str = typer.Argument(..., help="Nome do modelo (ex: qwen2.5-coder:7b)"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
):
    """Testa um modelo específico: disponibilidade, RAM, contexto e tool mode."""
    cfg, reg = _load_or_die(config, models)
    from .machine_id import machine_summary
    from .model_registry import contexto_seguro
    from .ollama_client import OllamaError

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


# --- memory -----------------------------------------------------------------

_MEMORY_DIR = Path("memory")

_FILE_ALIASES = {
    "memory": "MEMORY.md",
    "decisions": "DECISIONS.md",
    "failures": "FAILED_ATTEMPTS.md",
    "experience": "MODEL_EXPERIENCE.md",
    "prefs": "USER_PREFERENCES.md",
    "lessons": "RUNTIME_LESSONS.md",
}


@memory_app.command("init")
def memory_init(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Cria o diretorio memory/ e inicializa os arquivos Markdown."""
    mm = MemoryManager(memory_dir)
    created = mm.init_files()
    if created:
        for p in created:
            console.print(f"[green]criado:[/green] {p}")
    else:
        console.print(f"[dim]Todos os arquivos ja existem em {memory_dir}/[/dim]")


@memory_app.command("list")
def memory_list(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Lista os arquivos de memoria com contagem de entradas."""
    mm = MemoryManager(memory_dir)
    table = Table(title=f"Memoria — {memory_dir}/")
    table.add_column("arquivo", style="cyan")
    table.add_column("linhas", justify="right")
    table.add_column("entradas", justify="right")
    for name, lines, entries in mm.list_files():
        table.add_row(name, str(lines), str(entries))
    console.print(table)


@memory_app.command("show")
def memory_show(
    file: str = typer.Argument(
        "memory",
        help="Arquivo: memory | decisions | failures | experience | prefs | lessons",
    ),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Mostra o conteudo de um arquivo de memoria."""
    mm = MemoryManager(memory_dir)
    filename = _FILE_ALIASES.get(file.lower(), file)
    content = mm.read_file(filename)
    console.print(content)


@memory_app.command("add-decision")
def memory_add_decision(
    title: str = typer.Argument(..., help="Titulo curto da decisao"),
    body: str = typer.Argument(..., help="Descricao da decisao"),
    context: str = typer.Option("", "--context", help="Contexto ou motivo"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Registra uma decisao tecnica em DECISIONS.md."""
    mm = MemoryManager(memory_dir)
    p = mm.add_decision(title, body, context)
    console.print(f"[green]Decisao registrada em {p}[/green]")


@memory_app.command("add-failure")
def memory_add_failure(
    title: str = typer.Argument(..., help="Titulo curto do problema"),
    error: str = typer.Argument(..., help="Descricao do erro"),
    fix: str = typer.Option("", "--fix", help="O que corrigiu o problema"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Registra uma tentativa falha em FAILED_ATTEMPTS.md."""
    mm = MemoryManager(memory_dir)
    p = mm.add_failure(title, error, fix)
    console.print(f"[green]Falha registrada em {p}[/green]")


@memory_app.command("add-model-exp")
def memory_add_model_exp(
    result: str = typer.Argument(..., help="Resultado: ok | slow | oom | error"),
    lesson: str = typer.Option("", "--lesson", help="Licao aprendida"),
    state_file: Path = typer.Option(Path(".runtime_state.json"), "--state-file"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Registra experiencia do modelo atual em MODEL_EXPERIENCE.md.

    Le o modelo, contexto e RAM diretamente do .runtime_state.json.
    """
    state = read_state(state_file)
    if state is None:
        console.print(
            "[red]Runtime state nao encontrado.[/red]\n"
            "Rode [bold]bauer doctor[/bold] primeiro."
        )
        raise typer.Exit(code=1)

    mm = MemoryManager(memory_dir)
    p = mm.add_model_experience(
        model=state["configured_model"],
        context_tokens=state["context"]["applied"],
        result=result,
        ram_used_mb=state["ram_available_mb"],
        machine_id=state["machine_id"],
        lesson=lesson,
    )
    console.print(f"[green]Experiencia registrada em {p}[/green]")


@memory_app.command("summarize")
def memory_summarize(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Mostra resumo estruturado de todos os arquivos de memoria."""
    import re
    from .memory_manager import MEMORY_FILES

    mm = MemoryManager(memory_dir)
    _SECTION_RE = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)

    table = Table(title="Resumo da Memoria — memory/")
    table.add_column("arquivo", style="cyan")
    table.add_column("entradas", justify="right")
    table.add_column("ultima entrada", style="dim")

    for key, filename in MEMORY_FILES.items():
        p = memory_dir / filename
        if not p.exists():
            table.add_row(filename, "0", "—")
            continue
        content = p.read_text(encoding="utf-8", errors="replace")
        matches = list(_SECTION_RE.finditer(content))
        count = len(matches)
        last_ts = matches[-1].group(1) if matches else "—"
        table.add_row(filename, str(count), last_ts)

    console.print(table)
    console.print(
        "\n[dim]Use 'bauer memory show <arquivo>' para ver o conteudo completo.[/dim]"
    )


@memory_app.command("add-note")
def memory_add_note(
    title: str = typer.Argument(..., help="Titulo da nota"),
    body: str = typer.Argument(..., help="Conteudo da nota"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Adiciona uma nota geral em MEMORY.md."""
    mm = MemoryManager(memory_dir)
    p = mm.add_note(title, body)
    console.print(f"[green]Nota registrada em {p}[/green]")


@memory_app.command("add-lesson")
def memory_add_lesson(
    decision: str = typer.Argument(..., help="Decisao automatica tomada"),
    reason: str = typer.Argument(..., help="Motivo da decisao"),
    undo: str = typer.Option("", "--undo", help="Como desfazer"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir"),
):
    """Registra uma decisao automatica do runtime em RUNTIME_LESSONS.md."""
    mm = MemoryManager(memory_dir)
    p = mm.add_runtime_lesson(decision, reason, undo)
    console.print(f"[green]Licao registrada em {p}[/green]")


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Texto a buscar na memoria"),
    top_k: int = typer.Option(5, "--top", "-n", help="Numero de resultados"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    fts: bool = typer.Option(False, "--fts", help="Usa indice SQLite FTS persistente"),
):
    """Busca semantica (TF-IDF) nos arquivos de memoria."""
    from rich.table import Table as RichTable

    if fts:
        from .memory_index import MemoryIndex

        index = MemoryIndex(memory_dir)
        if not index.db_path.exists():
            index.rebuild()
        hits = index.search(query, limit=top_k)
        results = [
            {"file": hit.file, "title": hit.title, "score": hit.score, "snippet": hit.snippet}
            for hit in hits
        ]
    else:
        mm = MemoryManager(memory_dir)
        results = mm.search(query, top_k=top_k)

    if not results:
        console.print(f"[yellow]Nenhum resultado para '{query}' em {memory_dir}/[/yellow]")
        raise typer.Exit()

    table = RichTable(title=f"Busca: '{query}' — {len(results)} resultado(s)", show_lines=True)
    table.add_column("Arquivo", style="cyan", no_wrap=True)
    table.add_column("Titulo", style="bold")
    table.add_column("Score", style="dim", width=7)
    table.add_column("Trecho", style="dim")

    for r in results:
        table.add_row(
            r["file"],
            r["title"][:60],
            str(r["score"]),
            r["snippet"][:120] + ("…" if len(r["snippet"]) > 120 else ""),
        )

    console.print(table)


@memory_app.command("index")
def memory_index_cmd(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Reconstrói o indice SQLite FTS dos arquivos Markdown de memoria."""
    from .memory_index import MemoryIndex

    count = MemoryIndex(memory_dir).rebuild()
    console.print(f"[green]Indice de memoria atualizado:[/green] {count} bloco(s)")


@memory_app.command("skills-pending")
def memory_skills_pending_cmd(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
):
    """Lista sugestões de skills pendentes de aprovação manual."""
    from .skill_registry import SkillRegistry

    suggestions = SkillRegistry(memory_dir).pending_suggestions()
    if not suggestions:
        console.print("[dim]Nenhuma sugestao de skill pendente.[/dim]")
        return
    table = Table(title="Skills pendentes", show_lines=False)
    table.add_column("Skill", style="cyan")
    table.add_column("Ocorrencias", justify="right")
    table.add_column("Status")
    for suggestion in suggestions:
        table.add_row(
            suggestion.get("name", ""),
            suggestion.get("ocorrencias", ""),
            suggestion.get("status", ""),
        )
    console.print(table)


@memory_app.command("skill-approve")
def memory_skill_approve_cmd(
    name: str = typer.Argument(..., help="Nome da skill sugerida"),
    workspace: Path = typer.Option(Path("workspace"), "--workspace"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    description: str = typer.Option("", "--description"),
    content: str = typer.Option("", "--content"),
):
    """Promove uma sugestão pendente para workspace/.bauer_skills.json."""
    from .skill_registry import SkillRegistry

    try:
        path = SkillRegistry(memory_dir).approve_suggestion(
            name,
            workspace=workspace,
            description=description,
            content=content,
        )
    except Exception as exc:
        console.print(f"[red]Erro aprovando skill:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]Skill aprovada em[/green] {path}")


@memory_app.command("cleanup")
def memory_cleanup(
    days: int = typer.Option(90, "--days", "-d", help="Remover entradas mais antigas que N dias"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simula sem modificar arquivos"),
):
    """Remove entradas de memória mais antigas que N dias (padrão: 90).

    Exemplos:
      bauer memory cleanup              # remove entradas >90 dias
      bauer memory cleanup --days 30    # remove entradas >30 dias
      bauer memory cleanup --dry-run    # conta sem apagar
    """
    from rich.table import Table as RichTable

    mm = MemoryManager(memory_dir)
    removed = mm.cleanup_old_entries(max_age_days=days, dry_run=dry_run)

    total = sum(removed.values())
    if total == 0:
        console.print(f"[green]Nenhuma entrada com mais de {days} dias encontrada.[/green]")
        return

    table = RichTable(
        title=f"{'[dim]Simulação[/dim] — ' if dry_run else ''}Entradas removidas (>{days} dias)",
        show_lines=False,
        box=None,
    )
    table.add_column("Arquivo", style="cyan")
    table.add_column("Removidas", style="yellow", justify="right")
    for fname, n in removed.items():
        if n > 0:
            table.add_row(fname, str(n))

    console.print(table)
    action = "seriam removidas" if dry_run else "removidas"
    console.print(f"[bold]{total}[/bold] entradas {action} no total.")
    if dry_run:
        console.print("[dim]Rode sem --dry-run para aplicar.[/dim]")


# --- tools ------------------------------------------------------------------

_WORKSPACE_DIR = Path("workspace")
# Localização canônica das empresas — dentro do workspace para manter tudo junto.
# Fallback legacy: bauer também aceita companies/ na raiz (via CompanyManager.get_active).
_COMPANIES_DIR = Path("workspace") / "companies"


def _build_client(cfg):
    """Retorna o client correto conforme model.provider.

    Providers suportados (igual Hermes Agent):
      ollama      — Ollama local/remoto
      openai      — OpenAI oficial ou endpoint OpenAI-compatible
      openrouter  — OpenRouter (200+ modelos: GPT, Claude, Gemini…)
      custom      — Qualquer endpoint OpenAI-compatible (alias de openai)

    Autenticacao via bauer auth:
      Se o provider tiver token autenticado via 'bauer auth login',
      usa automaticamente as credenciais salvas.
    """
    provider = cfg.model.provider

    # Verifica se há token autenticado via bauer auth
    try:
        from .auth import AuthManager
        auth = AuthManager()
        token = auth.store.load(provider) or auth.store.load(f"{provider}-api")
        if token:
            # Verifica se é JWT do Codex (não serve como API key)
            if token.extra.get("type") == "jwt":
                console.print(
                    f"[yellow]Aviso:[/yellow] Token do Codex CLI detectado.\n"
                    f"Este token é para uso exclusivo do Codex CLI.\n"
                    f"Para usar a API, insira uma API key: [bold]bauer auth login -p openai-api[/bold]"
                )
            elif provider == "copilot" and token.is_expired:
                # Copilot session token expira a cada ~30 min — renova automaticamente
                console.print("[dim]Token Copilot expirado. Renovando...[/dim]")
                refreshed = auth.refresh_copilot_token(token)
                if refreshed:
                    token = refreshed
                    console.print("[green]✓ Token Copilot renovado.[/green]")
                else:
                    console.print(
                        "[red]Nao foi possivel renovar o token Copilot.[/red]\n"
                        "Execute: [bold]bauer auth login -p copilot[/bold]"
                    )
                    import sys; sys.exit(1)
            # ChatGPT via browser (OAuth): token sem api_key → usa o backend
            # ChatGPT (Responses API) billando na assinatura, igual ao Codex.
            if (
                provider == "openai"
                and not token.api_key
                and token.access_token
                and not token.extra.get("type") == "jwt"
            ):
                # Renova automaticamente se expirado (sem novo login no browser).
                if token.is_expired and token.refresh_token:
                    console.print("[dim]Token ChatGPT expirado. Renovando...[/dim]")
                    refreshed = auth.refresh("openai")
                    if refreshed:
                        token = refreshed
                        console.print("[green]✓ Token ChatGPT renovado.[/green]")
                    else:
                        console.print(
                            "[yellow]Nao foi possivel renovar.[/yellow] "
                            "[dim]Refaca o login: bauer auth login -p openai[/dim]"
                        )
                from .chatgpt_backend import ChatGPTBackendClient, DEFAULT_CHATGPT_BASE
                _base = getattr(cfg.openai, "chatgpt_base_url", "") or DEFAULT_CHATGPT_BASE
                return ChatGPTBackendClient(
                    access_token=token.access_token,
                    account_id=token.extra.get("chatgpt_account_id") or "",
                    base_url=_base,
                    timeout_seconds=cfg.openai.timeout_seconds,
                    model=cfg.model.name,
                )
            if not token.extra.get("type") == "jwt":
                from .openai_client import OpenAIClient
                api_key = token.api_key or token.access_token
                api_base = token.api_base or cfg.openai.host
                extra_headers: dict[str, str] = {}
                # Providers sem prefixo /v1/ no endpoint de chat
                _NO_V1 = {"copilot", "github", "gemini"}
                if provider in _NO_V1:
                    chat_path = "/chat/completions"
                elif api_base.rstrip("/").endswith("/v1"):
                    # api_base já inclui /v1 (ex: OAuth token salva "https://api.openai.com/v1")
                    # não duplicar: /v1/v1/chat/completions → 404
                    chat_path = "/chat/completions"
                else:
                    chat_path = "/v1/chat/completions"
                if provider == "copilot":
                    extra_headers = {
                        "Copilot-Integration-Id": "vscode-chat",
                        "Editor-Version": "vscode/1.99.0",
                        "Editor-Plugin-Version": "copilot-chat/0.26.0",
                        "User-Agent": "GitHubCopilotChat/0.26.0",
                        "X-GitHub-Api-Version": "2023-07-07",
                    }
                elif provider == "github":
                    extra_headers = {
                        "X-GitHub-Api-Version": "2023-07-07",
                    }
                return OpenAIClient(
                    host=api_base,
                    timeout_seconds=getattr(getattr(cfg, provider, None), "timeout_seconds", cfg.openai.timeout_seconds),
                    api_key=api_key,
                    model=cfg.model.name,
                    extra_headers=extra_headers or None,
                    chat_path=chat_path,
                )
    except Exception:
        pass

    if provider == "opencode":
        import os
        from .openai_client import OpenAIClient
        # OpenCode Zen — endpoint público gratuito, sem API key necessária
        # Requer User-Agent identificando o cliente opencode para passar Cloudflare
        # OPENCODE_API_KEY no .env pode sobrescrever a chave pública (ex: conta premium)
        opencode_key = os.environ.get("OPENCODE_API_KEY", "public")
        return OpenAIClient(
            host="https://opencode.ai/zen",
            timeout_seconds=cfg.opencode.timeout_seconds,
            api_key=opencode_key,
            model=cfg.model.name,
            extra_headers={"User-Agent": "opencode/1.15.11"},
        )

    if provider == "openrouter":
        from .openai_client import OpenAIClient
        # OpenRouter usa OpenAI wire protocol com headers extras de identificação
        extra_headers = {}
        if cfg.openrouter.http_referer:
            extra_headers["HTTP-Referer"] = cfg.openrouter.http_referer
        if cfg.openrouter.x_title:
            extra_headers["X-Title"] = cfg.openrouter.x_title
        return OpenAIClient(
            host="https://openrouter.ai/api",
            timeout_seconds=cfg.openrouter.timeout_seconds,
            api_key=cfg.openrouter.api_key,
            model=cfg.model.name,
            extra_headers=extra_headers,
        )

    if provider == "groq":
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.groq.com/openai",
            timeout_seconds=cfg.groq.timeout_seconds,
            api_key=cfg.groq.api_key,
            model=cfg.model.name,
        )

    if provider == "mistral":
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.mistral.ai",
            timeout_seconds=cfg.mistral.timeout_seconds,
            api_key=cfg.mistral.api_key,
            model=cfg.model.name,
        )

    if provider == "xai":
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.x.ai",
            timeout_seconds=cfg.xai.timeout_seconds,
            api_key=cfg.xai.api_key,
            model=cfg.model.name,
        )

    if provider == "together":
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.together.xyz",
            timeout_seconds=cfg.together.timeout_seconds,
            api_key=cfg.together.api_key,
            model=cfg.model.name,
        )

    if provider == "deepseek":
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.deepseek.com",
            timeout_seconds=cfg.deepseek.timeout_seconds,
            api_key=cfg.deepseek.api_key,
            model=cfg.model.name,
        )

    if provider == "gemini":
        from .openai_client import OpenAIClient
        # Google expõe endpoint OpenAI-compatible
        # Host já contém /v1beta/openai — não adicionar /v1/ extra
        return OpenAIClient(
            host="https://generativelanguage.googleapis.com/v1beta/openai",
            timeout_seconds=cfg.gemini.timeout_seconds,
            api_key=cfg.gemini.api_key,
            model=cfg.model.name,
            chat_path="/chat/completions",
        )

    if provider == "anthropic":
        from .anthropic_client import AnthropicClient
        return AnthropicClient(
            api_key=cfg.anthropic.api_key,
            timeout_seconds=cfg.anthropic.timeout_seconds,
            api_version=cfg.anthropic.api_version,
            model=cfg.model.name,
        )

    if provider == "azure":
        from .openai_client import OpenAIClient
        # Azure usa api-key header em vez de Authorization: Bearer
        endpoint = cfg.azure.endpoint.rstrip("/")
        deployment = cfg.azure.deployment or cfg.model.name
        base_url = f"{endpoint}/openai/deployments/{deployment}"
        return OpenAIClient(
            host=base_url,
            timeout_seconds=cfg.azure.timeout_seconds,
            api_key=cfg.azure.api_key,
            model=deployment,
            extra_headers={
                "api-key": cfg.azure.api_key,
                "x-ms-useragent": "bauer-agent/1.0",
            },
            api_version=cfg.azure.api_version,
        )

    if provider == "github":
        from .openai_client import OpenAIClient
        # GitHub Models: endpoint sem /v1/ prefix
        # POST https://models.inference.ai.azure.com/chat/completions
        return OpenAIClient(
            host="https://models.inference.ai.azure.com",
            timeout_seconds=cfg.github.timeout_seconds,
            api_key=cfg.github.token,
            model=cfg.model.name,
            chat_path="/chat/completions",
            extra_headers={
                "X-GitHub-Api-Version": "2023-07-07",
            },
        )

    if provider == "copilot":
        from .openai_client import OpenAIClient
        # GitHub Copilot: endpoint sem /v1/ prefix
        # POST https://api.githubcopilot.com/chat/completions
        return OpenAIClient(
            host="https://api.githubcopilot.com",
            timeout_seconds=cfg.copilot.timeout_seconds,
            api_key=cfg.copilot.token,
            model=cfg.model.name,
            chat_path="/chat/completions",
            extra_headers={
                "Copilot-Integration-Id": "vscode-chat",
                "Editor-Version": "vscode/1.99.0",
                "Editor-Plugin-Version": "copilot-chat/0.26.0",
                "User-Agent": "GitHubCopilotChat/0.26.0",
                "X-GitHub-Api-Version": "2023-07-07",
            },
        )

    if provider == "cohere":
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.cohere.com/compatibility",
            timeout_seconds=cfg.cohere.timeout_seconds,
            api_key=cfg.cohere.api_key,
            model=cfg.model.name,
        )

    if provider == "perplexity":
        from .openai_client import OpenAIClient
        # Perplexity não usa /v1/ prefix — POST direto em /chat/completions
        return OpenAIClient(
            host="https://api.perplexity.ai",
            timeout_seconds=cfg.perplexity.timeout_seconds,
            api_key=cfg.perplexity.api_key,
            model=cfg.model.name,
            chat_path="/chat/completions",
        )

    if provider == "fireworks":
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.fireworks.ai/inference",
            timeout_seconds=cfg.fireworks.timeout_seconds,
            api_key=cfg.fireworks.api_key,
            model=cfg.model.name,
        )

    if provider == "huggingface":
        from .openai_client import OpenAIClient
        host = cfg.huggingface.host.rstrip("/")
        # Host padrão já inclui /v1 — usar chat_path para não duplicar
        if host.endswith("/v1"):
            return OpenAIClient(
                host=host,
                timeout_seconds=cfg.huggingface.timeout_seconds,
                api_key=cfg.huggingface.api_key,
                model=cfg.model.name,
                chat_path="/chat/completions",
            )
        return OpenAIClient(
            host=host,
            timeout_seconds=cfg.huggingface.timeout_seconds,
            api_key=cfg.huggingface.api_key,
            model=cfg.model.name,
        )

    if provider == "cerebras":
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.cerebras.ai",
            timeout_seconds=cfg.cerebras.timeout_seconds,
            api_key=cfg.cerebras.api_key,
            model=cfg.model.name,
        )

    if provider == "sambanova":
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.sambanova.ai",
            timeout_seconds=cfg.sambanova.timeout_seconds,
            api_key=cfg.sambanova.api_key,
            model=cfg.model.name,
        )

    if provider == "nvidia":
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host="https://integrate.api.nvidia.com",
            timeout_seconds=cfg.nvidia.timeout_seconds,
            api_key=cfg.nvidia.api_key,
            model=cfg.model.name,
        )

    if provider == "lmstudio":
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host=cfg.lmstudio.host,
            timeout_seconds=cfg.lmstudio.timeout_seconds,
            api_key=cfg.lmstudio.api_key or "lm-studio",
            model=cfg.model.name,
        )

    if provider == "databricks":
        from .openai_client import OpenAIClient
        host = cfg.databricks.host.rstrip("/")
        # Databricks serving-endpoints usa /chat/completions sem /v1
        return OpenAIClient(
            host=f"{host}/serving-endpoints",
            timeout_seconds=cfg.databricks.timeout_seconds,
            api_key=cfg.databricks.api_key,
            model=cfg.model.name,
            chat_path="/chat/completions",
            extra_headers={"Authorization": f"Bearer {cfg.databricks.api_key}"},
        )

    if provider == "moonshot":
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.moonshot.cn",
            timeout_seconds=cfg.moonshot.timeout_seconds,
            api_key=cfg.moonshot.api_key,
            model=cfg.model.name,
        )

    if provider == "alibaba":
        from .openai_client import OpenAIClient
        # DashScope endpoint já inclui /compatible-mode (sem /v1 adicional via host)
        return OpenAIClient(
            host="https://dashscope.aliyuncs.com/compatible-mode",
            timeout_seconds=cfg.alibaba.timeout_seconds,
            api_key=cfg.alibaba.api_key,
            model=cfg.model.name,
        )

    if provider == "vertex":
        from .openai_client import OpenAIClient
        region = cfg.vertex.region or "us-central1"
        project = cfg.vertex.project_id
        vertex_host = (
            f"https://{region}-aiplatform.googleapis.com/v1beta1"
            f"/projects/{project}/locations/{region}/endpoints/openapi"
        )
        return OpenAIClient(
            host=vertex_host,
            timeout_seconds=cfg.vertex.timeout_seconds,
            api_key=cfg.vertex.access_token,
            model=cfg.model.name,
            chat_path="/chat/completions",
        )

    if provider in ("openai", "custom"):
        from .openai_client import OpenAIClient
        return OpenAIClient(
            host=cfg.openai.host,
            timeout_seconds=cfg.openai.timeout_seconds,
            api_key=cfg.openai.api_key,
            model=cfg.model.name,
        )

    # padrão: ollama
    return OllamaClient(cfg.ollama.host, cfg.ollama.timeout_seconds, cfg.ollama.api_key)


def _build_shell_runner(cfg, workspace: Path) -> ShellRunner | None:
    """Cria ShellRunner se tools.shell_enabled=true na config."""
    if cfg is None or not cfg.tools.shell_enabled:
        return None
    return ShellRunner(
        workspace=workspace,
        safe_mode=cfg.tools.safe_mode,
        timeout=cfg.tools.timeout_seconds,
        max_output_bytes=cfg.tools.max_output_kb * 1024,
    )


def _build_router(cfg, workspace: Path, llm_client=None) -> ToolRouter:
    """Cria ToolRouter com shell_runner, web e llm_client a partir da config."""
    shell_runner = _build_shell_runner(cfg, workspace)
    web_enabled = cfg.tools.web_enabled if cfg is not None else False
    web_config = cfg.web if cfg is not None else None
    return ToolRouter(
        workspace,
        shell_runner=shell_runner,
        web_enabled=web_enabled,
        web_config=web_config,
        llm_client=llm_client,
    )


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
    from .plugin_registry import PluginRegistry

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


# ---------------------------------------------------------------------------
# bauer plugin — plugin manager com suporte a plugin.yaml manifests
# ---------------------------------------------------------------------------

@plugin_app.command("list")
def plugin_list(
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
):
    """Lista plugins instalados (mostra versão e manifest quando disponível)."""
    from .plugin_registry import PluginRegistry

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
    from .plugin_registry import PluginRegistry, install_plugin

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
    from .plugin_registry import PluginRegistry

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


# --- agent ------------------------------------------------------------------


def _resolve_model_with_ram_check(
    model_name: str,
    reg,
    client: OllamaClient,
    ram_available_mb: int,
    safety_margin_mb: int,
    memory_dir: Path,
) -> str:
    """Verifica se model_name cabe na RAM disponível.

    Se não couber, seleciona automaticamente o melhor modelo instalado que caiba.
    Registra a decisão em RUNTIME_LESSONS.md.
    """
    from .model_registry import contexto_seguro
    from .memory_manager import MemoryManager

    info = reg.get(model_name)
    if info is None:
        return model_name

    # Verifica histórico de MODEL_EXPERIENCE antes da RAM
    try:
        from .learning_engine import LearningEngine
        engine = LearningEngine(memory_dir)
        exps = engine.load_experience()
        from .machine_id import machine_id as get_machine_id
        mid = get_machine_id()
        bad_results = {"oom", "slow", "error", "out of memory"}
        bad_history = [
            e for e in exps
            if (not e.machine_id or e.machine_id == mid)
            and model_name.lower() in e.title.lower()
            and any(b in e.result.lower() for b in bad_results)
        ]
        if len(bad_history) >= 2:
            console.print(
                f"[yellow]Historico:[/yellow] '{model_name}' falhou {len(bad_history)}x "
                f"nesta maquina ({', '.join(e.result for e in bad_history[-2:])})."
            )
    except Exception:
        bad_history = []

    safe_ctx = contexto_seguro(info, ram_available_mb, safety_margin_mb)
    if safe_ctx > 0 and len(bad_history) < 2:
        return model_name

    console.print(
        f"[yellow]RAM insuficiente:[/yellow] '{model_name}' precisa de ~{info.ram_base_mb} MB, "
        f"apenas {ram_available_mb} MB disponíveis."
    )

    try:
        installed = list(dict.fromkeys(client.list_models()))
    except Exception:
        installed = []

    candidates = []
    for m in installed:
        m_info = reg.get(m)
        if m_info is None:
            continue
        ctx = contexto_seguro(m_info, ram_available_mb, safety_margin_mb)
        if ctx > 0:
            candidates.append((m, m_info.ram_base_mb))

    if not candidates:
        console.print(
            "[red]Nenhum modelo instalado cabe na RAM disponível. "
            "Feche aplicativos e tente novamente.[/red]"
        )
        return model_name

    best_model = max(candidates, key=lambda x: x[1])[0]
    best_info = reg.get(best_model)

    console.print(
        f"[cyan]Auto-selecionando:[/cyan] '{best_model}' "
        f"(~{best_info.ram_base_mb if best_info else '?'} MB — melhor que cabe na RAM)"
    )

    try:
        mm = MemoryManager(memory_dir)
        mm.add_runtime_lesson(
            decision=f"Modelo trocado de '{model_name}' para '{best_model}'",
            reason=f"RAM disponível ({ram_available_mb} MB) insuficiente para '{model_name}' (~{info.ram_base_mb} MB necessários)",
            undo=f"Feche aplicativos ou force com: bauer agent --model {model_name}",
        )
    except Exception:
        pass

    return best_model


def _pick_model(client: OllamaClient, current: str) -> str:
    """Lista modelos instalados no Ollama e deixa o usuario escolher.

    Retorna o modelo escolhido (ou current se usuario cancelar/pressionar Enter).
    """
    from .ollama_client import OllamaError as _OllamaError

    try:
        installed = list(dict.fromkeys(client.list_models()))  # preserva ordem, remove duplicatas
    except _OllamaError:
        return current

    if not installed:
        return current

    console.print("\n[bold]Modelos instalados no Ollama:[/bold]")
    for i, name in enumerate(installed, 1):
        marker = "  [dim]<- atual[/dim]" if name == current else ""
        console.print(f"  [cyan]{i}.[/cyan] {name}{marker}")

    try:
        raw = input(f"\nNumero ou nome do modelo (Enter = {current}): ").strip()
    except (KeyboardInterrupt, EOFError):
        return current

    if not raw:
        return current

    if raw.isdigit():
        idx = int(raw) - 1
        if 0 <= idx < len(installed):
            return installed[idx]
        console.print(f"[yellow]Numero fora do intervalo. Usando '{current}'.[/yellow]")
        return current

    if raw in installed:
        return raw

    console.print(f"[yellow]Modelo '{raw}' nao encontrado. Usando '{current}'.[/yellow]")
    return current


@agent_app.callback(invoke_without_command=True)
def agent(
    ctx: typer.Context,
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    models: Path = typer.Option(Path("models.yaml"), "--models", help="Caminho do models.yaml"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(
        Path(".runtime_state.json"),
        "--state-file",
        help="Runtime state (gerado pelo doctor)",
    ),
    model: str = typer.Option("", "--model", help="Sobrescreve o modelo do config"),
    pick: bool = typer.Option(False, "--pick", help="Mostra lista de modelos para escolher"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Retoma a ultima sessao salva"),
    session_id_opt: str = typer.Option("", "--session-id", help="ID de sessao especifica para retomar"),
    sessions_dir: Path = typer.Option(Path("memory/sessions"), "--sessions-dir", help="Diretorio de sessoes"),
    no_intro: bool = typer.Option(False, "--no-intro", help="Pula a animacao de entrada"),
    port: int = typer.Option(
        0, "--port", "-p",
        help="Sobe servidor HTTP embutido nesta porta (0 = desabilitado). "
             "Permite conexao do Claw3D/escritorio virtual sem bauer serve separado.",
    ),
    gateway_port: int = typer.Option(
        0, "--gateway-port", "-g",
        help="Porta do gateway WebSocket Claw3D (0 = desabilitado). "
             "Requer --port. Ex: --port 7770 --gateway-port 18789",
    ),
    api_key_opt: str = typer.Option("", "--api-key", help="API key para o servidor embutido"),
):
    """Agente interativo com Tool Bridge, roteamento inteligente e sessao persistente.

    Sem sub-comando: inicia o chat com o modelo atual.
    Sub-comandos: create, list, run, delete — gerenciam agents especializados.

    Use --resume para continuar a conversa de onde parou.
    Use --resume --session-id abc123 para retomar uma sessao especifica.
    Use --port 7770 para aceitar conexoes HTTP do Claw3D (floor local-runtime).
    Use --port 7770 --gateway-port 18789 para conexao WebSocket completa (todos os floors).
    """
    # Se um sub-comando foi chamado (create/list/run/delete), nao executa o chat
    if ctx.invoked_subcommand is not None:
        return

    # Animacao de entrada — apenas em sessoes novas (nao --resume)
    # Para bauer agent run <name>, a intro eh chamada dentro do agent_run
    if not resume:
        play_intro(console, skip=no_intro)

    cfg, reg = _load_or_die(config, models)
    setup_logging(cfg.logging.level, cfg.logging.file)

    is_ollama_provider = cfg.model.provider == "ollama"

    # Cria cliente Ollama local SEMPRE — necessario para roteamento e planejamento
    # mesmo quando o provider principal e OpenAI/OpenRouter.
    from bauer.ollama_client import OllamaClient as _OllamaClient
    _ollama = _OllamaClient(cfg.ollama.host, cfg.ollama.timeout_seconds, cfg.ollama.api_key)
    _ollama_alive, _ = _ollama.is_alive()

    state = _get_or_run_state(cfg, reg, state_file)

    # Ollama e obrigatorio apenas quando o provider principal e ollama.
    # Para OpenAI/OpenRouter, o agente funciona sem Ollama local
    # (apenas o roteamento fica desabilitado se Ollama estiver offline).
    if is_ollama_provider and not state.get("ollama_alive"):
        console.print(
            "[red]Ollama offline.[/red]\n"
            "Verifique se o Ollama esta rodando e rode [bold]bauer doctor[/bold]."
        )
        raise typer.Exit(code=1)

    if not workspace.exists():
        console.print(f"[yellow]Workspace '{workspace}' nao existe — criando.[/yellow]")
        workspace.mkdir(parents=True, exist_ok=True)

    client = _build_client(cfg)
    applied_context = state["context"]["applied"]
    # Propaga num_ctx ao OllamaClient — sem isso o Ollama usa o default do modelo (geralmente 2048)
    if is_ollama_provider and hasattr(client, "num_ctx"):
        client.num_ctx = applied_context
    # Propaga think ao OllamaClient — usa valor de config.yaml (None → False no cliente)
    if is_ollama_provider and hasattr(client, "think"):
        client.think = cfg.model.think

    # Resolucao do modelo: --model > --pick > auto (com RAM check so para Ollama)
    import logging as _logging
    _mlog = _logging.getLogger("bauer.model_selection")
    _mlog.debug("[model-selection] config.model.name=%s", cfg.model.name)
    _mlog.debug("[model-selection] config.model.provider=%s", cfg.model.provider)
    _mlog.debug("[model-selection] router.enabled=%s", cfg.router.enabled)
    _mlog.debug("[model-selection] requested_context=%s  applied_context=%s",
               cfg.model.requested_context, applied_context)
    if is_ollama_provider:
        _mlog.debug("[model-selection] think=%s", cfg.model.think)

    if model:
        model_name = model
        _mlog.debug("[model-selection] source=--model flag  active=%s", model_name)
    elif pick:
        model_name = _pick_model(client, state["configured_model"])
        _mlog.debug("[model-selection] source=--pick  active=%s", model_name)
    else:
        if is_ollama_provider:
            model_name = _resolve_model_with_ram_check(
                state["configured_model"], reg, client,
                state["ram_available_mb"], cfg.runtime.safety_margin_mb, _MEMORY_DIR,
            )
        else:
            # Providers cloud: usa o modelo configurado diretamente (sem RAM check local)
            model_name = cfg.model.name
        _mlog.debug("[model-selection] source=config.yaml  active=%s", model_name)

    # Verifica modelo no Ollama apenas quando provider=ollama
    if is_ollama_provider:
        resolved = client.resolve_model_name(model_name)
        if resolved is None:
            console.print(
                f"[red]Modelo '{model_name}' nao encontrado no Ollama.[/red]\n"
                f"Rode: [bold]ollama pull {model_name}[/bold]\n"
                f"Ou veja os modelos instalados: [bold]ollama list[/bold]"
            )
            raise typer.Exit(code=1)
        if resolved != model_name:
            console.print(f"[dim]Modelo resolvido: '{model_name}' → '{resolved}'[/dim]")
            model_name = resolved

    # ── Empresa ativa — redireciona workspace ANTES de construir o router ────
    from .company_manager import CompanyManager as _CompanyManager
    _cm_main = _CompanyManager(_COMPANIES_DIR)
    _active_company_main = _cm_main.get_active()
    if _active_company_main:
        _default_ws = Path(_WORKSPACE_DIR)
        if workspace == _default_ws or workspace == _default_ws.resolve():
            _cws = _cm_main.root / _active_company_main.id / "workspace"
            _cws.mkdir(parents=True, exist_ok=True)
            # Só redireciona para workspace isolado se ele tiver conteúdo real.
            # Se estiver vazio mas o workspace global tiver arquivos, usa o global
            # (setup legado: conteúdo ainda está em workspace/).
            _cws_has = any(
                f for f in _cws.rglob("*")
                if f.is_file() and f.name not in (".gitkeep", ".gitignore")
            )
            _gws_has = any(
                f for f in _default_ws.rglob("*")
                if f.is_file() and f.name not in (".gitkeep", ".gitignore")
                and not str(f).startswith(str(_cm_main.root))
            ) if _default_ws.exists() else False
            workspace = _cws if (_cws_has or not _gws_has) else _default_ws

        _default_sessions = Path("memory/sessions")
        if sessions_dir == _default_sessions or sessions_dir == _default_sessions.resolve():
            _css = _cm_main.root / _active_company_main.id / "memory" / "sessions"
            _css.mkdir(parents=True, exist_ok=True)
            sessions_dir = _css
        console.print(
            f"Empresa ativa: [bold cyan]{_active_company_main.name}[/bold cyan]"
        )

    router = _build_router(cfg, workspace)

    # ── ModelRouter: só faz sentido com Ollama (múltiplos modelos locais) ────
    # Com provider cloud (Groq, OpenAI, etc.) há apenas UM modelo configurado.
    # Rodar o classificador (qwen3:0.6b) pra rotear pro mesmo modelo é overhead
    # sem ganho — desabilitamos o router automaticamente nesses casos.
    model_router = None
    orchestrator = None
    if cfg.router.enabled:
        if not is_ollama_provider:
            pass  # cloud provider: router silently disabled
        elif not _ollama_alive:
            console.print(
                f"[yellow]Roteamento desabilitado:[/yellow] Ollama offline em {cfg.ollama.host}.\n"
                f"[dim]O ModelRouter precisa do Ollama local para o classificador "
                f"({cfg.router.router_model}).[/dim]"
            )
        else:
            # Ollama com múltiplos modelos — roteamento faz sentido
            router_cfg = RouterConfig(
                router_model=cfg.router.router_model,
                default_model=model_name,
                routes=[
                    Route("code",       "codigo",     cfg.router.code_model),
                    Route("reasoning",  "raciocinio", cfg.router.reasoning_model),
                    Route("tool",       "ferramenta", cfg.router.code_model),
                    Route("direct",     "direto",     cfg.router.direct_model),
                    Route("orchestrate","orquestrar", cfg.router.reasoning_model),
                ],
            )
            model_router = ModelRouter(_ollama, router_cfg)

            _parallel = cfg.runtime.profile == "high"
            orch_cfg = OrchestratorConfig(
                planner_model=cfg.router.router_model,
                synthesizer_model=cfg.router.reasoning_model,
                max_steps=MAX_STEPS,
                parallel_steps=_parallel,
            )
            orchestrator = AgentOrchestrator(
                client, router, model_router, orch_cfg,
                planner_client=_ollama,
            )
            console.print(
                f"[dim]Router ativo ({cfg.router.router_model}) -> "
                f"code={cfg.router.code_model} | "
                f"reasoning={cfg.router.reasoning_model} | "
                f"direct={cfg.router.direct_model}[/dim]"
            )

    # ── Sessao persistente ───────────────────────────────────────────────────
    try:
        from .sqlite_session_store import SqliteSessionStore
        store = SqliteSessionStore(sessions_dir)
    except Exception:
        from .session_store import SessionStore
        store = SessionStore(sessions_dir)
    sid: str | None = None

    if resume:
        if session_id_opt:
            if store.exists(session_id_opt):
                sid = session_id_opt
                console.print(f"[yellow]Retomando sessao: {sid}[/yellow]")
            else:
                console.print(f"[red]Sessao '{session_id_opt}' nao encontrada.[/red]")
                sessions = store.list_sessions()
                if sessions:
                    console.print(f"[dim]Sessoes disponiveis: {', '.join(sessions[-5:])}[/dim]")
                raise typer.Exit(code=1)
        else:
            sessions = store.list_sessions()
            if sessions:
                sid = sessions[-1]  # mais recente (ordenado por nome = timestamp UUID)
                msgs = store.load(sid)
                console.print(
                    f"[yellow]Retomando ultima sessao: {sid} "
                    f"({len(msgs)} mensagens)[/yellow]"
                )
            else:
                console.print("[yellow]Nenhuma sessao anterior encontrada — iniciando nova.[/yellow]")

    if sid is None:
        sid = store.new_id()

    # ── Servidor HTTP embutido (opcional — para Claw3D sem bauer serve separado) ──
    _embedded_server_thread = None
    if port > 0:
        _embedded_server_thread = _start_embedded_server(
            client=client,
            model_name=model_name,
            applied_context=applied_context,
            router=router,
            sessions_dir=sessions_dir,
            api_key=api_key_opt or cfg.serve.api_key,
            host="0.0.0.0",
            port=port,
            console=console,
        )
        # ── Gateway WebSocket (opcional — requer --port) ──────────────────────
        if gateway_port > 0:
            _start_gateway_thread_cli(
                bauer_url=f"http://localhost:{port}",
                host="0.0.0.0",
                port=gateway_port,
                api_key=api_key_opt or cfg.serve.api_key,
                console=console,
            )
        else:
            console.print(
                f"[dim]  Claw3D Gateway: desabilitado "
                f"(use --gateway-port 18789 para ativar)[/dim]"
            )
    elif gateway_port > 0:
        console.print(
            "[yellow]Aviso:[/yellow] --gateway-port requer --port. "
            "Exemplo: [bold]bauer agent --port 7770 --gateway-port 18789[/bold]"
        )

    # Constrói clientes de fallback se configurados em model.fallback_providers
    _fallback_clients: list = []
    for _fb_provider in getattr(cfg.model, "fallback_providers", []):
        try:
            _fb_raw = cfg.model_dump()
            _fb_raw["model"]["provider"] = _fb_provider
            from .config_loader import BauerConfig as _BauerCfg
            _fb_cfg = _BauerCfg(**_fb_raw)
            from .env_loader import apply_env_to_config as _aenv
            _aenv(_fb_cfg)
            _fb_client = _build_client(_fb_cfg)
            _fallback_clients.append((_fb_client, _fb_cfg.model.name))
        except Exception:
            pass  # fallback mal configurado — ignora silenciosamente

    def _rebuild_client_chat():
        """Reconstrói client + model_name a partir do config.yaml atual."""
        from .env_loader import load_dotenv as _lenv
        _lenv()
        _new_cfg, _ = _load_or_die(config, models)
        _new_client = _build_client(_new_cfg)
        return _new_client, _new_cfg.model.name

    try:
        run_agent_session(
            client, model_name, applied_context, console, router,
            model_router, orchestrator,
            session_store=store, session_id=sid,
            rebuild_client_fn=_rebuild_client_chat,
            fallback_clients=_fallback_clients or None,
            tool_timeout_s=cfg.agent.tool_timeout_s,
        )
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            console.print("\n[dim]Sessao encerrada pelo usuario.[/dim]")
        else:
            console.print(f"\n[red]Erro inesperado:[/red] {exc}")
            console.print("[dim]Execute 'bauer doctor' para verificar o ambiente.[/dim]")
        raise typer.Exit(code=1)


def _start_embedded_server(
    *,
    client,
    model_name: str,
    applied_context: int,
    router,
    sessions_dir: Path,
    api_key: str,
    host: str,
    port: int,
    console: Console,
):
    """Sobe o servidor HTTP em uma daemon thread e retorna o thread.

    O servidor usa a mesma configuração do agent (client, router, model),
    mas mantém sessões HTTP independentes das sessões do terminal.
    """
    import threading

    try:
        from .server import create_app
        from .agent import _build_system_prompt
    except ImportError as exc:
        console.print(f"[yellow]Servidor embutido indisponivel: {exc}[/yellow]")
        return None

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[yellow]uvicorn nao instalado — servidor embutido desabilitado.[/yellow]\n"
            "[dim]Instale com: pip install uvicorn[/dim]"
        )
        return None

    system_prompt = _build_system_prompt(router)
    fastapi_app = create_app(
        model_name=model_name,
        applied_context=applied_context,
        router=router,
        client=client,
        system_prompt=system_prompt,
        sessions_dir=sessions_dir,
        api_key=api_key,
        rate_limit_requests=60,
        rate_limit_window_s=60.0,
    )

    uv_config = uvicorn.Config(
        fastapi_app,
        host=host,
        port=port,
        log_level="error",   # silencioso — logs vao para o terminal do agent
        access_log=False,
    )
    uv_server = uvicorn.Server(uv_config)

    def _run():
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(uv_server.serve())

    t = threading.Thread(target=_run, daemon=True, name="bauer-embedded-server")
    t.start()

    console.print(
        f"[dim]Servidor embutido em [bold]http://{host}:{port}[/bold] "
        f"(POST /v1/chat/completions) — Claw3D: floor local-runtime[/dim]"
    )
    return t


# --- agent sub-commands (create / list / run / delete) ----------------------


@agent_app.command("create")
def agent_create(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
):
    """Cria um agent especializado em modo entrevista (wizard interativo)."""
    from .agent_registry import AgentRegistry
    from .agent_wizard import wizard_create_agent

    try:
        cfg = load_config(config)
        config_model = cfg.model.name
        config_provider = cfg.model.provider
    except ConfigError:
        config_model = ""
        config_provider = "ollama"

    registry = AgentRegistry(agents_file)
    wizard_create_agent(registry, config_model=config_model, config_provider=config_provider)


@agent_app.command("list")
def agent_list(
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
):
    """Lista todos os agents criados."""
    from .agent_registry import AgentRegistry
    from rich.table import Table

    registry = AgentRegistry(agents_file)
    agents = registry.list_agents()

    if not agents:
        console.print(
            "[yellow]Nenhum agent criado ainda.[/yellow]\n"
            "Crie um com: [bold]bauer agent create[/bold]"
        )
        return

    table = Table(title="Agents", show_lines=True)
    table.add_column("nome", style="cyan", no_wrap=True)
    table.add_column("descrição")
    table.add_column("modelo", style="dim")
    table.add_column("tools", style="dim")
    table.add_column("criado em", style="dim")

    for ag in agents:
        model_str = f"{ag.provider}/{ag.model}" if ag.model else "[dim]config.yaml[/dim]"
        tools_str = ", ".join(ag.tools) if ag.tools else "—"
        created = ag.created_at[:10] if ag.created_at else "—"
        table.add_row(ag.name, ag.description, model_str, tools_str, created)

    console.print(table)
    console.print(f"\n[dim]Para rodar: [bold]bauer agent run <nome>[/bold][/dim]")


@agent_app.command("run")
def agent_run(
    name: str = typer.Argument(..., help="Nome do agent (ex: python-expert)"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(Path(".runtime_state.json"), "--state-file"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Retoma a ultima sessao"),
    sessions_dir: Path = typer.Option(Path("memory/sessions"), "--sessions-dir"),
    no_intro: bool = typer.Option(False, "--no-intro", help="Pula a animacao de entrada"),
):
    """Inicia um agent especializado pelo nome."""
    from .agent_registry import AgentRegistry

    registry = AgentRegistry(agents_file)
    ag = registry.get(name)

    # Fallback: busca em workspace/agents.yaml e companies/<slug>/agents.yaml
    if ag is None:
        _extra_paths: list[Path] = [
            _WORKSPACE_DIR / "agents.yaml",           # workspace/agents.yaml
        ]
        # Empresa ativa → também checa o agents.yaml dela
        _cm_pre = None
        try:
            from .company_manager import CompanyManager as _CMpre
            _cm_pre = _CMpre(_COMPANIES_DIR)
            _active_id = _cm_pre.get_active_id()
            if _active_id:
                _extra_paths.insert(0, _COMPANIES_DIR / _active_id / "agents.yaml")
        except Exception:
            pass

        for _xp in _extra_paths:
            if _xp.exists() and _xp != agents_file:
                _xreg = AgentRegistry(_xp)
                _xag = _xreg.get(name)
                if _xag:
                    ag = _xag
                    agents_file = _xp  # atualiza para salvar no lugar certo
                    break

    if ag is None:
        console.print(f"[yellow]Agent '[cyan]{name}[/cyan]' nao encontrado.[/yellow]")
        if typer.confirm(f"Criar o agent '{name}' agora?", default=True):
            from .agent_wizard import wizard_create_agent
            cfg_tmp, _ = _load_or_die(config, models)
            ag = wizard_create_agent(
                registry,
                config_model=cfg_tmp.model.name,
                config_provider=cfg_tmp.model.provider,
            )
            if ag is None:
                raise typer.Exit(code=0)
        else:
            console.print(
                f"Liste os agents: [bold]bauer agent list[/bold]\n"
                f"Crie um novo:   [bold]bauer agent create[/bold]"
            )
            raise typer.Exit(code=1)

    # Carrega config base, sobrescreve modelo/provider se o agent define
    cfg, reg = _load_or_die(config, models)
    if ag.model:
        cfg.model.name = ag.model
    if ag.provider:
        cfg.model.provider = ag.provider  # type: ignore[assignment]

    # ── Detecção de empresa ativa (ANTES de construir o router) ─────────────
    # Feito aqui para garantir que workspace, sessions_dir, model/provider e
    # tools_allowed da empresa sejam todos aplicados antes de qualquer setup.
    from .company_manager import CompanyManager
    _cm = CompanyManager(_COMPANIES_DIR)
    _active_company = _cm.get_active()

    if _active_company:
        # 1) Workspace: sempre usa companies/<slug>/workspace/ quando empresa está ativa.
        #    O workspace isolado é o padrão — não há mais fallback para o global.
        _default_ws = Path(_WORKSPACE_DIR)  # "workspace"
        if workspace == _default_ws or workspace == _default_ws.resolve():
            _company_ws = _cm.root / _active_company.id / "workspace"
            _company_ws.mkdir(parents=True, exist_ok=True)
            workspace = _company_ws

        # 2) Sessions: redireciona para companies/<slug>/memory/sessions/
        _default_sessions = Path("memory/sessions")
        if sessions_dir == _default_sessions or sessions_dir == _default_sessions.resolve():
            _company_sessions = _cm.root / _active_company.id / "memory" / "sessions"
            _company_sessions.mkdir(parents=True, exist_ok=True)
            sessions_dir = _company_sessions

        # 3) Model/provider da empresa (prioridade menor que o do agent)
        if _active_company.model and not ag.model:
            cfg.model.name = _active_company.model
        if _active_company.provider and not ag.provider:
            cfg.model.provider = _active_company.provider  # type: ignore[assignment]

    setup_logging(cfg.logging.level, cfg.logging.file)

    is_ollama_provider = cfg.model.provider == "ollama"

    from bauer.ollama_client import OllamaClient as _OllamaClient
    _ollama = _OllamaClient(cfg.ollama.host, cfg.ollama.timeout_seconds, cfg.ollama.api_key)
    _ollama_alive, _ = _ollama.is_alive()

    state = _get_or_run_state(cfg, reg, state_file)

    if is_ollama_provider and not state.get("ollama_alive"):
        console.print("[red]Ollama offline.[/red] Verifique se o Ollama esta rodando.")
        raise typer.Exit(code=1)

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)

    # Intro antes de qualquer mensagem de cliente/token
    if not resume:
        play_intro(console, skip=no_intro)

    client = _build_client(cfg)
    applied_context = state["context"]["applied"]
    model_name = ag.model or cfg.model.name

    # Constrói ToolRouter respeitando as tools do agent e da empresa ativa
    from .tool_router import ToolRouter as _ToolRouter
    from .agent_registry import ALL_TOOLS as _ALL_TOOLS
    allowed = set(ag.tools) if ag.tools else set(_ALL_TOOLS)
    # Se a empresa define tools_allowed, intersecta (empresa restringe o agent)
    if _active_company and _active_company.tools_allowed:
        allowed = allowed & set(_active_company.tools_allowed)
    # Constrói router com workspace CORRETO (empresa ou global) e llm_client para vision/delegate
    router = _build_router(cfg, workspace, llm_client=client)
    # Filtra tools fora do escopo do agent/empresa
    router._tools = {k: v for k, v in router._tools.items() if k in allowed}  # type: ignore[attr-defined]

    # ModelRouter/Orchestrator — só ativo com Ollama (múltiplos modelos locais).
    # Com provider cloud há apenas um modelo: routing é overhead sem ganho.
    model_router = None
    orchestrator = None
    if cfg.router.enabled and is_ollama_provider and _ollama_alive:
        router_cfg = RouterConfig(
            router_model=cfg.router.router_model,
            default_model=model_name,
            routes=[
                Route("code",       "codigo",     cfg.router.code_model),
                Route("reasoning",  "raciocinio", cfg.router.reasoning_model),
                Route("tool",       "ferramenta", cfg.router.code_model),
                Route("direct",     "direto",     cfg.router.direct_model),
                Route("orchestrate","orquestrar", cfg.router.reasoning_model),
            ],
        )
        model_router = ModelRouter(_ollama, router_cfg)

    # Sessao persistente — nomeada pelo agent para auto-resume automático.
    # Cada agent tem seu próprio histórico: "agent-<nome>.jsonl"
    # /clear dentro da sessão apaga o histórico e começa do zero.
    try:
        from .sqlite_session_store import SqliteSessionStore
        store = SqliteSessionStore(sessions_dir)
    except Exception:
        from .session_store import SessionStore
        store = SessionStore(sessions_dir)
    sid = f"agent-{ag.name}"
    _prev_msgs = store.load(sid)
    if _prev_msgs:
        console.print(
            f"[dim]Continuando sessao anterior — {len(_prev_msgs)} mensagens. "
            f"Use [bold]/clear[/bold] para reiniciar.[/dim]"
        )

    # Painel de informações do agent
    _ws_display = str(workspace)
    _company_badge = (
        f" | Empresa: [cyan]{_active_company.name}[/cyan]" if _active_company else ""
    )
    console.print(Panel(
        f"[cyan]{ag.name}[/cyan] — {ag.description}\n"
        f"[dim]Modelo: {cfg.model.provider}/{model_name} | "
        f"Tools: {', '.join(list(allowed)[:4])}{'…' if len(allowed) > 4 else ''}"
        f"{_company_badge}[/dim]\n"
        f"[dim]Workspace: {_ws_display}[/dim]",
        title="[bold]Agent Especializado[/bold]",
        border_style="cyan",
    ))

    # ── Resolve o workspace de identidade do agent ─────────────────────────
    # Regra: o agent usa os arquivos de identidade (SOUL/SKILLS/MEMORY/CONTEXT)
    # do workspace onde ele foi ENCONTRADO — não necessariamente o workspace ativo.
    #
    # Exemplos:
    #   alice (agents.yaml da bauer-corp)    → workspace/companies/bauer-corp/workspace/
    #   henrique-ferraz (workspace/agents.yaml global) → workspace/  ← workspace global
    #
    # Isso evita o bug de agents globais não encontrarem seus arquivos quando
    # uma empresa está ativa (que redireciona o workspace de trabalho).
    _agents_file_abs = agents_file.resolve()
    _company_ws_root = _cm.root  # workspace/companies/

    # Verifica se o agents_file está dentro do diretório de alguma empresa
    _identity_ws: Path
    try:
        _rel = _agents_file_abs.relative_to(_company_ws_root.resolve())
        # agents_file está dentro de workspace/companies/<slug>/...
        # → identity workspace = workspace/companies/<slug>/workspace/
        _company_slug = _rel.parts[0]
        _identity_ws = _company_ws_root / _company_slug / "workspace"
    except ValueError:
        # agents_file NÃO está dentro de companies/ → é um agent global
        # → identity workspace = workspace/ (global)
        _identity_ws = Path(_WORKSPACE_DIR)

    _agent_dir = _identity_ws / "agents" / ag.name

    # Verifica arquivos de identidade (silencioso — erros visíveis no prompt)
    if not _agent_dir.exists():
        pass  # agent sem diretório de identidade — sistema prompt padrão é usado

    # Injeta system prompt do agent + contexto da empresa
    from .agent import run_agent_session as _run_session
    from .agent import _build_system_prompt

    _original_build = _build_system_prompt

    def _agent_system_prompt(r):
        base = _original_build(r)
        specialization = f"\n\n# ESPECIALIZACAO\n{ag.system}"
        result = base + specialization

        # ── Injeção de arquivos de identidade do agent ───────────────────────
        # Ordem: SOUL → SKILLS → MEMORY → CONTEXT → Empresa
        # Usa _agent_dir resolvido acima (workspace correto por origem do agent)
        _inject_files = [
            ("SOUL.md",    "ALMA DO AGENTE",
             "Sua identidade, valores e princípios carregados de"),
            ("SKILLS.md",  "HABILIDADES DO AGENTE",
             "Suas habilidades e expertise carregadas de"),
            ("MEMORY.md",  "MEMÓRIA DO AGENTE",
             "Contexto de sessões anteriores carregado de. "
             "Ao encerrar, atualize com novos aprendizados via write_file."),
            ("CONTEXT.md", "CONTEXTO ATIVO",
             "Estado atual do projeto/objetivos carregado de. "
             "Atualize ao encerrar a sessão."),
        ]
        for _fname, _section, _desc in _inject_files:
            _fpath = _agent_dir / _fname
            if _fpath.exists():
                _content = _fpath.read_text(encoding="utf-8").strip()
                if _content:
                    result = result + (
                        f"\n\n# {_section}\n"
                        f"[{_desc} `agents/{ag.name}/{_fname}`]\n\n"
                        f"{_content}"
                    )

        # Prefixa contexto da empresa se houver empresa ativa
        if _active_company:
            result = _cm.inject_context(result, _active_company)

        return result

    import bauer.agent as _agent_mod
    _agent_mod._build_system_prompt = _agent_system_prompt  # type: ignore[attr-defined]

    def _rebuild_client_agent():
        """Reconstrói client + model_name a partir do config.yaml atual (live switch)."""
        from .env_loader import load_dotenv as _lenv
        _lenv()
        _new_cfg, _ = _load_or_die(config, models)
        _new_client = _build_client(_new_cfg)
        return _new_client, _new_cfg.model.name

    try:
        _run_session(
            client, model_name, applied_context, console, router,
            model_router, orchestrator,
            session_store=store, session_id=sid,
            rebuild_client_fn=_rebuild_client_agent,
        )
    finally:
        _agent_mod._build_system_prompt = _original_build  # type: ignore[attr-defined]


@agent_app.command("delete")
def agent_delete(
    name: str = typer.Argument(..., help="Nome do agent a remover"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Pula confirmacao"),
):
    """Remove um agent do registry."""
    from .agent_registry import AgentRegistry
    from rich.prompt import Confirm

    registry = AgentRegistry(agents_file)
    ag = registry.get(name)
    if ag is None:
        console.print(f"[red]Agent '{name}' nao encontrado.[/red]")
        raise typer.Exit(code=1)

    if not yes:
        if not Confirm.ask(f"[yellow]Remover agent '{name}'?[/yellow]", default=False):
            console.print("[dim]Cancelado.[/dim]")
            return

    registry.delete(name)
    console.print(f"[green]✓[/green] Agent [cyan]{name}[/cyan] removido.")


# --- orchestrate ------------------------------------------------------------


def _build_orchestrator_runtime(
    *,
    config: Path,
    models: Path,
    workspace: Path,
    state_file: Path,
    agents_file: Path,
    planner: str = "",
    synthesizer: str = "",
) -> AgentOrchestrator:
    cfg, reg = _load_or_die(config, models)
    _get_or_run_state(cfg, reg, state_file)
    client = _build_client(cfg)

    ollama_client = OllamaClient(cfg.ollama.host, cfg.ollama.timeout_seconds, cfg.ollama.api_key)
    alive, alive_msg = ollama_client.is_alive()
    if not alive:
        if cfg.model.provider == "ollama":
            console.print(
                f"[red]Ollama em {cfg.ollama.host} nao esta respondendo: {alive_msg}[/red]\n"
                f"Verifique se o servidor Ollama esta rodando."
            )
            raise typer.Exit(code=1)
        ollama_client = client

    workspace.mkdir(parents=True, exist_ok=True)
    tool_router = _build_router(cfg, workspace)
    is_ollama = cfg.model.provider == "ollama"
    main_model = cfg.model.name
    code_model = cfg.router.code_model if is_ollama else main_model
    reasoning_model = cfg.router.reasoning_model if is_ollama else main_model
    direct_model = cfg.router.direct_model if is_ollama else main_model
    router_cfg = RouterConfig(
        router_model=cfg.router.router_model,
        default_model=cfg.model.name,
        routes=[
            Route("code", "codigo", code_model),
            Route("reasoning", "raciocinio", reasoning_model),
            Route("tool", "ferramenta", code_model),
            Route("direct", "direto", direct_model),
        ],
    )
    model_router = ModelRouter(ollama_client, router_cfg)
    orch_cfg = OrchestratorConfig(
        planner_model=planner or (cfg.router.router_model if is_ollama else cfg.model.name),
        synthesizer_model=synthesizer or (cfg.router.reasoning_model if is_ollama else cfg.model.name),
        max_steps=MAX_STEPS,
        parallel_steps=cfg.runtime.profile == "high",
        agents_file=str(agents_file),
    )
    return AgentOrchestrator(
        client,
        tool_router,
        model_router,
        orch_cfg,
        planner_client=ollama_client,
        console=console,
    )


@orchestrate_app.command("run")
def orchestrate_run(
    task: str = typer.Argument("", help="Descricao da tarefa — omita para modo entrevista"),
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    models: Path = typer.Option(Path("models.yaml"), "--models", help="Caminho do models.yaml"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(
        Path(".runtime_state.json"),
        "--state-file",
        help="Runtime state (gerado pelo doctor)",
    ),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
    agent_name: str = typer.Option("", "--agent", "-a", help="Agent especializado a usar (ex: python-expert)"),
    planner: str = typer.Option("", "--planner", help="Modelo planejador (padrao: qwen3:0.6b)"),
    synthesizer: str = typer.Option("", "--synthesizer", help="Modelo sintetizador (padrao: phi4-mini)"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Modo passo-a-passo (confirma cada onda antes de executar)"),
    resume: bool = typer.Option(False, "--resume", "-r", help="Retoma execucao anterior interrompida"),
    mode: str = typer.Option("sync", "--mode", help="sync | hybrid | durable"),
    node_runtime: str = typer.Option("auto", "--node-runtime", help="auto | inline | dispatcher"),
    background: bool = typer.Option(False, "--background", help="Submete nodes ao dispatcher e retorna sem bloquear"),
    run_id: str = typer.Option("", "--run-id", help="ID de orchestration_run para retomar/forcar"),
):
    """Executa tarefa complexa com orquestrador multi-passo (DAG + paralelo).

    Planeja, executa passos independentes em paralelo e sintetiza o resultado.
    Progresso e salvo automaticamente — use --resume para retomar se interrompido.

    Exemplos:
      bauer orchestrate run "crie um script que leia dados.csv e calcule estatisticas"
      bauer orchestrate run "pesquise sobre IA, salve o resumo em um arquivo" --interactive
      bauer orchestrate run "tarefa longa" --resume
    """
    cfg, reg = _load_or_die(config, models)

    # Modo entrevista: sem tarefa → wizard interativo
    if not task:
        from .agent_wizard import wizard_orchestrate
        result = wizard_orchestrate()
        if result is None:
            raise typer.Exit(code=0)
        task = result["task"]
        if result["agent"] and not agent_name:
            agent_name = result["agent"]
        if result["interactive"]:
            interactive = True
        if result["resume"]:
            resume = True

    # Auto-seleção de agent por capability matching (se não especificado)
    if not agent_name:
        try:
            from .agent_registry import AgentRegistry as _AR
            _reg = _AR(agents_file)
            _matched = _reg.auto_select(task)
            if _matched:
                console.print(
                    f"[dim]Auto-selecionado: agent [cyan]{_matched.name}[/cyan] "
                    f"— {_matched.description[:60]}[/dim]"
                )
                agent_name = _matched.name
        except Exception:
            pass

    # Aplica system prompt do agent especializado (se especificado)
    _agent_system_patch = None
    if agent_name:
        from .agent_registry import AgentRegistry
        from .agent import _build_system_prompt as _bsp
        reg_agents = AgentRegistry(agents_file)
        ag = reg_agents.get(agent_name)
        if ag:
            console.print(f"[dim]Agent: [cyan]{ag.name}[/cyan] — {ag.description}[/dim]")
            _orig_bsp = _bsp

            def _patched_bsp(r):
                base = _orig_bsp(r) + f"\n\n# ESPECIALIZACAO\n{ag.system}"
                _mem = workspace / "agents" / ag.name / "MEMORY.md"
                if _mem.exists():
                    _mc = _mem.read_text(encoding="utf-8").strip()
                    if _mc:
                        base += (
                            f"\n\n# MEMÓRIA DO AGENTE\n"
                            f"Conteúdo carregado de `agents/{ag.name}/MEMORY.md`:\n\n{_mc}"
                        )
                return base

            import bauer.agent as _agent_mod
            _agent_mod._build_system_prompt = _patched_bsp  # type: ignore[attr-defined]
            _agent_system_patch = (_agent_mod, _orig_bsp)
        else:
            console.print(f"[yellow]Agent '[cyan]{agent_name}[/cyan]' nao encontrado.[/yellow]")
            if typer.confirm(f"Criar o agent '{agent_name}' agora?", default=True):
                from .agent_wizard import wizard_create_agent
                _cfg_tmp, _ = _load_or_die(config, models)
                _created_ag = wizard_create_agent(
                    reg_agents,
                    config_model=_cfg_tmp.model.name,
                    config_provider=_cfg_tmp.model.provider,
                )
                if _created_ag:
                    ag = _created_ag
                    _orig_bsp2 = _bsp

                    def _patched_bsp2(r):
                        base2 = _orig_bsp2(r) + f"\n\n# ESPECIALIZACAO\n{ag.system}"  # type: ignore[union-attr]
                        _mem2 = workspace / "agents" / ag.name / "MEMORY.md"  # type: ignore[union-attr]
                        if _mem2.exists():
                            _mc2 = _mem2.read_text(encoding="utf-8").strip()
                            if _mc2:
                                base2 += (
                                    f"\n\n# MEMÓRIA DO AGENTE\n"
                                    f"Conteúdo carregado de `agents/{ag.name}/MEMORY.md`:\n\n{_mc2}"  # type: ignore[union-attr]
                                )
                        return base2

                    import bauer.agent as _agent_mod2
                    _agent_mod2._build_system_prompt = _patched_bsp2  # type: ignore[attr-defined]
                    _agent_system_patch = (_agent_mod2, _orig_bsp2)
                    console.print(f"[green]✓[/green] Agent [cyan]{ag.name}[/cyan] criado e aplicado.")
                else:
                    console.print("[dim]Agent nao criado — usando agent padrao.[/dim]")
            else:
                console.print("[dim]Usando agent padrao.[/dim]")

    state = _get_or_run_state(cfg, reg, state_file)

    # Cliente principal (pode ser Ollama, OpenAI, etc.)
    client = _build_client(cfg)

    # Cliente Ollama separado para roteamento/planejamento (sempre local quando disponivel)
    # Se Ollama nao estiver rodando E o provider principal nao for Ollama,
    # usamos o proprio client principal como planejador (fallback gracioso).
    from bauer.ollama_client import OllamaClient as _OllamaClient
    ollama_client = _OllamaClient(cfg.ollama.host, cfg.ollama.timeout_seconds, cfg.ollama.api_key)
    alive, _alive_msg = ollama_client.is_alive()
    _ollama_available = alive

    if not _ollama_available:
        if cfg.model.provider == "ollama":
            # Provider e Ollama mas servidor down → erro fatal
            console.print(
                f"[red]Ollama em {cfg.ollama.host} nao esta respondendo: {_alive_msg}[/red]\n"
                f"Verifique se o servidor Ollama esta rodando."
            )
            raise typer.Exit(code=1)
        # Fallback: usa o client principal (cloud) tambem para planejamento/roteamento
        console.print(
            f"[dim]Ollama indisponivel em {cfg.ollama.host} — "
            f"usando [cyan]{cfg.model.provider}[/cyan] para planejar e sintetizar.[/dim]"
        )
        ollama_client = client  # sinaliza para o resto do fluxo usar o client principal

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)

    tool_router = _build_router(cfg, workspace)

    # ModelRouter usa o Ollama client (modelos locais de roteamento)
    # Para providers cloud, todas as rotas de execucao usam o modelo cloud
    _orch_is_ollama = cfg.model.provider == "ollama"
    _orch_main_model = cfg.model.name
    _orch_code   = cfg.router.code_model      if _orch_is_ollama else _orch_main_model
    _orch_reason = cfg.router.reasoning_model if _orch_is_ollama else _orch_main_model
    _orch_direct = cfg.router.direct_model    if _orch_is_ollama else _orch_main_model

    router_cfg = RouterConfig(
        router_model=cfg.router.router_model,
        default_model=cfg.model.name,
        routes=[
            Route("code",      "codigo",     _orch_code),
            Route("reasoning", "raciocinio", _orch_reason),
            Route("tool",      "ferramenta", _orch_code),
            Route("direct",    "direto",     _orch_direct),
        ],
    )
    model_router = ModelRouter(ollama_client, router_cfg)

    # Paralelo apenas no perfil high (GPU/alta RAM)
    _parallel = cfg.runtime.profile == "high"

    # Quando Ollama nao esta disponivel, usamos o modelo do client principal
    # para planejamento e sintese tambem — modelos Ollama (qwen3, phi4-mini) nao existem.
    if _ollama_available:
        _planner = planner or cfg.router.router_model
        _synthesizer = synthesizer or cfg.router.reasoning_model
    else:
        _planner = planner or cfg.model.name
        _synthesizer = synthesizer or cfg.model.name

    orch_cfg = OrchestratorConfig(
        planner_model=_planner,
        synthesizer_model=_synthesizer,
        max_steps=MAX_STEPS,
        parallel_steps=_parallel,
        agents_file=str(agents_file),
    )
    orch = AgentOrchestrator(
        client, tool_router, model_router, orch_cfg,
        planner_client=ollama_client,
        console=console,
    )

    # Carrega lista de agents para o planejador
    from .agent_registry import AgentRegistry as _AgentRegistry
    _agents_list = _AgentRegistry(agents_file).list_agents()

    # Carrega specs aprovados/implementados para o planejador respeitar contratos
    from .spec_manager import SpecManager as _SpecManager
    _specs_list = _SpecManager(_SPECS_DIR).list_specs()
    _active_specs = [s for s in _specs_list if s.status in ("approved", "implemented")]
    if _active_specs:
        console.print(f"[dim]Specs ativos: {', '.join(s.id for s in _active_specs)}[/dim]")

    # --- Execucao com tratamento de erros ---
    try:

        # --- Planejamento (ou carrega plano salvo) ---
        console.print(Rule("[bold]Orquestrador[/bold]"))
        console.print(f"[dim]Tarefa:[/dim] {task}\n")

        _mode = (mode or "sync").strip().lower()
        if _mode not in {"sync", "hybrid", "durable"}:
            console.print("[red]--mode invalido. Use sync, hybrid ou durable.[/red]")
            raise typer.Exit(code=2)
        _node_runtime = (node_runtime or "auto").strip().lower()
        if _node_runtime not in {"auto", "inline", "dispatcher"}:
            console.print("[red]--node-runtime invalido. Use auto, inline ou dispatcher.[/red]")
            raise typer.Exit(code=2)
        if background and not (_mode == "durable" or _node_runtime == "dispatcher"):
            console.print("[red]--background requer --mode durable ou --node-runtime dispatcher.[/red]")
            raise typer.Exit(code=2)

        if _mode in {"hybrid", "durable"}:
            from .execution_engine import DurableDAGExecutionEngine

            console.print(
                f"[yellow]ExecutionEngine duravel ativo:[/yellow] "
                f"mode={_mode} node_runtime={_node_runtime}"
            )
            engine = DurableDAGExecutionEngine(
                orch,
                workspace=workspace,
                mode=_mode,
                node_runtime=_node_runtime,
            )
            if background:
                result = engine.submit(
                    task,
                    resume=resume or bool(run_id),
                    run_id=run_id,
                    agents=_agents_list or None,
                    specs=_active_specs or None,
                )
            else:
                result = engine.run(
                    task,
                    resume=resume or bool(run_id),
                    run_id=run_id,
                    agents=_agents_list or None,
                    specs=_active_specs or None,
                )
            console.print(
                f"[dim]orchestration_run:[/dim] [cyan]{result.run_id}[/cyan] "
                f"status={result.status} runtime={result.node_runtime} steps={len(result.results)}"
            )
            if background:
                console.print(
                    "[green]Run submetido ao dispatcher.[/green] "
                    "Use [bold]bauer dispatch daemon[/bold] para processar em background."
                )
                if _agent_system_patch:
                    _mod, _orig = _agent_system_patch
                    _mod._build_system_prompt = _orig  # type: ignore[attr-defined]
                return
            console.print(Rule("[bold]Resultado Final[/bold]"))
            sys.stdout.write("\033[32morchestrate>\033[0m ")
            sys.stdout.write(result.final)
            sys.stdout.write("\n\n")
            sys.stdout.flush()
            if _agent_system_patch:
                _mod, _orig = _agent_system_patch
                _mod._build_system_prompt = _orig  # type: ignore[attr-defined]
            return

        if resume and orch.has_saved_progress(task):
            saved_steps = orch.load_plan(task)
            if saved_steps:
                steps = saved_steps
                console.print("[yellow]Retomando execucao anterior...[/yellow]")
            else:
                steps = None
        else:
            steps = None

        if not steps:
            if _agents_list:
                console.print(f"[dim]Agents disponiveis para o planejador: {', '.join(a.name for a in _agents_list)}[/dim]")
            console.print("[yellow]Planejando passos...[/yellow]")
            steps = orch.plan(task, agents=_agents_list or None, specs=_active_specs or None)
            orch.save_plan(task, steps)

        if not steps:
            console.print("[red]Nao foi possivel decompor a tarefa em passos.[/red]")
            raise typer.Exit(code=1)

        # Exibe plano com indicacao de dependencias e agent designado
        batches = orch._topological_batches(steps)
        _mode_label = "paralelo" if orch_cfg.parallel_steps else "sequencial"
        console.print(f"\n[bold]Plano ({len(steps)} passos, {len(batches)} onda(s)) [{_mode_label}]:[/bold]")
        for wave_idx, batch in enumerate(batches):
            can_parallel = len(batch) > 1 and orch_cfg.parallel_steps
            wave_label = f"  Onda {wave_idx + 1}" + (" [paralelo]" if can_parallel else "")
            console.print(f"[dim]{wave_label}:[/dim]")
            for s in batch:
                tools_tag = "[cyan][tools][/cyan]" if s.get("tools") else ""
                deps = s.get("depends_on", [])
                deps_tag = f"[dim](dep: {deps})[/dim]" if deps else ""
                agent_tag = f"[magenta][{s['agent']}][/magenta]" if s.get("agent") else ""
                console.print(f"    {s['id']}. {s['goal']} {tools_tag}{agent_tag} {deps_tag}")

        # Confirmacao do plano (sempre — nao apenas em modo interativo)
        if not typer.confirm("\nExecutar plano?", default=True):
            console.print("[dim]Cancelado.[/dim]")
            orch.clear_progress(task)
            if _agent_system_patch:
                _mod, _orig = _agent_system_patch
                _mod._build_system_prompt = _orig  # type: ignore[attr-defined]
            return

        # Registra passos como tarefas no TASKS.md (se workspace inicializado)
        _task_ids: dict[int, str] = {}
        try:
            _wm_orch = WorkspaceManager(workspace)
            if _wm_orch.tasks_file.exists():
                _spec_mgr_orch = _SpecManager(_SPECS_DIR) if _active_specs else None
                for s in steps:
                    _step_spec_id = ""
                    if _spec_mgr_orch:
                        _relevant = _spec_mgr_orch.find_relevant(s["goal"], max_results=1)
                        if _relevant:
                            _step_spec_id = _relevant[0].id
                    _t = _wm_orch.add_task(
                        f"[Orch] {s['goal']}",
                        description=f"Passo {s['id']} do plano: {task}",
                        spec_id=_step_spec_id,
                    )
                    _task_ids[s["id"]] = _t.id
                    _wm_orch.update_task_status(_t.id, "IN_PROGRESS")
                if _task_ids:
                    console.print(f"[dim]{len(_task_ids)} tarefa(s) registradas em TASKS.md[/dim]")
        except Exception:
            pass  # workspace nao inicializado — silenciosamente ignora

        # --- Execucao em ondas ---
        done: dict = {r.id: r for r in (orch.load_progress(task) if resume else [])}
        all_results: list = list(done.values())

        for wave_idx, batch in enumerate(batches):
            pending = [s for s in batch if s["id"] not in done]

            # Passos ja concluidos nesta onda (retomados do cache)
            cached = [s for s in batch if s["id"] in done]
            if cached:
                for s in cached:
                    console.print(f"  [dim]Passo {s['id']} (retomado do cache)[/dim]")

            if not pending:
                continue

            # Cabecalho da onda
            if len(pending) > 1 and orch_cfg.parallel_steps:
                ids = ", ".join(str(s["id"]) for s in pending)
                console.print(f"\n[bold]Onda {wave_idx + 1} — Passos {ids} (paralelo):[/bold]")
                for s in pending:
                    tools_tag = "[cyan][tools][/cyan]" if s.get("tools") else ""
                    agent_tag = f" [magenta][{s['agent']}][/magenta]" if s.get("agent") else ""
                    console.print(f"  {s['id']}. {s['goal']} {tools_tag}{agent_tag}")
            else:
                s = pending[0]
                tools_tag = "[cyan][tools][/cyan]" if s.get("tools") else ""
                agent_tag = f" [magenta][{s['agent']}][/magenta]" if s.get("agent") else ""
                console.print(f"\n[bold]Passo {s['id']}:[/bold] {s['goal']} {tools_tag}{agent_tag}")

            if interactive:
                if not typer.confirm(f"Executar onda {wave_idx + 1}?", default=True):
                    console.print("[dim]Interrompido pelo usuario. Use --resume para continuar.[/dim]")
                    if _task_ids:
                        try:
                            for s in pending:
                                if s["id"] in _task_ids:
                                    _wm_orch.update_task_status(_task_ids[s["id"]], "BLOCKED")
                        except Exception:
                            pass
                    break

            batch_results = orch.execute_parallel_steps(pending, all_results)
            all_results.extend(batch_results)
            orch.save_progress(task, batch_results)
            for r in batch_results:
                done[r.id] = r

            # Atualiza status de cada passo concluido no TASKS.md
            if _task_ids:
                try:
                    for r in batch_results:
                        if r.id in _task_ids:
                            new_status = "BLOCKED" if r.model_used == "(erro)" else "DONE"
                            _wm_orch.update_task_status(_task_ids[r.id], new_status)
                except Exception:
                    pass

            # Exibe resultado de cada passo da onda
            for r in batch_results:
                step_used_tools = any(s["id"] == r.id and s.get("tools") for s in pending)
                if len(pending) > 1:
                    console.print(f"  [bold]Passo {r.id}[/bold] [dim](modelo: {r.model_used})[/dim]")
                else:
                    console.print(f"  [dim]Modelo: {r.model_used}[/dim]")
                if r.tool_log:
                    for tl in r.tool_log:
                        console.print(f"  [dim]  -> {tl['tool']}[/dim]")
                if step_used_tools or not r.tool_log and r.model_used != "(erro)":
                    if step_used_tools:
                        preview = r.response[:400].replace("\n", " ")
                        suffix = "..." if len(r.response) > 400 else ""
                        console.print(f"  [green]{preview}{suffix}[/green]")
                if r.model_used == "(erro)":
                    console.print(f"  [red]{r.response}[/red]")

        # --- Sintese ---
        console.print("\n[yellow]Sintetizando resultados...[/yellow]")
        goal_text = steps[0].get("goal", task)
        final = orch.synthesize(goal_text, all_results)
        orch.clear_progress(task)

        # Marca tarefas restantes como DONE no TASKS.md (ex: se interrompido antes)
        if _task_ids:
            try:
                for step_id, tid in _task_ids.items():
                    if step_id in done:
                        r = done[step_id]
                        if r.model_used != "(erro)":
                            _wm_orch.update_task_status(tid, "DONE")
            except Exception:
                pass

        console.print(Rule("[bold]Resultado Final[/bold]"))
        sys.stdout.write("\033[32morchestrate>\033[0m ")
        sys.stdout.write(final)
        sys.stdout.write("\n\n")
        sys.stdout.flush()

    except Exception as exc:
        from .openai_client import OpenAIClientError as _OCE
        from .ollama_client import OllamaError as _OE
        _err_type = "Ollama" if isinstance(exc, _OE) else "Provider" if isinstance(exc, _OCE) else "Erro"
        console.print(f"\n[red]{_err_type} no orquestrador:[/red] {exc}")
        console.print("[dim]Use --resume para retomar de onde parou.[/dim]")

    # Restaura system prompt original se foi patchado pelo agent
    if _agent_system_patch:
        _mod, _orig = _agent_system_patch
        _mod._build_system_prompt = _orig  # type: ignore[attr-defined]


@orchestrate_app.command("node-worker")
def orchestrate_node_worker(
    run_id: str = typer.Argument(..., help="ID do orchestration_run"),
    step_id: int = typer.Argument(..., help="ID do step/node dentro do plano"),
    task_id: str = typer.Option("", "--task-id", help="Task Kanban claimed pelo dispatcher"),
    claim_id: str = typer.Option("", "--claim-id", help="Claim id esperado"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(Path(".runtime_state.json"), "--state-file"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
    planner: str = typer.Option("", "--planner"),
    synthesizer: str = typer.Option("", "--synthesizer"),
):
    """Worker interno: executa um node persistido de uma orchestration_run."""
    from .execution_engine import run_orchestration_node

    orch = _build_orchestrator_runtime(
        config=config,
        models=models,
        workspace=workspace,
        state_file=state_file,
        agents_file=agents_file,
        planner=planner,
        synthesizer=synthesizer,
    )
    try:
        result = run_orchestration_node(
            orch,
            workspace=workspace,
            run_id=run_id,
            step_id=step_id,
            task_id=task_id,
            claim_id=claim_id,
        )
    except Exception as exc:
        console.print(f"[red]Erro no node-worker:[/red] {exc}")
        raise typer.Exit(code=1)

    console.print(
        f"[dim]orchestration_run:[/dim] [cyan]{result.run_id}[/cyan] "
        f"step={result.step_id} status={result.status} "
        f"orchestration={result.orchestration_status}"
    )
    if result.final:
        console.print(Rule("[bold]Resultado Final[/bold]"))
        sys.stdout.write("\033[32morchestrate>\033[0m ")
        sys.stdout.write(result.final)
        sys.stdout.write("\n\n")
    else:
        sys.stdout.write(result.step_result.response[:2000])
        sys.stdout.write("\n")
    sys.stdout.flush()
    if result.status == "failed":
        raise typer.Exit(code=1)


@orchestrate_app.command("advance")
def orchestrate_advance(
    run_id: str = typer.Argument(..., help="ID do orchestration_run"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(Path(".runtime_state.json"), "--state-file"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
):
    """Avanca uma orchestration_run duravel: enfileira proximos nodes ou sintetiza final."""
    from .execution_engine import DurableDAGExecutionEngine
    from .orchestration_store import OrchestrationStore

    store = OrchestrationStore(workspace)
    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]orchestration_run nao encontrado:[/red] {run_id}")
        raise typer.Exit(code=1)
    orch = _build_orchestrator_runtime(
        config=config,
        models=models,
        workspace=workspace,
        state_file=state_file,
        agents_file=agents_file,
    )
    engine = DurableDAGExecutionEngine(
        orch,
        workspace=workspace,
        mode=run.mode or "durable",
        node_runtime=run.metadata.get("node_runtime") or "dispatcher",
    )
    result = engine.advance(run_id)
    console.print(
        f"[dim]orchestration_run:[/dim] [cyan]{result.run_id}[/cyan] "
        f"status={result.status} runtime={result.node_runtime} steps_done={len(result.results)}"
    )
    if result.final:
        console.print(Rule("[bold]Resultado Final[/bold]"))
        sys.stdout.write("\033[32morchestrate>\033[0m ")
        sys.stdout.write(result.final)
        sys.stdout.write("\n\n")
        sys.stdout.flush()


@orchestrate_app.command("resume")
def orchestrate_resume(
    run_id: str = typer.Argument(..., help="ID do orchestration_run"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(Path(".runtime_state.json"), "--state-file"),
    agents_file: Path = typer.Option(Path("agents.yaml"), "--agents"),
):
    """Retoma uma execucao duravel pelo run_id."""
    from .orchestration_store import OrchestrationStore

    store = OrchestrationStore(workspace)
    run = store.get_run(run_id)
    if run is None:
        console.print(f"[red]orchestration_run nao encontrado:[/red] {run_id}")
        raise typer.Exit(code=1)
    orchestrate_run(
        task=run.objective,
        config=config,
        models=models,
        workspace=workspace,
        state_file=state_file,
        agents_file=agents_file,
        agent_name="",
        planner="",
        synthesizer="",
        interactive=False,
        resume=True,
        mode=run.mode or "hybrid",
        node_runtime="auto",
        background=False,
        run_id=run_id,
    )


@orchestrate_app.command("list")
def orchestrate_list(
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    durable: bool = typer.Option(True, "--durable/--legacy-only", help="Inclui runs duraveis"),
):
    """Lista tarefas do orquestrador com progresso salvo (prontas para --resume)."""
    from .orchestrator import AgentOrchestrator, OrchestratorConfig
    from unittest.mock import MagicMock as _MM

    # Cria instância mínima só para usar list_saved_progress
    orch = AgentOrchestrator(_MM(), _MM(), _MM(), OrchestratorConfig())

    entries = orch.list_saved_progress()
    durable_runs = []
    if durable:
        try:
            from .orchestration_store import OrchestrationStore

            durable_runs = OrchestrationStore(workspace).list_runs(limit=20)
        except Exception:
            durable_runs = []

    if not entries and not durable_runs:
        console.print("[dim]Nenhuma tarefa com progresso salvo em .orchestrate_progress/[/dim]")
        console.print("[dim]Nenhuma orchestration_run duravel encontrada.[/dim]")
        console.print("[dim]Tarefas aparecem aqui quando interrompidas antes de concluir.[/dim]")
        return

    if durable_runs:
        durable_table = Table(title=f"Runs duraveis ({len(durable_runs)})", show_lines=True)
        durable_table.add_column("Run", style="cyan")
        durable_table.add_column("Status")
        durable_table.add_column("Mode")
        durable_table.add_column("Steps", justify="right")
        durable_table.add_column("Objetivo", style="bold")
        for run in durable_runs:
            durable_table.add_row(
                run.run_id,
                run.status,
                run.mode,
                str(len(run.plan)),
                run.objective[:70],
            )
        console.print(durable_table)

    if not entries:
        console.print("\n[dim]Para retomar run duravel: [bold]bauer orchestrate resume <run_id>[/bold][/dim]")
        return

    table = Table(title=f"Progresso legado ({len(entries)})", show_lines=True)
    table.add_column("Tarefa", style="bold")
    table.add_column("Progresso", style="cyan")
    table.add_column("Criado", style="dim")
    table.add_column("Hash", style="dim", width=12)

    for e in entries:
        progress = f"{e['steps_done']}/{e['steps_total']} passos"
        table.add_row(
            e["task"][:70],
            progress,
            e["created"],
            e["hash"],
        )

    console.print(table)
    console.print("\n[dim]Para retomar: [bold]bauer orchestrate run \"<tarefa>\" --resume[/bold][/dim]")


@orchestrate_app.command("cancel")
def orchestrate_cancel(
    task: str = typer.Argument("", help="Texto da tarefa a cancelar (ou 'all' para todas)"),
    all_tasks: bool = typer.Option(False, "--all", "-a", help="Cancela todas as tarefas salvas"),
    force: bool = typer.Option(False, "--force", "-f", help="Sem confirmacao interativa"),
):
    """Cancela tarefa(s) do orquestrador removendo progresso salvo."""
    import shutil
    from .orchestrator import AgentOrchestrator, OrchestratorConfig
    from unittest.mock import MagicMock as _MM

    orch = AgentOrchestrator(_MM(), _MM(), _MM(), OrchestratorConfig())

    if all_tasks:
        entries = orch.list_saved_progress()
        if not entries:
            console.print("[dim]Nenhuma tarefa salva para cancelar.[/dim]")
            return
        if not force:
            console.print(f"[yellow]Remover {len(entries)} tarefa(s) salva(s)?[/yellow]")
            if not typer.confirm("Confirmar?", default=False):
                console.print("[dim]Cancelamento abortado.[/dim]")
                return
        base = Path(".orchestrate_progress")
        if base.exists():
            shutil.rmtree(base)
        console.print(f"[green]{len(entries)} tarefa(s) cancelada(s).[/green]")
        return

    if not task:
        console.print("[red]Especifique a tarefa ou use --all.[/red]")
        raise typer.Exit(1)

    if orch.has_saved_progress(task):
        if not force:
            console.print(f"[yellow]Remover progresso de: '{task[:60]}'?[/yellow]")
            if not typer.confirm("Confirmar?", default=False):
                console.print("[dim]Cancelamento abortado.[/dim]")
                return
        orch.clear_progress(task)
        console.print(f"[green]Progresso de '{task[:60]}' removido.[/green]")
    else:
        console.print(f"[yellow]Nenhum progresso salvo para: '{task[:60]}'[/yellow]")


# --- project ----------------------------------------------------------------

_PROJECT_WORKSPACE = Path("workspace")


@project_app.command("init")
def project_init(
    name: str = typer.Argument(..., help="Nome do projeto"),
    description: str = typer.Option("", "--desc", help="Descricao do projeto"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Inicializa o workspace com PROJECT.md e TASKS.md."""
    wm = WorkspaceManager(workspace)
    created = wm.init_project(name, description)
    if created:
        for p in created:
            console.print(f"[green]criado:[/green] {p}")
    else:
        console.print(f"[dim]Projeto ja inicializado em {workspace}/[/dim]")


@project_app.command("status")
def project_status(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Mostra PROJECT.md e resumo de tarefas."""
    wm = WorkspaceManager(workspace)
    console.print(wm.get_project_info())

    tasks = wm.list_tasks()
    if not tasks:
        console.print("[dim]Nenhuma tarefa registrada ainda.[/dim]")
        return

    from collections import Counter
    counts = Counter(t.status for t in tasks)
    summary = "  ".join(f"{s}: {n}" for s, n in sorted(counts.items()))
    console.print(f"[dim]Tarefas — {summary} | Total: {len(tasks)}[/dim]")


@project_app.command("board")
def project_board(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    refresh: int = typer.Option(3, "--refresh", "-r", help="Intervalo de atualizacao em segundos"),
):
    """Kanban ao vivo no terminal — atualiza automaticamente via Rich Live.

    Exibe as tarefas do TASKS.md em colunas por status.
    Pressione Ctrl+C para sair.
    """
    import time
    from collections import Counter
    from rich.live import Live
    from rich.panel import Panel as RPanel
    from rich.columns import Columns
    from rich.text import Text

    _STATUS_ORDER = ["TODO", "IN_PROGRESS", "BLOCKED", "DONE"]
    _STATUS_LABEL = {
        "TODO":        ("TODO",          "blue"),
        "IN_PROGRESS": ("EM PROGRESSO",  "yellow"),
        "BLOCKED":     ("BLOQUEADO",     "red"),
        "DONE":        ("CONCLUIDO",     "green"),
    }
    _COMPACT_THRESHOLD = 8

    def _build_board():
        wm = WorkspaceManager(workspace)
        tasks = wm.list_tasks()
        by_status: dict[str, list] = {s: [] for s in _STATUS_ORDER}
        for t in tasks:
            if t.status in by_status:
                by_status[t.status].append(t)

        panels = []
        for status in _STATUS_ORDER:
            label, color = _STATUS_LABEL[status]
            col_tasks = by_status[status]
            compact = len(col_tasks) > _COMPACT_THRESHOLD
            # Para DONE compacto: mostra só os 8 mais recentes
            visible = col_tasks[-_COMPACT_THRESHOLD:] if compact else col_tasks
            hidden = len(col_tasks) - len(visible)

            lines = Text()
            if hidden > 0:
                lines.append(f"  + {hidden} anteriores...\n", style="dim")
            if not col_tasks:
                lines.append("  (vazio)\n", style="dim")
            for t in visible:
                lines.append(f"  #{t.id} ", style="dim")
                title = t.title if len(t.title) <= 38 else t.title[:35] + "..."
                lines.append(f"{title}\n", style="bold" if status == "IN_PROGRESS" else "")

            count_label = f" ({len(col_tasks)})"
            panels.append(RPanel(
                lines,
                title=f"[{color}]{label}{count_label}[/{color}]",
                border_style=color,
                padding=(0, 1),
            ))

        ts = time.strftime("%H:%M:%S")
        return Columns(panels, equal=True, expand=True), ts

    console.print(f"\n[dim]Kanban ao vivo — {workspace}/TASKS.md "
                  f"(atualiza a cada {refresh}s — Ctrl+C para sair)[/dim]\n")

    with Live(console=console, refresh_per_second=1, screen=False) as live:
        while True:
            try:
                board, ts = _build_board()
                from rich.console import Group
                live.update(Group(board, Text(f"  Atualizado: {ts}", style="dim")))
                time.sleep(refresh)
            except KeyboardInterrupt:
                break

    console.print("\n[dim]Board encerrado.[/dim]")



# --- bauer tui (TUI moderno) -------------------------------------------------


@app.command("tui")
def tui_cmd(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    workspace: Path = typer.Option(Path("workspace"), "--workspace", help="Diretório workspace do agente"),
    theme: str = typer.Option("default", "--theme", help="Tema visual: default, mono, dark"),
):
    """Abre a Terminal UI moderna do Bauer Agent (prompt_toolkit).

    Interface TUI com:
    - Painel de histórico scrollável
    - Input fixo no rodapé com autohistórico
    - Streaming de output via append_token
    - Temas: default (Catppuccin), mono, dark
    - Atalhos: Enter=enviar, Ctrl+C=interromper, Ctrl+L=limpar, F1=ajuda, /exit=sair
    """
    try:
        from .tui import make_tui
    except ImportError as exc:
        console.print("[red]X Erro ao carregar TUI.[/red]")
        console.print(f"[dim]{exc}[/dim]")
        console.print("[yellow]Dica: pip install prompt-toolkit[/yellow]")
        raise typer.Exit(1)

    try:
        cfg = load_config(config)
    except ConfigError as exc:
        console.print(f"[red]Config inválido:[/red] {exc}")
        raise typer.Exit(2)

    from .agent import run_one_turn, _build_system_prompt
    from .context_manager import ContextManager

    client = _build_client(cfg)
    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
    router = _build_router(cfg, workspace, llm_client=client)
    model_name = cfg.model.name

    _provider = getattr(client, "_provider", None) or (
        "ollama" if hasattr(client, "host") and "ollama" in getattr(client, "host", "").lower()
        else "openai"
    )
    ctx = ContextManager(
        applied_context=cfg.model.requested_context or 8192,
        system_prompt=_build_system_prompt(router),
        provider=_provider,
    )
    ctx.set_llm(client, model_name)

    history_path = Path.home() / ".bauer" / ".tui_history"
    history_path.parent.mkdir(parents=True, exist_ok=True)

    def handler(user_input: str) -> str:
        ctx.add_user(user_input)
        try:
            response, _ = run_one_turn(ctx, router, client, model_name)
            return response
        except Exception as exc:  # noqa: BLE001
            return f"[Erro: {exc}]"

    tui = make_tui(
        handler,
        theme=theme,
        history_file=str(history_path),
        model_name=f"{model_name} ({cfg.model.provider})",
    )
    tui.run()


# --- kanban (browser ao vivo) -----------------------------------------------


@app.command("kanban")
def kanban_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    company: str = typer.Option("", "--company", "-c", help="Slug da empresa (ex: acme-corp)"),
    port: int = typer.Option(7780, "--port", "-p", help="Porta do servidor Kanban"),
    host: str = typer.Option("127.0.0.1", "--host", help="Interface de escuta"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Nao abre o browser automaticamente"),
    background: bool = typer.Option(False, "--background", "-b", help="Roda em background e libera o terminal"),
):
    """Kanban ao vivo no browser — sem precisar do bauer serve.

    Sobe um mini servidor HTTP local, abre o browser automaticamente
    e atualiza o board a cada 3s lendo o TASKS.md.

    Sem --company: usa o workspace padrao (workspace/).
    Com --company acme-corp: usa companies/acme-corp/workspace/.

    Use --background / -b para liberar o terminal apos exibir a URL.
    """
    from .kanban_server import run_kanban_server
    from .company_manager import CompanyManager as _KanbanCM

    company_name = ""
    _kanban_cm = _KanbanCM(_COMPANIES_DIR)

    # Se --company não foi passado, tenta detectar empresa ativa automaticamente
    if not company:
        _active = _kanban_cm.get_active()
        if _active:
            company = _active.id
            console.print(f"[dim]Empresa ativa detectada: [cyan]{_active.name}[/cyan][/dim]")

    if company:
        import yaml as _yaml

        # Procura a pasta da empresa: canonical (workspace/companies/) + legacy (companies/)
        _candidates = [
            _COMPANIES_DIR,          # workspace/companies/
            Path("companies"),       # legacy — raiz do projeto
        ]
        _company_dir: Path | None = None
        for _cdir in _candidates:
            _d = _cdir / company
            if (_d / "company.yaml").exists():
                _company_dir = _d
                break

        if _company_dir is None:
            # Lista empresas disponíveis em todos os candidatos
            _found = []
            for _cdir in _candidates:
                if _cdir.exists():
                    _found += [d.name for d in _cdir.iterdir()
                               if d.is_dir() and (d / "company.yaml").exists()]
            console.print(f"[red]Empresa '{company}' nao encontrada.[/red]")
            if _found:
                console.print(f"[dim]Empresas disponiveis: {', '.join(_found)}[/dim]")
            else:
                console.print("[dim]Nenhuma empresa criada. Use: bauer company create[/dim]")
            raise typer.Exit(code=1)

        # Lê o nome da empresa — suporta formato padrão e formato personalizado
        _yaml_path = _company_dir / "company.yaml"
        try:
            _raw = _yaml.safe_load(_yaml_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(_raw, dict):
                if "company" in _raw and isinstance(_raw["company"], dict):
                    # Formato personalizado: company: { name: "..." }
                    company_name = _raw["company"].get("name", company)
                else:
                    # Formato padrão: name: "..."
                    company_name = _raw.get("name", company)
            else:
                company_name = company
        except Exception:
            company_name = company

        # Workspace da empresa — usa smart selection (mesma lógica do agent_run)
        _company_ws = _company_dir / "workspace"
        _company_ws.mkdir(parents=True, exist_ok=True)

        # Garante que TASKS.md existe (auto-scaffold)
        _tasks_file = _company_ws / "TASKS.md"
        if not _tasks_file.exists():
            from .workspace_manager import WorkspaceManager as _WM
            _WM(_company_ws).init_project(company_name or company)

        # Empresa ativa → sempre usa workspace isolado da empresa
        workspace = _company_ws
        console.print(f"[dim]Empresa: [cyan]{company_name}[/cyan] ({company})[/dim]")

    # Garante que TASKS.md existe no workspace selecionado
    _ws_tasks = workspace / "TASKS.md"
    if not _ws_tasks.exists():
        from .workspace_manager import WorkspaceManager as _WM2
        _WM2(workspace).init_project("Projeto")

    url = f"http://{host}:{port}"
    console.print(f"\n[bold]Bauer Kanban[/bold]{f' — {company_name}' if company_name else ''}")
    console.print(f"  URL:       [cyan]{url}[/cyan]")
    console.print(f"  Workspace: {workspace}")
    console.print(f"  Refresh:   3s")

    if background:
        import subprocess, sys

        args = [
            sys.executable, "-m", "bauer.cli", "kanban",
            "--no-browser",
            "--port", str(port),
            "--host", host,
            "--workspace", str(workspace),
        ]
        if company:
            args += ["--company", company]

        kwargs: dict = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            kwargs["start_new_session"] = True

        subprocess.Popen(args, **kwargs)
        console.print(f"\n[green]Kanban rodando em background.[/green] "
                      f"Acesse [cyan]{url}[/cyan]")
        console.print("[dim]Para encerrar: feche o processo ou reinicie o terminal.[/dim]")
        return

    console.print(f"\n[dim]Pressione Ctrl+C para encerrar.[/dim]\n")

    try:
        run_kanban_server(
            workspace=workspace,
            host=host,
            port=port,
            open_browser=not no_browser,
            company_name=company_name,
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Kanban encerrado.[/dim]")


# --- task -------------------------------------------------------------------


@task_app.command("add")
def task_add(
    title: str = typer.Argument("", help="Titulo da tarefa — omita para modo entrevista"),
    desc: str = typer.Option("", "--desc", help="Descricao opcional"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Adiciona uma nova tarefa. Sem argumentos: modo entrevista interativo."""
    from .agent_wizard import wizard_create_task

    spec_id = ""
    if not title:
        result = wizard_create_task()
        if result is None:
            raise typer.Exit(code=0)
        title = result["title"]
        desc = result.get("description", "")
        spec_id = result.get("spec_id", "")
        # Prioridade e agent como prefixo na descrição se definidos
        extras: list[str] = []
        if result.get("priority") and result["priority"] != "media":
            extras.append(f"[{result['priority']}]")
        if result.get("assigned_agent"):
            extras.append(f"@{result['assigned_agent']}")
        if extras:
            desc = " ".join(extras) + (f" {desc}" if desc else "")

    wm = WorkspaceManager(workspace)
    task = wm.add_task(title, desc, spec_id=spec_id)
    spec_tag = f" [dim](spec: {spec_id})[/dim]" if spec_id else ""
    console.print(f"[green]✓ Tarefa {task.id} criada:[/green] {task.title}{spec_tag}")


@task_app.command("list")
def task_list(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    status: str = typer.Option("", "--status", help="Filtrar por status (TODO, DONE, ...)"),
):
    """Lista todas as tarefas com status."""
    wm = WorkspaceManager(workspace)
    tasks = wm.list_tasks()

    if not tasks:
        console.print("[dim]Nenhuma tarefa. Adicione com: bauer task add 'titulo'[/dim]")
        return

    if status:
        tasks = [t for t in tasks if t.status == status.upper()]

    table = Table(title=f"Tarefas — {workspace}/TASKS.md")
    table.add_column("id", style="dim", width=5)
    table.add_column("status", width=12)
    table.add_column("titulo")

    _STATUS_COLOR = {
        "TODO": "white",
        "READY": "cyan",
        "IN_PROGRESS": "yellow",
        "DONE": "green",
        "BLOCKED": "red",
        "FAILED": "magenta",
    }
    for t in tasks:
        color = _STATUS_COLOR.get(t.status, "white")
        table.add_row(t.id, f"[{color}]{t.status}[/{color}]", t.title)

    console.print(table)


def _task_update(workspace: Path, task_id: str, new_status: str) -> None:
    wm = WorkspaceManager(workspace)
    try:
        task = wm.update_task_status(task_id, new_status)
        console.print(f"[green]{task.id}[/green] → [{new_status}] {task.title}")
    except WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)


@task_app.command("start")
def task_start(
    task_id: str = typer.Argument(..., help="ID da tarefa (ex: 001)"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Marca tarefa como IN_PROGRESS."""
    _task_update(workspace, task_id, "IN_PROGRESS")


@task_app.command("done")
def task_done(
    task_id: str = typer.Argument(..., help="ID da tarefa (ex: 001)"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Marca tarefa como DONE."""
    _task_update(workspace, task_id, "DONE")


@task_app.command("block")
def task_block(
    task_id: str = typer.Argument(..., help="ID da tarefa (ex: 001)"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Marca tarefa como BLOCKED."""
    _task_update(workspace, task_id, "BLOCKED")


@task_app.command("ready")
def task_ready(
    task_id: str = typer.Argument(..., help="ID da tarefa (ex: 001)"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    assignee: str = typer.Option("", "--assignee", "-a", help="Responsavel/agente opcional"),
    max_retries: int = typer.Option(2, "--max-retries", help="Tentativas antes de FAILED"),
    max_runtime_seconds: int = typer.Option(0, "--max-runtime-seconds", help="Timeout do worker (0 = sem limite)"),
):
    """Marca tarefa como READY e opt-in para o dispatcher hibrido."""
    from .task_dispatcher import TaskDispatcher

    dispatcher = TaskDispatcher(workspace, max_retries=max_retries)
    task = dispatcher.mark_ready(
        task_id,
        assignee=assignee,
        max_retries=max_retries,
        max_runtime_seconds=max_runtime_seconds or None,
    )
    console.print(f"[green]{task.id}[/green] -> [READY] {task.title}")


@task_app.command("fail")
def task_fail(
    task_id: str = typer.Argument(..., help="ID da tarefa (ex: 001)"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Marca tarefa como FAILED."""
    _task_update(workspace, task_id, "FAILED")


@task_app.command("board")
def task_board(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    compact: bool = typer.Option(False, "--compact", "-c", help="Mostra apenas ID e titulo (sem descricao)"),
):
    """Exibe o Kanban board no terminal com todos os status de TASKS.md."""
    import sys as _sys
    from rich.columns import Columns
    from rich.markup import escape as _esc
    from rich.panel import Panel
    from rich.text import Text

    # Detecta se o terminal suporta UTF-8 (Linux/Mac sim, Windows legacy nao)
    _utf8 = _sys.platform != "win32" or (
        hasattr(_sys.stdout, "encoding") and
        (_sys.stdout.encoding or "").lower().replace("-", "") == "utf8"
    )

    # Icones: versao rica (UTF-8) ou ASCII puro (Windows legacy)
    if _utf8:
        _ICONS = {
            "TODO":        "📋",
            "READY":       "▶",
            "IN_PROGRESS": "🔄",
            "DONE":        "✅",
            "BLOCKED":     "🚫",
            "FAILED":      "✖",
        }
        _BAR_FULL  = "█"
        _BAR_EMPTY = "░"
        _ELLIPSIS  = "…"
    else:
        _ICONS = {
            "TODO":        "[ ]",
            "READY":       "[>]",
            "IN_PROGRESS": "[~]",
            "DONE":        "[x]",
            "BLOCKED":     "[!]",
            "FAILED":      "[x!]",
        }
        _BAR_FULL  = "#"
        _BAR_EMPTY = "."
        _ELLIPSIS  = "..."

    wm = WorkspaceManager(workspace)
    tasks = wm.list_tasks()

    if not tasks:
        console.print("[dim]Nenhuma tarefa. Adicione com: bauer task add 'titulo'[/dim]")
        return

    # Configuracao de cada coluna: (status, label, cor)
    COLUMNS = [
        ("TODO",        "TODO",        "bright_white"),
        ("READY",       "READY",       "cyan"),
        ("IN_PROGRESS", "IN PROGRESS", "yellow"),
        ("BLOCKED",     "BLOCKED",     "red"),
        ("FAILED",      "FAILED",      "magenta"),
        ("DONE",        "DONE",        "green"),
    ]

    _CARD_COLOR = {
        "TODO":        "white",
        "READY":       "cyan",
        "IN_PROGRESS": "yellow",
        "DONE":        "green",
        "BLOCKED":     "red",
        "FAILED":      "magenta",
    }

    # Agrupa tarefas por status
    by_status: dict[str, list] = {s: [] for s, *_ in COLUMNS}
    for t in tasks:
        bucket = by_status.get(t.status)
        if bucket is not None:
            bucket.append(t)

    panels = []
    for status, label, border_color in COLUMNS:
        col_tasks = by_status[status]
        icon = _ICONS.get(status, status)

        # Monta o conteudo do painel
        lines = Text()
        if not col_tasks:
            lines.append("  (vazio)\n", style="dim")
        else:
            for t in col_tasks:
                card_color = _CARD_COLOR.get(status, "white")
                lines.append(f" [{t.id}] ", style="dim")
                lines.append(t.title, style=card_color)
                lines.append("\n")
                if not compact and t.description:
                    desc = t.description[:40] + (_ELLIPSIS if len(t.description) > 40 else "")
                    lines.append(f"       {desc}\n", style="dim")

        title = f"{_esc(icon)} {label} ({len(col_tasks)})"
        panels.append(
            Panel(
                lines,
                title=f"[bold {border_color}]{title}[/bold {border_color}]",
                border_style=border_color,
                expand=True,
                padding=(0, 1),
            )
        )

    # Barra de progresso
    total = len(tasks)
    done_count = len(by_status["DONE"])
    pct = int(done_count / total * 100) if total else 0
    bar = _BAR_FULL * (pct // 5) + _BAR_EMPTY * (20 - pct // 5)

    console.print()
    console.print(Columns(panels, equal=True, expand=True))
    console.print(
        f"[dim]  Progresso: {bar} {pct}%  "
        f"({done_count}/{total} concluidas)[/dim]\n"
    )


# --- ops --------------------------------------------------------------------


@ops_app.command("status")
def ops_status_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    limit: int = typer.Option(10, "--limit", help="Numero de runs/eventos recentes"),
    as_json: bool = typer.Option(False, "--json", help="Imprime JSON bruto para automacao"),
):
    """Mostra saude operacional: filas, lanes, claims ativos, runs e eventos."""
    import json as _json

    from .ops_status import build_ops_status

    status = build_ops_status(workspace, limit=limit)
    if as_json:
        console.print(_json.dumps(status, ensure_ascii=False, indent=2), soft_wrap=True)
        return

    counts = status["status_counts"]
    summary = Table(title=f"Ops status - {workspace}", show_lines=False)
    summary.add_column("Status", style="cyan")
    summary.add_column("Qtd", justify="right")
    for name in ("READY", "IN_PROGRESS", "FAILED", "BLOCKED", "TODO", "DONE"):
        summary.add_row(name, str(counts.get(name, 0)))
    console.print(summary)

    lanes = status.get("lanes", [])
    if lanes:
        lane_table = Table(title="Lanes", show_lines=False)
        lane_table.add_column("Lane", style="cyan")
        lane_table.add_column("Agent")
        lane_table.add_column("Capacidade", justify="right")
        lane_table.add_column("Ready", justify="right")
        lane_table.add_column("Running", justify="right")
        lane_table.add_column("Failed", justify="right")
        lane_table.add_column("Blocked", justify="right")
        for lane in lanes:
            lane_table.add_row(
                str(lane.get("lane", "")),
                str(lane.get("agent", "")),
                str(lane.get("max_concurrent", "")),
                str(lane.get("ready", 0)),
                str(lane.get("running", 0)),
                str(lane.get("failed", 0)),
                str(lane.get("blocked", 0)),
            )
        console.print(lane_table)

    claims = status.get("active_claims", [])
    if claims:
        claim_table = Table(title="Claims ativos", show_lines=False)
        claim_table.add_column("Task", style="cyan")
        claim_table.add_column("Lane")
        claim_table.add_column("Run", style="dim")
        claim_table.add_column("PID", justify="right")
        claim_table.add_column("Alive")
        claim_table.add_column("Lease", justify="right")
        for claim in claims:
            lease = claim.get("claim_seconds_left")
            claim_table.add_row(
                str(claim.get("public_id", "")),
                str(claim.get("lane", "")),
                str(claim.get("run_id", "")),
                str(claim.get("worker_pid") or ""),
                str(claim.get("worker_alive")),
                "" if lease is None else f"{lease}s",
            )
        console.print(claim_table)
    else:
        console.print("[dim]Nenhum claim ativo.[/dim]")

    runs = status.get("recent_runs", [])
    if runs:
        run_table = Table(title="Runs recentes", show_lines=False)
        run_table.add_column("Run", style="dim")
        run_table.add_column("Task")
        run_table.add_column("Status")
        run_table.add_column("Lane")
        run_table.add_column("Heartbeat", style="dim")
        for run in runs:
            metadata = run.get("metadata", {}) or {}
            run_table.add_row(
                str(run.get("run_id", "")),
                str(run.get("task_id", "")),
                str(run.get("status", "")),
                str(metadata.get("lane", "")),
                str(run.get("heartbeat_at", "")),
            )
        console.print(run_table)

    orchestrations = status.get("recent_orchestrations", [])
    if orchestrations:
        orch_table = Table(title="Orquestracoes duraveis", show_lines=False)
        orch_table.add_column("Run", style="cyan")
        orch_table.add_column("Status")
        orch_table.add_column("Mode")
        orch_table.add_column("Steps", justify="right")
        orch_table.add_column("Objetivo")
        for run in orchestrations:
            orch_table.add_row(
                str(run.get("run_id", "")),
                str(run.get("status", "")),
                str(run.get("mode", "")),
                str(len(run.get("plan", []) or [])),
                str(run.get("objective", ""))[:70],
            )
        console.print(orch_table)

    automation_jobs = status.get("automation_jobs", [])
    if automation_jobs:
        cron_table = Table(title="Automacoes cron", show_lines=False)
        cron_table.add_column("Nome", style="cyan")
        cron_table.add_column("Status")
        cron_table.add_column("Schedule")
        cron_table.add_column("Next", style="dim")
        cron_table.add_column("Runs", justify="right")
        for job in automation_jobs:
            cron_table.add_row(
                str(job.get("name", "")),
                str(job.get("status", "")),
                str(job.get("schedule_str", "")),
                str(job.get("next_run_at", "")),
                str(job.get("run_count", 0)),
            )
        console.print(cron_table)

    events = status.get("recent_events", [])
    if events:
        event_table = Table(title="Eventos recentes", show_lines=False)
        event_table.add_column("ID", justify="right", style="dim")
        event_table.add_column("Task")
        event_table.add_column("Evento")
        event_table.add_column("Mensagem")
        for event in events:
            event_table.add_row(
                str(event.get("id", "")),
                str(event.get("task_id", "")),
                str(event.get("event_type", "")),
                str(event.get("message", ""))[:80],
            )
        console.print(event_table)


@ops_app.command("migrations")
def ops_migrations_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    apply: bool = typer.Option(True, "--apply/--list-only", help="Registra baseline se ainda nao existir"),
):
    """Mostra/aplica ledger de schema migrations dos sidecars Bauer."""
    from .schema_migrations import MigrationLedger, ensure_level8_migrations

    records = ensure_level8_migrations(workspace) if apply else MigrationLedger(workspace).list_records()
    if not records:
        console.print("[dim]Nenhuma migration registrada.[/dim]")
        return
    table = Table(title=f"Schema migrations - {workspace}", show_lines=False)
    table.add_column("Store", style="cyan")
    table.add_column("Version", justify="right")
    table.add_column("Name")
    table.add_column("Applied", style="dim")
    for record in records:
        table.add_row(record.store, str(record.version), record.name, record.applied_at)
    console.print(table)


@ops_app.command("watch")
def ops_watch_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    interval: float = typer.Option(2.0, "--interval"),
    iterations: int = typer.Option(0, "--iterations", help="0 = infinito"),
):
    """TUI simples de observabilidade operacional com auto-refresh."""
    import time as _time

    from rich.live import Live
    from rich.panel import Panel as _Panel

    from .ops_status import build_ops_status

    def _render():
        status = build_ops_status(workspace, limit=5)
        counts = status["status_counts"]
        lines = [
            f"Workspace: {status['workspace']}",
            f"READY={counts.get('READY', 0)} IN_PROGRESS={counts.get('IN_PROGRESS', 0)} "
            f"FAILED={counts.get('FAILED', 0)} BLOCKED={counts.get('BLOCKED', 0)}",
            f"Claims ativos: {len(status.get('active_claims', []))}",
            f"Automacoes: {len(status.get('automation_jobs', []))} | "
            f"Outbox: {len(status.get('gateway_outbox', []))} | "
            f"Orquestracoes: {len(status.get('recent_orchestrations', []))}",
            f"Atualizado: {status['generated_at']}",
        ]
        return _Panel("\n".join(lines), title="Bauer Ops Watch", border_style="cyan")

    count = 0
    with Live(_render(), refresh_per_second=1, console=console) as live:
        while True:
            _time.sleep(max(0.5, float(interval)))
            live.update(_render())
            count += 1
            if iterations and count >= iterations:
                break


# --- runtime supervisor -------------------------------------------------------


def _runtime_supervise_args(
    *,
    workspace: Path,
    config: Path,
    models: Path,
    dispatcher: bool,
    cron: bool,
    outbox: bool,
    kanban: bool,
    dispatch_interval: int,
    cron_interval: int,
    outbox_interval: int,
    supervisor_interval: int,
    kanban_host: str,
    kanban_port: int,
    max_spawn: int,
    max_in_progress: int,
    max_jobs: int,
    delivery_limit: int,
) -> list[str]:
    args = [
        "--workspace", str(workspace),
        "--config", str(config),
        "--models", str(models),
        "--dispatch-interval", str(dispatch_interval),
        "--cron-interval", str(cron_interval),
        "--outbox-interval", str(outbox_interval),
        "--supervisor-interval", str(supervisor_interval),
        "--kanban-host", kanban_host,
        "--kanban-port", str(kanban_port),
        "--max-spawn", str(max_spawn),
        "--max-in-progress", str(max_in_progress),
        "--max-jobs", str(max_jobs),
        "--delivery-limit", str(delivery_limit),
    ]
    args.append("--dispatcher" if dispatcher else "--no-dispatcher")
    args.append("--cron" if cron else "--no-cron")
    args.append("--outbox" if outbox else "--no-outbox")
    args.append("--kanban" if kanban else "--no-kanban")
    return args


def _runtime_specs_table(specs) -> Table:
    table = Table(title="Bauer Runtime Services", show_lines=False)
    table.add_column("Service", style="cyan")
    table.add_column("Enabled")
    table.add_column("Restart")
    table.add_column("Command")
    for spec in specs:
        table.add_row(
            spec.name,
            "yes" if spec.enabled else "no",
            "yes" if spec.restart else "no",
            " ".join(spec.command),
        )
    return table


@runtime_app.command("start")
def runtime_start_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    background: bool = typer.Option(True, "--background/--foreground", help="Roda supervisor em background ou prende este terminal"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra o que seria iniciado sem subir processos"),
    dispatcher: bool = typer.Option(True, "--dispatcher/--no-dispatcher"),
    cron: bool = typer.Option(True, "--cron/--no-cron"),
    outbox: bool = typer.Option(True, "--outbox/--no-outbox"),
    kanban: bool = typer.Option(True, "--kanban/--no-kanban"),
    dispatch_interval: int = typer.Option(30, "--dispatch-interval"),
    cron_interval: int = typer.Option(60, "--cron-interval"),
    outbox_interval: int = typer.Option(30, "--outbox-interval"),
    supervisor_interval: int = typer.Option(5, "--supervisor-interval"),
    kanban_host: str = typer.Option("127.0.0.1", "--kanban-host"),
    kanban_port: int = typer.Option(8765, "--kanban-port"),
    max_spawn: int = typer.Option(1, "--max-spawn"),
    max_in_progress: int = typer.Option(1, "--max-in-progress"),
    max_jobs: int = typer.Option(10, "--max-jobs"),
    delivery_limit: int = typer.Option(20, "--delivery-limit"),
):
    """Sobe o runtime always-on: dispatcher, cron, outbox e kanban."""
    import json as _json

    from .supervisor import RuntimeSupervisor

    supervisor = RuntimeSupervisor(workspace, config=config, models=models)
    specs = supervisor.build_service_specs(
        dispatcher=dispatcher,
        cron=cron,
        outbox=outbox,
        kanban=kanban,
        dispatch_interval=dispatch_interval,
        cron_interval=cron_interval,
        outbox_interval=outbox_interval,
        kanban_host=kanban_host,
        kanban_port=kanban_port,
        max_spawn=max_spawn,
        max_in_progress=max_in_progress,
        max_jobs=max_jobs,
        delivery_limit=delivery_limit,
    )
    if dry_run and not background:
        console.print(_runtime_specs_table(specs))
        return
    args = _runtime_supervise_args(
        workspace=workspace,
        config=config,
        models=models,
        dispatcher=dispatcher,
        cron=cron,
        outbox=outbox,
        kanban=kanban,
        dispatch_interval=dispatch_interval,
        cron_interval=cron_interval,
        outbox_interval=outbox_interval,
        supervisor_interval=supervisor_interval,
        kanban_host=kanban_host,
        kanban_port=kanban_port,
        max_spawn=max_spawn,
        max_in_progress=max_in_progress,
        max_jobs=max_jobs,
        delivery_limit=delivery_limit,
    )
    if background:
        result = supervisor.start_background(args, dry_run=dry_run)
        if dry_run:
            console.print(_runtime_specs_table(specs))
            console.print(_json.dumps(result, ensure_ascii=False, indent=2), soft_wrap=True)
            return
        console.print(f"[green]Runtime supervisor iniciado[/green] pid={result['pid']}")
        console.print(f"[dim]Logs: {result['log_path']}[/dim]")
        return

    console.print("[green]Runtime supervisor iniciado em foreground[/green] Ctrl+C para parar.")
    supervisor.run_forever(specs, supervisor_interval=supervisor_interval)


@runtime_app.command("supervise", hidden=True)
def runtime_supervise_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    dispatcher: bool = typer.Option(True, "--dispatcher/--no-dispatcher"),
    cron: bool = typer.Option(True, "--cron/--no-cron"),
    outbox: bool = typer.Option(True, "--outbox/--no-outbox"),
    kanban: bool = typer.Option(True, "--kanban/--no-kanban"),
    dispatch_interval: int = typer.Option(30, "--dispatch-interval"),
    cron_interval: int = typer.Option(60, "--cron-interval"),
    outbox_interval: int = typer.Option(30, "--outbox-interval"),
    supervisor_interval: int = typer.Option(5, "--supervisor-interval"),
    kanban_host: str = typer.Option("127.0.0.1", "--kanban-host"),
    kanban_port: int = typer.Option(8765, "--kanban-port"),
    max_spawn: int = typer.Option(1, "--max-spawn"),
    max_in_progress: int = typer.Option(1, "--max-in-progress"),
    max_jobs: int = typer.Option(10, "--max-jobs"),
    delivery_limit: int = typer.Option(20, "--delivery-limit"),
):
    """Processo interno que supervisiona os servicos do runtime."""
    from .supervisor import RuntimeSupervisor

    supervisor = RuntimeSupervisor(workspace, config=config, models=models)
    specs = supervisor.build_service_specs(
        dispatcher=dispatcher,
        cron=cron,
        outbox=outbox,
        kanban=kanban,
        dispatch_interval=dispatch_interval,
        cron_interval=cron_interval,
        outbox_interval=outbox_interval,
        kanban_host=kanban_host,
        kanban_port=kanban_port,
        max_spawn=max_spawn,
        max_in_progress=max_in_progress,
        max_jobs=max_jobs,
        delivery_limit=delivery_limit,
    )
    supervisor.run_forever(specs, supervisor_interval=supervisor_interval)


@runtime_app.command("status")
def runtime_status_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Mostra estado do supervisor e dos servicos supervisionados."""
    import json as _json

    from .supervisor import RuntimeSupervisor

    status = RuntimeSupervisor(workspace).status().to_public_dict()
    if as_json:
        console.print(_json.dumps(status, ensure_ascii=False, indent=2), soft_wrap=True)
        return
    table = Table(title=f"Bauer Runtime - {workspace}", show_lines=False)
    table.add_column("Service", style="cyan")
    table.add_column("State")
    table.add_column("PID", justify="right")
    table.add_column("Alive")
    table.add_column("Restarts", justify="right")
    table.add_column("Log")
    console.print(
        f"[bold]Supervisor:[/bold] state={status['state']} "
        f"pid={status.get('supervisor_pid') or '-'} alive={status.get('supervisor_alive')}"
    )
    for service in status.get("services", []):
        table.add_row(
            str(service.get("name", "")),
            str(service.get("state", "")),
            str(service.get("pid") or "-"),
            "yes" if service.get("alive") else "no",
            str(service.get("restarts", 0)),
            str(service.get("log_path", "")),
        )
    console.print(table)


@runtime_app.command("stop")
def runtime_stop_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    no_terminate: bool = typer.Option(False, "--no-terminate", help="Apenas escreve STOP; nao envia SIGTERM"),
):
    """Solicita parada do supervisor e encerra servicos supervisionados."""
    from .supervisor import RuntimeSupervisor

    status = RuntimeSupervisor(workspace).request_stop(terminate=not no_terminate)
    console.print(f"[green]Stop solicitado[/green] state={status.get('state')} workspace={workspace}")


@runtime_app.command("restart")
def runtime_restart_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    dispatcher: bool = typer.Option(True, "--dispatcher/--no-dispatcher"),
    cron: bool = typer.Option(True, "--cron/--no-cron"),
    outbox: bool = typer.Option(True, "--outbox/--no-outbox"),
    kanban: bool = typer.Option(True, "--kanban/--no-kanban"),
):
    """Para o runtime atual e sobe um novo supervisor em background."""
    from .supervisor import RuntimeSupervisor

    supervisor = RuntimeSupervisor(workspace, config=config, models=models)
    if not dry_run:
        supervisor.request_stop(terminate=True)
    args = _runtime_supervise_args(
        workspace=workspace,
        config=config,
        models=models,
        dispatcher=dispatcher,
        cron=cron,
        outbox=outbox,
        kanban=kanban,
        dispatch_interval=30,
        cron_interval=60,
        outbox_interval=30,
        supervisor_interval=5,
        kanban_host="127.0.0.1",
        kanban_port=8765,
        max_spawn=1,
        max_in_progress=1,
        max_jobs=10,
        delivery_limit=20,
    )
    result = supervisor.start_background(args, dry_run=dry_run)
    console.print(f"[green]Runtime restart solicitado[/green] pid={result.get('pid') or '-'}")


@runtime_app.command("logs")
def runtime_logs_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    service: str = typer.Option("supervisor", "--service", "-s"),
    lines: int = typer.Option(80, "--lines", "-n"),
):
    """Mostra as ultimas linhas de log do supervisor ou de um servico."""
    from .supervisor import RuntimeSupervisor, tail_log

    supervisor = RuntimeSupervisor(workspace)
    status = supervisor.status().to_public_dict()
    if service == "supervisor":
        log_path = Path(status["runtime_dir"]) / "logs" / "supervisor.log"
    else:
        matches = [svc for svc in status.get("services", []) if svc.get("name") == service]
        if not matches:
            console.print(f"[red]Servico nao encontrado:[/red] {service}")
            raise typer.Exit(code=1)
        log_path = Path(str(matches[0].get("log_path", "")))
    for line in tail_log(log_path, lines=lines):
        console.print(line)


# --- daemon -----------------------------------------------------------------


def _daemon_state_dir() -> "Path":
    """Return the default daemon state directory (~/.bauer/daemon)."""
    import os as _os
    home = _os.environ.get("BAUER_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".bauer"
    return base / "daemon"


def _daemon_log_path() -> "Path":
    return _daemon_state_dir() / "daemon.log"


def _daemon_pid_path() -> "Path":
    return _daemon_state_dir() / "daemon.pid"


def _read_daemon_pid() -> "int | None":
    pid_path = _daemon_pid_path()
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except Exception:
        return None


def _daemon_pid_alive(pid: int) -> bool:
    """Return True if a process with *pid* is currently running."""
    import psutil
    try:
        return psutil.pid_exists(pid)
    except Exception:
        pass
    # Fallback: os.kill(pid, 0) — works on Unix; skip on Windows
    import os as _os
    try:
        _os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False


@daemon_app.command("start")
def daemon_start_cmd(
    board: str = typer.Option("default", "--board", "-b", help="Kanban board slug"),
    workers: int = typer.Option(2, "--workers", "-w", help="Numero de workers paralelos"),
    budget_usd: float = typer.Option(5.0, "--budget-usd", help="Limite de custo USD por sessao"),
    budget_hours: float = typer.Option(1.0, "--budget-hours", help="Limite de tempo em horas"),
    max_llm_calls: int = typer.Option(200, "--max-llm-calls"),
    max_tool_calls: int = typer.Option(500, "--max-tool-calls"),
    poll_interval: float = typer.Option(5.0, "--poll-interval", help="Segundos de sleep quando sem task"),
    profile: str = typer.Option("low", "--profile", help="Perfil do modelo: low/medium/high"),
    headless_mode: str = typer.Option(
        "threshold", "--headless-mode",
        help="Modo de aprovacao: threshold | yolo | deny_all",
    ),
    detach: bool = typer.Option(True, "--detach/--foreground", help="Roda em background (detach) ou prende o terminal"),
    log_level: str = typer.Option("INFO", "--log-level", help="DEBUG | INFO | WARNING | ERROR"),
):
    """Inicia o BauerDaemon — pool de workers que executam tasks do kanban autonomamente."""
    import subprocess as _sp
    import sys as _sys
    import logging as _logging

    state_dir = _daemon_state_dir()

    if detach:
        # Check for existing alive daemon
        existing_pid = _read_daemon_pid()
        if existing_pid and _daemon_pid_alive(existing_pid):
            console.print(
                f"[yellow]Daemon ja rodando[/yellow] pid={existing_pid} "
                f"board={board}. Use [bold]bauer daemon status[/bold] para ver detalhes."
            )
            raise typer.Exit(code=0)

        state_dir.mkdir(parents=True, exist_ok=True)
        log_path = _daemon_log_path()
        log_handle = log_path.open("ab")

        cmd = [
            _sys.executable, "-m", "bauer.cli", "daemon", "_run",
            "--board", board,
            "--workers", str(workers),
            "--budget-usd", str(budget_usd),
            "--budget-hours", str(budget_hours),
            "--max-llm-calls", str(max_llm_calls),
            "--max-tool-calls", str(max_tool_calls),
            "--poll-interval", str(poll_interval),
            "--profile", profile,
            "--headless-mode", headless_mode,
            "--log-level", log_level,
        ]

        import os as _os
        popen_kwargs: dict = {
            "stdout": log_handle,
            "stderr": _sp.STDOUT,
            "stdin": _sp.DEVNULL,
            "close_fds": True,
        }
        if _os.name == "nt":
            popen_kwargs["creationflags"] = (
                getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(_sp, "DETACHED_PROCESS", 0)
            )
        else:
            popen_kwargs["start_new_session"] = True

        try:
            proc = _sp.Popen(cmd, **popen_kwargs)
        finally:
            log_handle.close()

        console.print(
            f"[green]BauerDaemon iniciado em background[/green] pid={proc.pid} "
            f"board={board} workers={workers} budget=${budget_usd:.2f}"
        )
        console.print(f"[dim]Log: {log_path}[/dim]")
        console.print("[dim]Use [bold]bauer daemon status[/bold] e [bold]bauer daemon logs[/bold] para monitorar.[/dim]")
        return

    # ── foreground mode ──────────────────────────────────────────────────────
    import asyncio as _asyncio
    _logging.basicConfig(
        level=getattr(_logging, log_level.upper(), _logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    )

    from .daemon import BauerDaemon, DaemonConfig

    cfg = DaemonConfig(
        board=board,
        workers=workers,
        max_cost_usd=budget_usd,
        max_wall_seconds=int(budget_hours * 3600),
        max_llm_calls=max_llm_calls,
        max_tool_calls=max_tool_calls,
        poll_interval_seconds=poll_interval,
        profile=profile,
        headless_mode=headless_mode,
        state_dir=state_dir,
    )
    console.print(
        f"[green]BauerDaemon iniciando em foreground[/green] "
        f"board={board} workers={workers} budget=${budget_usd:.2f} Ctrl+C para parar."
    )
    exit_code = _asyncio.run(BauerDaemon(cfg).start())
    raise typer.Exit(code=exit_code)


@daemon_app.command("_run", hidden=True)
def daemon_run_internal_cmd(
    board: str = typer.Option("default", "--board"),
    workers: int = typer.Option(2, "--workers"),
    budget_usd: float = typer.Option(5.0, "--budget-usd"),
    budget_hours: float = typer.Option(1.0, "--budget-hours"),
    max_llm_calls: int = typer.Option(200, "--max-llm-calls"),
    max_tool_calls: int = typer.Option(500, "--max-tool-calls"),
    poll_interval: float = typer.Option(5.0, "--poll-interval"),
    profile: str = typer.Option("low", "--profile"),
    headless_mode: str = typer.Option("threshold", "--headless-mode"),
    log_level: str = typer.Option("INFO", "--log-level"),
):
    """Processo interno chamado por 'daemon start --detach'. Nao chamar diretamente."""
    import asyncio as _asyncio
    import logging as _logging

    _logging.basicConfig(
        level=getattr(_logging, log_level.upper(), _logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
    )

    from .daemon import BauerDaemon, DaemonConfig

    cfg = DaemonConfig(
        board=board,
        workers=workers,
        max_cost_usd=budget_usd,
        max_wall_seconds=int(budget_hours * 3600),
        max_llm_calls=max_llm_calls,
        max_tool_calls=max_tool_calls,
        poll_interval_seconds=poll_interval,
        profile=profile,
        headless_mode=headless_mode,
        state_dir=_daemon_state_dir(),
    )
    exit_code = _asyncio.run(BauerDaemon(cfg).start())
    raise typer.Exit(code=exit_code)


@daemon_app.command("stop")
def daemon_stop_cmd(
    force: bool = typer.Option(False, "--force", "-f", help="Envia SIGKILL apos timeout"),
    timeout: int = typer.Option(10, "--timeout", help="Segundos para aguardar parada graceful"),
):
    """Para o daemon em execucao (envia SIGTERM; aguarda parada graceful)."""
    import os as _os
    import signal as _signal
    import time as _time
    import subprocess as _sp

    def _kill_pid(pid: int, hard: bool = False) -> bool:
        """Kill a PID. Returns True if the signal was sent."""
        try:
            if _os.name == "nt":
                flags = ["/F"] if hard else []
                _sp.call(["taskkill", *flags, "/PID", str(pid)], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            else:
                sig = _signal.SIGKILL if hard else _signal.SIGTERM
                _os.kill(pid, sig)
            return True
        except (ProcessLookupError, OSError):
            return False

    def _cleanup_stale_sessions(reason: str = "daemon_stopped") -> int:
        """Mark running DB sessions as stopped; returns count of sessions cleaned."""
        state_db_path = _daemon_state_dir() / "daemon_state.db"
        if not state_db_path.exists():
            return 0
        from .daemon import DaemonStateDB
        db = DaemonStateDB(state_db_path)
        sessions = db.get_running()
        count = 0
        for s in sessions:
            db.mark_stopped(s["id"], reason=reason)
            count += 1
        return count

    def _kill_db_session_pids(hard: bool = False) -> list[int]:
        """Kill PIDs of all running DB sessions. Returns list of killed PIDs."""
        state_db_path = _daemon_state_dir() / "daemon_state.db"
        if not state_db_path.exists():
            return []
        from .daemon import DaemonStateDB
        db = DaemonStateDB(state_db_path)
        killed = []
        for s in db.get_running():
            pid = s.get("pid")
            if pid and _daemon_pid_alive(pid):
                _kill_pid(pid, hard=hard)
                killed.append(pid)
        return killed

    pid = _read_daemon_pid()

    # ── No daemon.pid: check DB for orphaned running sessions ─────────────────
    if pid is None:
        orphan_pids = _kill_db_session_pids(hard=force)
        cleaned = _cleanup_stale_sessions(reason="force_stop_orphan")
        if cleaned:
            console.print(
                f"[yellow]daemon.pid nao encontrado, mas {cleaned} sessao(oes) ativa(s) no DB.[/yellow]"
            )
            if orphan_pids:
                console.print(f"[green]PIDs encerrados: {orphan_pids}[/green]")
            else:
                console.print("[dim]PIDs ja estavam mortos — estado do DB corrigido.[/dim]")
            _daemon_pid_path().unlink(missing_ok=True)
        else:
            console.print("[yellow]Nenhum daemon.pid encontrado. Daemon nao esta rodando.[/yellow]")
        raise typer.Exit(code=0)

    # ── daemon.pid exists but process is already dead ─────────────────────────
    if not _daemon_pid_alive(pid):
        console.print(f"[yellow]PID {pid} nao esta mais ativo. Limpando estado.[/yellow]")
        _daemon_pid_path().unlink(missing_ok=True)
        _cleanup_stale_sessions(reason="stale_pid_cleanup")
        raise typer.Exit(code=0)

    # ── Send SIGTERM and wait ─────────────────────────────────────────────────
    console.print(f"[cyan]Enviando SIGTERM para pid={pid}...[/cyan]")
    _kill_pid(pid, hard=False)

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if not _daemon_pid_alive(pid):
            console.print(f"[green]Daemon encerrado graciosamente[/green] pid={pid}")
            _cleanup_stale_sessions(reason="graceful_stop")
            raise typer.Exit(code=0)
        _time.sleep(0.5)

    # ── Timeout: force kill or warn ───────────────────────────────────────────
    if force:
        console.print(f"[yellow]Timeout — enviando SIGKILL para pid={pid}[/yellow]")
        _kill_pid(pid, hard=True)
        _cleanup_stale_sessions(reason="force_kill")
        console.print("[green]Daemon encerrado forcosamente.[/green]")
    else:
        console.print(
            f"[yellow]Daemon ainda ativo apos {timeout}s. "
            f"Use --force para SIGKILL.[/yellow]"
        )
        raise typer.Exit(code=1)


@daemon_app.command("status")
def daemon_status_cmd(
    as_json: bool = typer.Option(False, "--json", help="Saida em JSON"),
    all_sessions: bool = typer.Option(False, "--all", "-a", help="Mostra todas as sessoes, nao so as ativas"),
):
    """Mostra estado atual do daemon (sessoes ativas, budget, workers)."""
    import json as _json
    import time as _time

    state_db_path = _daemon_state_dir() / "daemon_state.db"
    pid = _read_daemon_pid()
    alive = pid is not None and _daemon_pid_alive(pid)

    if not state_db_path.exists():
        if as_json:
            console.print(_json.dumps({"running": False, "pid": None, "sessions": []}, indent=2))
        else:
            console.print("[dim]Nenhuma sessao de daemon encontrada.[/dim]")
            console.print(f"[dim]Use [bold]bauer daemon start[/bold] para iniciar.[/dim]")
        raise typer.Exit(code=0)

    from .daemon import DaemonStateDB

    db = DaemonStateDB(state_db_path)
    if all_sessions:
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(str(state_db_path), timeout=5.0)
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM daemon_sessions ORDER BY started_at DESC LIMIT 20"
        ).fetchall()
        sessions = [dict(r) for r in rows]
        conn.close()
    else:
        sessions = db.get_running()
        # Also include the latest if nothing is running
        if not sessions:
            latest = db.get_latest()
            if latest:
                sessions = [latest]

    if as_json:
        console.print(_json.dumps(
            {"running": alive, "pid": pid, "sessions": sessions},
            indent=2, ensure_ascii=False,
        ))
        raise typer.Exit(code=0)

    # Pretty table
    console.print(
        f"\n[bold]BauerDaemon[/bold]  "
        + ("[green]RODANDO[/green]" if alive else "[red]PARADO[/red]")
        + (f"  pid={pid}" if pid else "")
    )
    if not sessions:
        console.print("[dim]Nenhuma sessao registrada.[/dim]")
        raise typer.Exit(code=0)

    table = Table(show_lines=False, box=None)
    table.add_column("Sessao", style="cyan", no_wrap=True)
    table.add_column("Board")
    table.add_column("Workers", justify="right")
    table.add_column("Status")
    table.add_column("Uptime")
    table.add_column("Budget")
    table.add_column("Shutdown")

    now = _time.time()
    for s in sessions:
        uptime_s = now - (s.get("started_at") or now)
        uptime_str = (
            f"{int(uptime_s // 3600)}h{int(uptime_s % 3600 // 60)}m"
            if uptime_s >= 60 else f"{int(uptime_s)}s"
        )
        budget_info = ""
        if s.get("budget_json"):
            try:
                b = _json.loads(s["budget_json"])
                cost = b.get("cost_usd", 0)
                pct = b.get("cost_pct", 0)
                budget_info = f"${cost:.3f} ({pct:.0f}%)"
            except Exception:
                pass
        status_color = "green" if s.get("status") == "running" else "dim"
        table.add_row(
            s.get("id", "")[:24],
            s.get("board", ""),
            str(s.get("workers", "")),
            f"[{status_color}]{s.get('status', '')}[/{status_color}]",
            uptime_str,
            budget_info or "-",
            s.get("shutdown_reason") or "-",
        )
    console.print(table)
    console.print()


@daemon_app.command("logs")
def daemon_logs_cmd(
    lines: int = typer.Option(50, "--lines", "-n", help="Numero de linhas iniciais"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Segue o arquivo de log (tail -f)"),
):
    """Exibe o log do daemon (tail). Use --follow para acompanhar em tempo real."""
    import time as _time

    log_path = _daemon_log_path()
    if not log_path.exists():
        console.print(f"[dim]Nenhum log encontrado em {log_path}[/dim]")
        console.print("[dim]Inicie o daemon com [bold]bauer daemon start[/bold] primeiro.[/dim]")
        raise typer.Exit(code=0)

    # Print last N lines
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        console.print(f"[red]Erro ao ler log:[/red] {exc}")
        raise typer.Exit(code=1)

    for line in content[-lines:]:
        console.print(line)

    if not follow:
        return

    # Follow mode — poll for new content
    console.print(f"\n[dim]--- seguindo {log_path} (Ctrl+C para parar) ---[/dim]")
    offset = log_path.stat().st_size
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            while True:
                line = fh.readline()
                if line:
                    console.print(line, end="")
                else:
                    _time.sleep(0.25)
    except KeyboardInterrupt:
        console.print("\n[dim]Log encerrado.[/dim]")


# --- cron -------------------------------------------------------------------


@cron_app.command("create")
def cron_create_cmd(
    name: str = typer.Argument(..., help="Nome unico do job"),
    prompt: str = typer.Argument(..., help="Prompt a executar quando o job vencer"),
    schedule: str = typer.Option(..., "--schedule", "-s", help="every 30m | every 2h | daily 09:00 | at ISO | cron: */15 * * * *"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    assignee: str = typer.Option("", "--assignee", help="Agent/lane preferido para a task"),
    priority: str = typer.Option("medium", "--priority"),
    max_retries: int = typer.Option(2, "--max-retries"),
    max_runtime_seconds: int = typer.Option(0, "--max-runtime-seconds"),
    deliver: str = typer.Option("", "--deliver", help="Entrega outbox: channel:<name> ou plataforma:<target>"),
):
    """Cria uma automacao duravel que enfileira tasks no dispatcher."""
    from .automation_store import AutomationStore

    metadata = {
        "assignee": assignee,
        "priority": priority,
        "max_retries": max(1, int(max_retries)),
    }
    if max_runtime_seconds > 0:
        metadata["max_runtime_seconds"] = int(max_runtime_seconds)
    if deliver:
        metadata["delivery"] = deliver
    try:
        job = AutomationStore(workspace).create_job(
            name=name,
            prompt=prompt,
            schedule=schedule,
            metadata=metadata,
        )
    except Exception as exc:
        console.print(f"[red]Erro criando cron job:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]cron criado[/green] [cyan]{job.name}[/cyan] "
        f"schedule={job.schedule_str} next={job.next_run_at}"
    )


@cron_app.command("list")
def cron_list_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    limit: int = typer.Option(50, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista automacoes configuradas."""
    import json as _json
    from dataclasses import asdict

    from .automation_store import AutomationStore

    jobs = AutomationStore(workspace).list_jobs(limit=limit)
    if as_json:
        console.print(_json.dumps([asdict(job) for job in jobs], ensure_ascii=False, indent=2))
        return
    if not jobs:
        console.print("[dim]Nenhuma automacao cron configurada.[/dim]")
        return
    table = Table(title=f"Cron jobs - {workspace}", show_lines=False)
    table.add_column("Nome", style="cyan")
    table.add_column("Status")
    table.add_column("Schedule")
    table.add_column("Next", style="dim")
    table.add_column("Runs", justify="right")
    for job in jobs:
        table.add_row(job.name, job.status, job.schedule_str, job.next_run_at, str(job.run_count))
    console.print(table)


@cron_app.command("tick")
def cron_tick_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    max_jobs: int = typer.Option(10, "--max-jobs"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Executa um tick do scheduler: jobs vencidos viram tasks READY."""
    from .automation_scheduler import AutomationScheduler

    result = AutomationScheduler(workspace).tick(max_jobs=max_jobs, dry_run=dry_run)
    console.print(
        "[bold]cron tick[/bold] "
        f"due={len(result.due)} queued={len(result.queued)} "
        f"skipped={len(result.skipped)} failed={len(result.failed)} dry={result.dry_run}"
    )
    for label, items in (
        ("due", result.due),
        ("queued", result.queued),
        ("skipped", result.skipped),
        ("failed", result.failed),
    ):
        if items:
            console.print(f"[dim]{label}:[/dim] {', '.join(items)}")


@cron_app.command("run")
def cron_run_cmd(
    name: str = typer.Argument(..., help="Nome ou job_id"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Enfileira uma automacao imediatamente, independente do next_run_at."""
    from .automation_scheduler import AutomationScheduler

    try:
        run = AutomationScheduler(workspace).run_now(name, dry_run=dry_run)
    except Exception as exc:
        console.print(f"[red]Erro executando cron job:[/red] {exc}")
        raise typer.Exit(code=1)
    if dry_run:
        console.print(f"[yellow]dry-run[/yellow] cron run {name}")
        return
    console.print(f"[green]cron queued[/green] run={run.run_id} task={run.task_id}")  # type: ignore[union-attr]


@cron_app.command("pause")
def cron_pause_cmd(
    name: str = typer.Argument(..., help="Nome ou job_id"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Pausa uma automacao."""
    from .automation_store import AutomationStore

    job = AutomationStore(workspace).update_job(name, status="paused")
    if job is None:
        console.print(f"[red]Cron job nao encontrado:[/red] {name}")
        raise typer.Exit(code=1)
    console.print(f"[yellow]cron paused[/yellow] {job.name}")


@cron_app.command("resume")
def cron_resume_cmd(
    name: str = typer.Argument(..., help="Nome ou job_id"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Reativa uma automacao pausada/completed, recalculando next_run_at se necessario."""
    from .automation_store import AutomationStore, next_run_after

    store = AutomationStore(workspace)
    job = store.get_job(name)
    if job is None:
        console.print(f"[red]Cron job nao encontrado:[/red] {name}")
        raise typer.Exit(code=1)
    next_run = job.next_run_at or next_run_after(job.schedule)
    updated = store.update_job(job.job_id, status="active", next_run_at=next_run)
    console.print(f"[green]cron active[/green] {updated.name} next={updated.next_run_at}")  # type: ignore[union-attr]


@cron_app.command("delete")
def cron_delete_cmd(
    name: str = typer.Argument(..., help="Nome ou job_id"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Remove uma automacao."""
    from .automation_store import AutomationStore

    if not yes and not typer.confirm(f"Remover cron job '{name}'?", default=False):
        console.print("[dim]Cancelado.[/dim]")
        return
    if not AutomationStore(workspace).delete_job(name):
        console.print(f"[red]Cron job nao encontrado:[/red] {name}")
        raise typer.Exit(code=1)
    console.print(f"[green]cron deleted[/green] {name}")


@cron_app.command("daemon")
def cron_daemon_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    interval: int = typer.Option(60, "--interval", help="Segundos entre ticks"),
    max_jobs: int = typer.Option(10, "--max-jobs"),
):
    """Loop simples do scheduler. Use junto com dispatch daemon para executar."""
    import time as _time

    from .automation_scheduler import AutomationScheduler

    scheduler = AutomationScheduler(workspace)
    console.print(f"[green]Cron scheduler iniciado[/green] workspace={workspace} interval={interval}s")
    try:
        while True:
            result = scheduler.tick(max_jobs=max_jobs)
            if result.queued or result.failed:
                console.print(
                    f"[dim]tick[/dim] due={len(result.due)} "
                    f"queued={len(result.queued)} failed={len(result.failed)}"
                )
            _time.sleep(max(1, int(interval)))
    except KeyboardInterrupt:
        console.print("\n[dim]Cron scheduler encerrado.[/dim]")


# --- research ---------------------------------------------------------------


@research_app.command("trajectory-add")
def research_trajectory_add_cmd(
    objective: str = typer.Argument(..., help="Objetivo ou tarefa da trajetória"),
    kind: str = typer.Option("manual", "--kind"),
    input_json: str = typer.Option("{}", "--input-json"),
    output_json: str = typer.Option("{}", "--output-json"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    """Registra uma trajetória JSONL append-only para avaliação/treino."""
    import json as _json

    from .trajectory_store import TrajectoryStore

    try:
        input_data = _json.loads(input_json)
        output_data = _json.loads(output_json)
        if not isinstance(input_data, dict) or not isinstance(output_data, dict):
            raise ValueError("input-json/output-json devem ser objetos JSON")
    except Exception as exc:
        console.print(f"[red]JSON invalido:[/red] {exc}")
        raise typer.Exit(code=2)
    record = TrajectoryStore(workspace).append(
        kind=kind,
        objective=objective,
        input=input_data,
        output=output_data,
    )
    console.print(f"[green]trajectory registrada[/green] {record.trajectory_id}")


@research_app.command("trajectory-list")
def research_trajectory_list_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    limit: int = typer.Option(20, "--limit"),
    kind: str = typer.Option("", "--kind"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista trajetórias recentes."""
    import json as _json
    from dataclasses import asdict

    from .trajectory_store import TrajectoryStore

    records = TrajectoryStore(workspace).list(limit=limit, kind=kind)
    if as_json:
        console.print(_json.dumps([asdict(record) for record in records], ensure_ascii=False, indent=2))
        return
    if not records:
        console.print("[dim]Nenhuma trajectory registrada.[/dim]")
        return
    table = Table(title=f"Trajectories - {workspace}", show_lines=False)
    table.add_column("ID", style="cyan")
    table.add_column("Kind")
    table.add_column("Objetivo")
    table.add_column("Criado", style="dim")
    for record in records:
        table.add_row(record.trajectory_id, record.kind, record.objective[:80], record.created_at)
    console.print(table)


# --- dispatch ---------------------------------------------------------------


@dispatch_app.command("once")
def dispatch_once_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    max_spawn: int = typer.Option(1, "--max-spawn", help="Maximo de novas tasks neste tick"),
    max_in_progress: int = typer.Option(1, "--max-in-progress", help="Limite global de tasks em execucao"),
    claim_ttl_seconds: int = typer.Option(900, "--claim-ttl-seconds", help="TTL do claim"),
    stale_seconds: int = typer.Option(1800, "--stale-seconds", help="Sem heartbeat por este tempo vira stale"),
    max_retries: int = typer.Option(2, "--max-retries", help="Tentativas antes de FAILED"),
    foreground: bool = typer.Option(False, "--foreground", help="Executa worker neste processo e aguarda fim"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Mostra o que seria claimed sem alterar"),
):
    """Executa um tick do dispatcher hibrido."""
    from .task_dispatcher import TaskDispatcher

    dispatcher = TaskDispatcher(
        workspace,
        claim_ttl_seconds=claim_ttl_seconds,
        stale_seconds=stale_seconds,
        max_retries=max_retries,
    )
    result = dispatcher.dispatch_once(
        dry_run=dry_run,
        max_spawn=max_spawn,
        max_in_progress=max_in_progress,
        spawn_background=not foreground,
        config=config,
        models=models,
    )
    console.print(
        "[bold]dispatch once[/bold] "
        f"crashed={len(result.crashed)} reclaimed={len(result.reclaimed)} claimed={len(result.claimed)} "
        f"spawned={len(result.spawned)} completed={len(result.completed)} "
        f"failed={len(result.failed)} dry={len(result.dry_run)}"
    )
    for label, items in (
        ("crashed", result.crashed),
        ("reclaimed", result.reclaimed),
        ("claimed", result.claimed),
        ("spawned", result.spawned),
        ("completed", result.completed),
        ("failed", result.failed),
        ("dry", result.dry_run),
        ("skipped", result.skipped),
    ):
        if items:
            console.print(f"[dim]{label}:[/dim] {', '.join(items)}")


@dispatch_app.command("status")
def dispatch_status_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    limit: int = typer.Option(10, "--limit", help="Numero de runs/eventos recentes"),
):
    """Mostra fila, runs e eventos recentes do dispatcher."""
    from collections import Counter

    from .kanban_store import KanbanStore

    wm = WorkspaceManager(workspace)
    store = KanbanStore(workspace)
    tasks = wm.list_tasks()
    counts = Counter(t.status for t in tasks)

    table = Table(title=f"Dispatcher status - {workspace}", show_lines=False)
    table.add_column("Status", style="cyan")
    table.add_column("Qtd", justify="right")
    for status in ("READY", "IN_PROGRESS", "FAILED", "DONE", "BLOCKED", "TODO"):
        table.add_row(status, str(counts.get(status, 0)))
    console.print(table)

    runs = store.list_runs(limit=limit)
    if runs:
        run_table = Table(title="Runs recentes", show_lines=False)
        run_table.add_column("Run", style="dim")
        run_table.add_column("Task")
        run_table.add_column("Status")
        run_table.add_column("Tent.", justify="right")
        run_table.add_column("Worker")
        run_table.add_column("Heartbeat", style="dim")
        for run in runs:
            run_table.add_row(
                run.run_id,
                run.task_id,
                run.status,
                str(run.attempt),
                str(run.worker_pid or run.runner or ""),
                run.heartbeat_at,
            )
        console.print(run_table)
    else:
        console.print("[dim]Nenhum task_run registrado ainda.[/dim]")

    events = store.list_events(limit=limit)
    if events:
        event_table = Table(title="Eventos recentes", show_lines=False)
        event_table.add_column("ID", justify="right", style="dim")
        event_table.add_column("Task")
        event_table.add_column("Evento")
        event_table.add_column("Ator")
        event_table.add_column("Mensagem")
        for event in events:
            event_table.add_row(
                str(event.id),
                event.task_id,
                event.event_type,
                event.actor,
                event.message[:80],
            )
        console.print(event_table)


@dispatch_app.command("reclaim")
def dispatch_reclaim_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    claim_ttl_seconds: int = typer.Option(900, "--claim-ttl-seconds"),
    stale_seconds: int = typer.Option(1800, "--stale-seconds"),
):
    """Detecta workers mortos e devolve claims stale para READY."""
    from .task_dispatcher import TaskDispatcher

    dispatcher = TaskDispatcher(
        workspace,
        claim_ttl_seconds=claim_ttl_seconds,
        stale_seconds=stale_seconds,
    )
    crashed = dispatcher.detect_crashed_workers()
    reclaimed = dispatcher.reclaim_stale()
    console.print(
        f"[bold]dispatch reclaim[/bold] crashed={len(crashed)} reclaimed={len(reclaimed)}"
    )
    if crashed:
        console.print(f"[dim]crashed:[/dim] {', '.join(crashed)}")
    if reclaimed:
        console.print(f"[dim]reclaimed:[/dim] {', '.join(reclaimed)}")


@dispatch_app.command("cancel")
def dispatch_cancel_cmd(
    task_id: str = typer.Argument(..., help="ID da task"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    reason: str = typer.Option("cancelled by operator", "--reason", "-r"),
    terminate_worker: bool = typer.Option(False, "--terminate-worker", help="Tenta encerrar PID do worker"),
):
    """Cancela uma task em execucao e fecha o run como cancelled."""
    from .task_dispatcher import TaskDispatcher

    task = TaskDispatcher(workspace).cancel_task(
        task_id,
        reason=reason,
        terminate_worker=terminate_worker,
    )
    console.print(f"[yellow]{task.id}[/yellow] -> [BLOCKED] {task.title}")


@dispatch_app.command("retry")
def dispatch_retry_cmd(
    task_id: str = typer.Argument(..., help="ID da task"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    reason: str = typer.Option("manual retry", "--reason", "-r"),
):
    """Retorna uma task FAILED/BLOCKED para READY."""
    from .task_dispatcher import TaskDispatcher

    task = TaskDispatcher(workspace).retry_failed(task_id, reason=reason)
    console.print(f"[cyan]{task.id}[/cyan] -> [READY] {task.title}")


@dispatch_app.command("daemon")
def dispatch_daemon_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    interval: int = typer.Option(30, "--interval", help="Segundos entre ticks"),
    max_spawn: int = typer.Option(1, "--max-spawn"),
    max_in_progress: int = typer.Option(1, "--max-in-progress"),
    claim_ttl_seconds: int = typer.Option(900, "--claim-ttl-seconds"),
    stale_seconds: int = typer.Option(1800, "--stale-seconds"),
    max_retries: int = typer.Option(2, "--max-retries"),
):
    """Loop duravel do dispatcher. Ctrl+C para parar."""
    import time as _time
    from .task_dispatcher import TaskDispatcher

    dispatcher = TaskDispatcher(
        workspace,
        claim_ttl_seconds=claim_ttl_seconds,
        stale_seconds=stale_seconds,
        max_retries=max_retries,
    )
    console.print(f"[green]Dispatcher iniciado[/green] workspace={workspace} interval={interval}s")
    dispatcher.record_daemon_started(
        interval=interval,
        max_spawn=max_spawn,
        max_in_progress=max_in_progress,
    )
    try:
        while True:
            result = dispatcher.watchdog_tick(
                max_spawn=max_spawn,
                max_in_progress=max_in_progress,
                config=config,
                models=models,
            )
            if result.crashed or result.reclaimed or result.claimed or result.spawned or result.failed:
                console.print(
                    f"[dim]tick[/dim] crashed={len(result.crashed)} reclaimed={len(result.reclaimed)} "
                    f"claimed={len(result.claimed)} spawned={len(result.spawned)} "
                    f"failed={len(result.failed)}"
                )
            _time.sleep(max(1, interval))
    except KeyboardInterrupt:
        dispatcher.record_daemon_stopped(reason="KeyboardInterrupt")
        console.print("\n[dim]Dispatcher encerrado.[/dim]")


@dispatch_app.command("worker")
def dispatch_worker_cmd(
    task_id: str = typer.Argument(..., help="ID da task"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    models: Path = typer.Option(Path("models.yaml"), "--models"),
    claim_id: str = typer.Option("", "--claim-id", help="Claim esperado"),
    claim_ttl_seconds: int = typer.Option(900, "--claim-ttl-seconds"),
    stale_seconds: int = typer.Option(1800, "--stale-seconds"),
):
    """Worker interno do dispatcher. Normalmente chamado por dispatch once/daemon."""
    from .task_dispatcher import TaskDispatcher, TaskDispatcherError

    dispatcher = TaskDispatcher(
        workspace,
        claim_ttl_seconds=claim_ttl_seconds,
        stale_seconds=stale_seconds,
    )
    try:
        result = dispatcher.run_claimed_worker(
            task_id,
            claim_id=claim_id,
            config=config,
            models=models,
        )
    except TaskDispatcherError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    if result.success:
        console.print(f"[green]Task {task_id} concluida.[/green]")
    else:
        console.print(f"[red]Task {task_id} falhou:[/red] {result.error}")
        raise typer.Exit(code=1)


# --- serve ------------------------------------------------------------------


@serve_app.callback()
def serve(
    ctx: typer.Context,
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    models: Path = typer.Option(Path("models.yaml"), "--models", help="Caminho do models.yaml"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(Path(".runtime_state.json"), "--state-file"),
    host: str = typer.Option("", "--host", help="Host de escuta (padrao: config serve.host)"),
    port: int = typer.Option(0, "--port", help="Porta (padrao: config serve.port)"),
    model: str = typer.Option("", "--model", help="Sobrescreve modelo do config"),
    api_key: str = typer.Option("", "--api-key", help="Sobrescreve serve.api_key do config"),
    sessions_dir: Path = typer.Option(Path("memory/sessions"), "--sessions-dir"),
    gateway_port: int = typer.Option(
        0, "--gateway-port", "-g",
        help="Porta do gateway WebSocket Claw3D (0 = desabilitado). Ex: --gateway-port 18789",
    ),
):
    """Inicia o Bauer Agent como servidor HTTP (REST + SSE).

    Requer: pip install 'bauer-agent[server]'

    Endpoints disponiveis apos iniciar:
      GET  /health        — liveness
      GET  /status        — modelo e tools
      POST /chat          — envia mensagem, recebe resposta
      GET  /stream        — resposta em tempo real (SSE)
      GET  /sessions      — lista sessoes (requer auth)
      GET  /docs          — documentacao interativa (Swagger)

    Use 'bauer serve service install' para rodar como servico do sistema.
    """
    if ctx.invoked_subcommand is not None:
        return
    try:
        from .server import create_app, run_server
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    cfg, reg = _load_or_die(config, models)
    setup_logging(cfg.logging.level, cfg.logging.file)

    state = _get_or_run_state(cfg, reg, state_file)

    if not state.get("ollama_alive"):
        console.print("[red]Ollama offline.[/red] Verifique se o Ollama esta rodando.")
        raise typer.Exit(code=1)

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)

    _client = _build_client(cfg)

    model_name = model or _resolve_model_with_ram_check(
        state["configured_model"], reg, _client,
        state["ram_available_mb"], cfg.runtime.safety_margin_mb, _MEMORY_DIR,
    )
    if not _client.has_model(model_name):
        console.print(
            f"[red]Modelo '{model_name}' nao encontrado no Ollama.[/red]\n"
            f"Rode: [bold]ollama pull {model_name}[/bold]"
        )
        raise typer.Exit(code=1)

    applied_context = state["context"]["applied"]
    router = _build_router(cfg, workspace)

    from .agent import _build_system_prompt
    system_prompt = _build_system_prompt(router)

    serve_host = host or cfg.serve.host
    serve_port = port or cfg.serve.port
    serve_key = api_key or cfg.serve.api_key

    fastapi_app = create_app(
        model_name=model_name,
        applied_context=applied_context,
        router=router,
        client=_client,
        system_prompt=system_prompt,
        sessions_dir=sessions_dir,
        api_key=serve_key,
        rate_limit_requests=cfg.serve.rate_limit_requests,
        rate_limit_window_s=cfg.serve.rate_limit_window_s,
        rate_limit_per_key=cfg.serve.rate_limit_per_key,
        cors_origins=list(cfg.serve.cors_origins) or None,
        enable_gzip=cfg.serve.enable_gzip,
        enable_access_log=cfg.serve.enable_access_log,
    )

    auth_status = "[green]habilitada[/green]" if serve_key else "[yellow]desabilitada[/yellow]"
    base_url = f"http://{serve_host}:{serve_port}"
    console.print(f"\n[bold]Bauer Agent Server[/bold] — {model_name}")
    console.print(f"  HTTP:       {base_url}")
    console.print(f"  Docs:       {base_url}/docs")
    console.print(f"  Auth:       {auth_status}")
    console.print(f"  Tools:      {', '.join(router.available_tools())}")
    console.print(f"[dim]  OpenAI-compat:  POST {base_url}/v1/chat/completions[/dim]")

    # Gateway WebSocket opcional
    if gateway_port > 0:
        _start_gateway_thread_cli(
            bauer_url=base_url,
            host=serve_host or "0.0.0.0",
            port=gateway_port,
            api_key=serve_key,
            console=console,
        )
    else:
        console.print(
            f"[dim]  Claw3D Gateway: desabilitado (use --gateway-port 18789 para ativar)[/dim]"
        )

    console.print()
    pid_file = workspace / ".bauer_serve" / "serve.pid"
    run_server(fastapi_app, host=serve_host, port=serve_port, pid_file=pid_file)


# --- learning ---------------------------------------------------------------


@learning_app.command("show")
def learning_show(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    state_file: Path = typer.Option(Path(".runtime_state.json"), "--state-file"),
):
    """Mostra resumo do aprendizado acumulado (experiencias e falhas)."""
    from .learning_engine import LearningEngine

    engine = LearningEngine(memory_dir)
    summary = engine.summary()

    table = Table(title="Adaptive Learning — resumo")
    table.add_column("fonte", style="cyan")
    table.add_column("entradas", justify="right")
    _LABELS = {
        "model_experiences": "MODEL_EXPERIENCE.md",
        "failed_attempts": "FAILED_ATTEMPTS.md",
    }
    for key, count in summary.items():
        table.add_row(_LABELS.get(key, key), str(count))
    console.print(table)

    state = read_state(state_file)
    machine_id = state.get("machine_id", "") if state else ""
    if machine_id:
        console.print(f"[dim]Machine: {machine_id}[/dim]")
    console.print("[dim]Use 'bauer learning explain' para ver recomendacoes.[/dim]")


@learning_app.command("explain")
def learning_explain(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    state_file: Path = typer.Option(Path(".runtime_state.json"), "--state-file"),
):
    """Mostra recomendacoes com motivo e evidencia explicita."""
    from .learning_engine import LearningEngine

    engine = LearningEngine(memory_dir)
    state = read_state(state_file)
    machine_id = state.get("machine_id", "") if state else ""

    recs = engine.recommend(machine_id=machine_id)

    _SEVERITY_COLOR = {"info": "dim", "suggestion": "cyan", "warning": "yellow"}
    for i, rec in enumerate(recs, 1):
        color = _SEVERITY_COLOR.get(rec.severity, "white")
        console.print(
            f"\n[bold]{i}.[/bold] [{color}][{rec.severity.upper()}][/{color}] {rec.action}"
        )
        console.print(f"   [dim]Motivo:[/dim] {rec.reason}")
        if rec.evidence:
            console.print("   [dim]Evidencia:[/dim]")
            for ev in rec.evidence:
                console.print(f"     - {ev}")

    console.print(
        "\n[dim]Nenhuma config foi alterada. "
        "Use 'bauer learning reset' para limpar o aprendizado.[/dim]"
    )


@learning_app.command("export")
def learning_export(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    output_dir: Path = typer.Option(Path("datasets"), "--output", help="Diretorio de saida"),
):
    """Exporta aprendizado como datasets JSONL para preparacao de fine-tuning (Fase 8).

    Gera:
      datasets/model_experience.jsonl  — historico de modelos
      datasets/failed_attempts.jsonl   — erros e correcoes
    """
    import json
    from .learning_engine import LearningEngine

    output_dir.mkdir(parents=True, exist_ok=True)
    engine = LearningEngine(memory_dir)

    # Exporta MODEL_EXPERIENCE
    exps = engine.load_experience()
    exp_path = output_dir / "model_experience.jsonl"
    with exp_path.open("w", encoding="utf-8") as f:
        for e in exps:
            record = {
                "timestamp": e.timestamp,
                "model": e.title.split(" — ")[0].strip() if " — " in e.title else e.title,
                "context_tokens": e.context_tokens,
                "result": e.result,
                "ram_used_mb": e.ram_used_mb,
                "machine_id": e.machine_id,
                "lesson": e.lesson,
                "input": f"Modelo {e.title} com contexto {e.context_tokens} tokens.",
                "output": f"Resultado: {e.result}. {e.lesson}".strip(". ") + ".",
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    console.print(f"[green]exportado:[/green] {exp_path}  ({len(exps)} registros)")

    # Exporta FAILED_ATTEMPTS
    failures = engine.load_failures()
    fail_path = output_dir / "failed_attempts.jsonl"
    with fail_path.open("w", encoding="utf-8") as f:
        for fa in failures:
            record = {
                "timestamp": fa.timestamp,
                "title": fa.title,
                "error": fa.error,
                "fix": fa.fix,
                "machine_id": fa.machine_id,
                "input": f"Erro: {fa.error}",
                "output": fa.fix if fa.fix else "Sem correcao registrada.",
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    console.print(f"[green]exportado:[/green] {fail_path}  ({len(failures)} registros)")
    console.print(f"\n[dim]Datasets em {output_dir}/ — prontos para fine-tuning LoRA/QLoRA.[/dim]")


@learning_app.command("forget-model")
def learning_forget_model(
    model_name: str = typer.Argument(..., help="Nome exato do modelo (ex: qwen2.5-coder:7b)"),
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    confirm: bool = typer.Option(False, "--confirm", help="Pular confirmacao interativa"),
):
    """Remove todas as entradas de um modelo dos arquivos de aprendizado.

    Cria backup .bak antes de modificar. Nenhum dado e deletado permanentemente.
    """
    from .learning_engine import LearningEngine

    if not confirm:
        typer.confirm(
            f"Remover todas as entradas de '{model_name}' de MODEL_EXPERIENCE.md e FAILED_ATTEMPTS.md?",
            abort=True,
        )

    engine = LearningEngine(memory_dir)
    results = engine.forget_model(model_name)

    total = sum(results.values())
    if total == 0:
        console.print(f"[dim]Nenhuma entrada encontrada para '{model_name}'.[/dim]")
        return

    for filename, count in results.items():
        if count > 0:
            bak = (memory_dir / filename).with_suffix(".md.bak")
            console.print(
                f"[green]removido:[/green] {count} entrada(s) de {filename}  "
                f"[dim](backup: {bak.name})[/dim]"
            )


@learning_app.command("reset")
def learning_reset(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    confirm: bool = typer.Option(False, "--confirm", help="Pular confirmacao interativa"),
):
    """Limpa os arquivos de aprendizado (cria backup .bak antes).

    Arquivos afetados: FAILED_ATTEMPTS.md, MODEL_EXPERIENCE.md, RUNTIME_LESSONS.md.
    O cabecalho de cada arquivo e preservado. Nenhum dado e deletado permanentemente.
    """
    from .learning_engine import LearningEngine

    if not confirm:
        typer.confirm(
            "Limpar FAILED_ATTEMPTS.md, MODEL_EXPERIENCE.md e RUNTIME_LESSONS.md? "
            "(backups .bak serao criados antes)",
            abort=True,
        )

    engine = LearningEngine(memory_dir)
    reset_paths = engine.reset()

    if reset_paths:
        for p in reset_paths:
            bak = p.with_suffix(".md.bak")
            console.print(f"[green]resetado:[/green] {p.name}  [dim](backup: {bak.name})[/dim]")
    else:
        console.print("[dim]Nenhum arquivo de aprendizado encontrado.[/dim]")


@learning_app.command("analyze")
def learning_analyze(
    memory_dir: Path = typer.Option(_MEMORY_DIR, "--dir", help="Diretorio de memoria"),
    model: str = typer.Option("", "--model", "-m", help="Modelo a usar (default: config.yaml)"),
    show_last: bool = typer.Option(False, "--last", "-l", help="Exibe a ultima analise salva sem gerar nova"),
):
    """Analisa os arquivos de memória usando o LLM e gera relatório com insights.

    Lê MODEL_EXPERIENCE.md, FAILED_ATTEMPTS.md, RUNTIME_LESSONS.md e SKILLS_LEARNED.md,
    envia ao modelo configurado e salva o relatório em memory/LEARNING_ANALYSIS.md.

    Nunca altera config. Nunca executa nada automaticamente — apenas analisa e sugere.
    """
    from rich.markdown import Markdown

    from .learning_engine import LearningEngineV2

    engine = LearningEngineV2(memory_dir)

    if show_last:
        last = engine.load_last_analysis()
        if last:
            console.print(Markdown(last))
        else:
            console.print("[dim]Nenhuma análise salva. Rode: bauer learning analyze[/dim]")
        return

    summary = engine._v1.summary()
    total = sum(summary.values())
    if total == 0:
        console.print(
            "[yellow]Nenhum dado de aprendizado encontrado.[/yellow]\n"
            "[dim]Use 'bauer memory add-model-exp' para registrar experiências.[/dim]"
        )
        return

    console.print(
        f"[dim]Dados: {', '.join(f'{k}: {v}' for k, v in summary.items())}[/dim]"
    )
    console.print("[bold cyan]Analisando com modelo...[/bold cyan] [dim](pode levar alguns segundos)[/dim]")
    console.print()

    try:
        result = engine.analyze(model=model or None)
    except Exception as exc:
        console.print(f"[red]Erro ao analisar: {exc}[/red]")
        raise typer.Exit(1)

    console.print(Markdown(result.report))
    console.print()
    console.print(
        f"[dim]Modelo: {result.model_used} | Salvo em: memory/LEARNING_ANALYSIS.md[/dim]"
    )


# --- auth -------------------------------------------------------------------


@auth_app.command("login")
def auth_login(
    provider: str = typer.Option(
        "",
        "--provider", "-p",
        help=(
            "Provider a autenticar (omita para menu interativo).\n"
            "API Key:     openai-api | anthropic | groq | deepseek | openrouter |\n"
            "             mistral | xai | together | gemini | custom\n"
            "Device Flow: github | copilot\n"
            "OAuth:       openai"
        ),
    ),
):
    """Autentica com um provider cloud.

    Sem --provider: exibe menu interativo com todos os 14 providers.

    Exemplos:
      bauer auth login                   # menu interativo
      bauer auth login --provider copilot
      bauer auth login -p groq
    """
    from .auth import cmd_login

    cmd_login(provider if provider else None)


@auth_app.command("status")
def auth_status():
    """Mostra providers autenticados e status dos tokens."""
    from .auth import cmd_status

    cmd_status()


@auth_app.command("logout")
def auth_logout(
    provider: str = typer.Option("", "--provider", "-p", help="Provider especifico (vazio = todos)"),
):
    """Remove autenticacao de um provider (ou todos)."""
    from .auth import cmd_logout

    cmd_logout(provider if provider else None)


@auth_app.command("providers")
def auth_providers():
    """Lista providers disponíveis para autenticacao."""
    from .auth import cmd_list_providers

    cmd_list_providers()


# --- logs -------------------------------------------------------------------


@app.command()
def logs(
    config: Path = typer.Option(Path("config.yaml"), "--config"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Modo tail -f (tempo real)"),
    lines: int = typer.Option(50, "--lines", "-n", help="Numero de linhas iniciais a mostrar"),
):
    """Mostra o log do Bauer. Use --follow para acompanhar em tempo real."""
    import time

    try:
        cfg = load_config(config)
        log_path = Path(cfg.logging.file)
    except ConfigError:
        log_path = Path("logs/bauer.log")

    if not log_path.exists():
        console.print(f"[yellow]Log nao encontrado: {log_path}[/yellow]")
        console.print("[dim]O arquivo sera criado quando o Bauer iniciar.[/dim]")
        raise typer.Exit(code=1)

    # Mostra as últimas N linhas
    all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in all_lines[-lines:]:
        console.print(line)

    if not follow:
        return

    console.print(f"\n[dim]--- seguindo {log_path} (Ctrl+C para sair) ---[/dim]\n")
    try:
        with log_path.open(encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)  # vai para o fim
            while True:
                line = f.readline()
                if line:
                    console.print(line.rstrip())
                else:
                    time.sleep(0.2)
    except KeyboardInterrupt:
        console.print("\n[dim]Log encerrado.[/dim]")


# --- spec (spec-driven development) ----------------------------------------

_SPECS_DIR = Path("specs")


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
    from .spec_manager import Spec, SpecManager
    from .spec_wizard import wizard_create_spec

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
    from .spec_manager import SpecManager

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
    from .spec_manager import SpecManager

    mgr = SpecManager(specs_dir)
    spec = mgr.get(spec_id)

    if not spec:
        console.print(f"[yellow]Spec '[cyan]{spec_id}[/cyan]' nao encontrado.[/yellow]")
        if typer.confirm(f"Criar o spec '{spec_id}' agora?", default=True):
            from .spec_wizard import wizard_create_spec
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
    from .spec_manager import SpecManager, _VALID_STATUSES

    if new_status not in _VALID_STATUSES:
        console.print(f"[red]Status inválido:[/red] '{new_status}'. Válidos: {', '.join(sorted(_VALID_STATUSES))}")
        raise typer.Exit(code=1)

    mgr = SpecManager(specs_dir)
    spec = mgr.get(spec_id)
    if not spec:
        console.print(f"[yellow]Spec '[cyan]{spec_id}[/cyan]' nao encontrado.[/yellow]")
        if typer.confirm(f"Criar o spec '{spec_id}' agora?", default=True):
            from .spec_wizard import wizard_create_spec
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
    from .spec_manager import SpecManager

    mgr = SpecManager(specs_dir)
    if not mgr.get(spec_id):
        console.print(f"[red]Spec '[cyan]{spec_id}[/cyan]' nao encontrado.[/red]")
        console.print(f"[dim]Liste os specs: [bold]bauer spec list[/bold][/dim]")
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
    from .spec_manager import SpecManager

    mgr = SpecManager(specs_dir)
    ctx = mgr.specs_context(query=query, compact=compact)
    if not ctx:
        console.print("[dim]Nenhum spec aprovado/implementado encontrado.[/dim]")
    else:
        console.print(ctx)


# ─────────────────────────────────────────────────────────────────────────────
# company — gestão multi-empresa
# ─────────────────────────────────────────────────────────────────────────────
# _COMPANIES_DIR já definido no topo do módulo como workspace/companies/


@company_app.command("create")
def company_create(
    slug: str = typer.Argument(..., help="ID da empresa (ex: acme-corp)"),
    name: str = typer.Option(..., "--name", "-n", help="Nome da empresa (ex: 'Acme Corp')"),
    industry: str = typer.Option("tecnologia", "--industry", "-i", help="Setor da empresa"),
    language: str = typer.Option("pt", "--language", "-l", help="Idioma padrao (pt|en|es)"),
    companies_dir: Path = typer.Option(_COMPANIES_DIR, "--dir"),
    activate: bool = typer.Option(True, "--activate/--no-activate", help="Ativar esta empresa apos criar"),
):
    """Cria uma nova empresa com namespace isolado em companies/<slug>/."""
    from .company_manager import CompanyManager, CompanyManagerError

    cm = CompanyManager(companies_dir)
    try:
        company = cm.create(slug, name, industry=industry, language=language)
    except CompanyManagerError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    console.print(Panel(
        f"[bold green]Empresa criada com sucesso![/bold green]\n\n"
        f"  ID:       [cyan]{company.id}[/cyan]\n"
        f"  Nome:     {company.name}\n"
        f"  Setor:    {industry}\n"
        f"  Idioma:   {company.language}\n\n"
        f"  [dim]Diretorio: {companies_dir / slug}[/dim]\n"
        f"  [dim]Edite o contexto: {companies_dir / slug / 'company.yaml'}[/dim]",
        title="[bold]Nova Empresa[/bold]",
        border_style="green",
    ))

    if activate:
        cm.set_active(slug)
        console.print(f"[green]Empresa [cyan]{slug}[/cyan] ativada.[/green]")

    console.print(
        f"\n[dim]Adicione agents especificos: "
        f"[bold]bauer agent create --agents {companies_dir / slug / 'agents.yaml'}[/bold][/dim]"
    )


@company_app.command("list")
def company_list(
    companies_dir: Path = typer.Option(_COMPANIES_DIR, "--dir"),
):
    """Lista todas as empresas cadastradas."""
    from .company_manager import CompanyManager

    cm = CompanyManager(companies_dir)
    companies = cm.list_companies()
    active_id = cm.get_active_id()

    if not companies:
        console.print("[dim]Nenhuma empresa cadastrada.[/dim]")
        console.print(f"[dim]Crie uma: [bold]bauer company create <slug> --name 'Nome'[/bold][/dim]")
        return

    from rich.table import Table
    table = Table(show_header=True, header_style="bold cyan", border_style="dim")
    table.add_column("", width=2)
    table.add_column("ID", style="cyan")
    table.add_column("Nome")
    table.add_column("Idioma", justify="center", width=8)
    table.add_column("Departments", justify="right")
    table.add_column("Criada em", style="dim")

    for c in companies:
        is_active = c.id == active_id
        marker = "[bold green]▶[/bold green]" if is_active else " "
        name_style = f"[bold]{c.name}[/bold]" if is_active else c.name
        table.add_row(
            marker,
            c.id,
            name_style,
            c.language,
            str(len(c.departments)),
            c.created_at[:10] if c.created_at else "—",
        )

    console.print(table)
    if active_id:
        console.print(f"\n[dim]Empresa ativa: [cyan]{active_id}[/cyan][/dim]")
    else:
        console.print(
            f"\n[dim]Nenhuma empresa ativa. Selecione: "
            f"[bold]bauer company select <id>[/bold][/dim]"
        )


@company_app.command("select")
def company_select(
    slug: str = typer.Argument(..., help="ID da empresa a ativar"),
    companies_dir: Path = typer.Option(_COMPANIES_DIR, "--dir"),
):
    """Define a empresa ativa para esta sessao."""
    from .company_manager import CompanyManager, CompanyManagerError

    cm = CompanyManager(companies_dir)
    try:
        cm.set_active(slug)
    except CompanyManagerError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    company = cm.get(slug)
    console.print(
        f"[green]Empresa ativa: [bold cyan]{slug}[/bold cyan]"
        + (f" — {company.name}" if company else "")
        + "[/green]"
    )
    console.print(
        f"[dim]Todos os agents usarao o contexto de [cyan]{slug}[/cyan] "
        f"automaticamente.[/dim]"
    )


@company_app.command("info")
def company_info(
    slug: str = typer.Argument("", help="ID da empresa (padrao: empresa ativa)"),
    companies_dir: Path = typer.Option(_COMPANIES_DIR, "--dir"),
):
    """Exibe detalhes de uma empresa."""
    from .company_manager import CompanyManager

    cm = CompanyManager(companies_dir)

    if not slug:
        slug = cm.get_active_id() or ""
        if not slug:
            console.print("[yellow]Nenhuma empresa ativa.[/yellow]")
            console.print("[dim]Use: [bold]bauer company select <id>[/bold][/dim]")
            raise typer.Exit(code=1)

    company = cm.get(slug)
    if company is None:
        console.print(f"[red]Empresa '{slug}' nao encontrada.[/red]")
        raise typer.Exit(code=1)

    active_id = cm.get_active_id()
    is_active = company.id == active_id

    lines = [
        f"  [bold]ID:[/bold]        [cyan]{company.id}[/cyan]"
        + (" [bold green](ativa)[/bold green]" if is_active else ""),
        f"  [bold]Nome:[/bold]      {company.name}",
        f"  [bold]Idioma:[/bold]    {company.language}",
    ]
    if company.model:
        lines.append(f"  [bold]Modelo:[/bold]    {company.provider}/{company.model}")
    if company.agent_prefix:
        lines.append(f"  [bold]Prefixo:[/bold]   {company.agent_prefix}")
    if company.departments:
        lines.append(f"  [bold]Depts:[/bold]     {', '.join(company.departments)}")
    if company.tools_allowed:
        lines.append(f"  [bold]Tools:[/bold]     {', '.join(company.tools_allowed)}")
    lines.append(f"  [bold]Criada:[/bold]    {company.created_at[:10] if company.created_at else '—'}")

    if company.context.strip():
        lines.append(f"\n  [bold]Contexto injetado:[/bold]")
        for ln in company.context.strip().splitlines():
            lines.append(f"  [dim]{ln}[/dim]")

    console.print(Panel(
        "\n".join(lines),
        title=f"[bold]Empresa: {company.name}[/bold]",
        border_style="cyan",
    ))

    # Mostra agents especificos desta empresa
    agents_file = companies_dir / slug / "agents.yaml"
    if agents_file.exists():
        from .agent_registry import AgentRegistry
        reg = AgentRegistry(agents_file)
        agents = reg.list_agents()
        if agents:
            console.print(f"\n[dim]Agents especificos ({len(agents)}):[/dim]")
            for ag in agents:
                console.print(f"  [cyan]{ag.name}[/cyan] — {ag.description}")
        else:
            console.print(f"\n[dim]Sem agents especificos. "
                          f"Crie: [bold]bauer agent create --agents {agents_file}[/bold][/dim]")


@company_app.command("clear")
def company_clear():
    """Remove a selecao de empresa ativa (volta ao modo global)."""
    from .company_manager import CompanyManager

    cm = CompanyManager(_COMPANIES_DIR)
    active = cm.get_active_id()
    if not active:
        console.print("[dim]Nenhuma empresa ativa no momento.[/dim]")
        return

    cm.clear_active()
    console.print(f"[yellow]Empresa '[cyan]{active}[/cyan]' desativada. Modo global restaurado.[/yellow]")


@company_app.command("delete")
def company_delete(
    slug: str = typer.Argument(..., help="ID da empresa a remover"),
    companies_dir: Path = typer.Option(_COMPANIES_DIR, "--dir"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Pula confirmacao"),
):
    """Remove uma empresa e todos os seus dados. IRREVERSIVEL."""
    from .company_manager import CompanyManager
    from rich.prompt import Confirm

    cm = CompanyManager(companies_dir)
    company = cm.get(slug)
    if company is None:
        console.print(f"[red]Empresa '{slug}' nao encontrada.[/red]")
        raise typer.Exit(code=1)

    if not yes:
        if not Confirm.ask(
            f"[bold red]Remover empresa '{slug}' ({company.name}) e TODOS os seus dados?[/bold red]",
            default=False,
        ):
            console.print("[dim]Cancelado.[/dim]")
            return

    # Desativa se for a empresa ativa
    if cm.get_active_id() == slug:
        cm.clear_active()

    cm.delete(slug)
    console.print(f"[red]Empresa '[cyan]{slug}[/cyan]' removida.[/red]")


@company_app.command("personas")
def company_personas(
    department: str = typer.Argument("", help="Filtrar por departamento (ex: tech, finance, hr)"),
):
    """Lista todas as personas disponíveis por departamento."""
    from .agent_registry import PERSONAS
    from rich.table import Table

    # Mapeamento de grupos
    groups: dict[str, list[str]] = {
        "Tecnologia": ["python", "backend", "frontend", "devops", "sre", "security",
                       "data-engineer", "ml-engineer", "sql", "architect", "scrum-master", "docs"],
        "C-Suite": ["ceo", "cto", "cfo", "coo", "cmo", "chro"],
        "Financeiro": ["financial-analyst", "controller", "internal-auditor", "treasury"],
        "Marketing": ["brand-manager", "copywriter", "seo", "growth", "social-media"],
        "Vendas": ["sdr", "account-executive", "sales-engineer", "customer-success"],
        "RH / Pessoas": ["recruiter", "learning-dev", "people-analytics", "comp-benefits"],
        "Juridico": ["legal-contracts", "compliance", "ip-specialist"],
        "Operacoes": ["supply-chain", "project-manager", "business-analyst", "process-engineer"],
        "Suporte": ["support-agent", "qa-analyst", "knowledge-manager"],
        "Dados & Analytics": ["data-scientist", "bi-analyst", "data-architect"],
        "Produto": ["product-manager", "product-owner", "ux-researcher", "ux-designer"],
    }

    dept_filter = department.lower()

    for group_name, keys in groups.items():
        # Filtra por departamento se especificado
        if dept_filter and dept_filter not in group_name.lower():
            # Tenta match parcial nos nomes das personas
            keys_filtered = [k for k in keys if dept_filter in k]
            if not keys_filtered:
                continue
            keys = keys_filtered

        table = Table(
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
            title=f"[bold]{group_name}[/bold]",
            title_justify="left",
        )
        table.add_column("Persona", style="cyan", width=22)
        table.add_column("Descricao")

        for key in keys:
            p = PERSONAS.get(key)
            if p:
                table.add_row(key, p["description"])

        console.print(table)
        console.print()

    total = len(PERSONAS)
    console.print(
        f"[dim]{total} personas disponíveis. "
        f"Use: [bold]bauer agent run <persona>[/bold] para iniciar.[/dim]"
    )


def main():
    app()


# ── gateway helpers ───────────────────────────────────────────────────────────

def _start_gateway_thread_cli(
    bauer_url: str,
    host: str,
    port: int,
    api_key: str,
    console: Console,
) -> None:
    """Inicia o gateway WebSocket em daemon thread e imprime status."""
    try:
        from .gateway import start_gateway_thread
        start_gateway_thread(bauer_url=bauer_url, host=host, port=port, api_key=api_key)
        console.print(
            f"[dim]  Claw3D Gateway: [bold]ws://{host}:{port}[/bold] "
            f"(adapterType=bauer) — configure no Claw3D[/dim]"
        )
    except RuntimeError as exc:
        console.print(f"[yellow]  Gateway WebSocket indisponivel: {exc}[/yellow]")


# ── bauer gateway command ─────────────────────────────────────────────────────


@app.command("gateway-ws")
def gateway_cmd(
    bauer_url: str = typer.Option(
        "http://localhost:7770",
        "--bauer-url", "-u",
        help="URL do Bauer HTTP server (deve estar rodando)",
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Interface de escuta"),
    port: int = typer.Option(18789, "--port", "-p", help="Porta WebSocket"),
    api_key: str = typer.Option("", "--api-key", help="API key do Bauer serve"),
):
    """Inicia o Gateway WebSocket para integracao com Claw3D / escritorio virtual.

    Implementa o protocolo Hermes WebSocket — permite que o Claw3D conecte
    ao Bauer como se fosse o Hermes Agent, usando adapterType=bauer.

    Prerequisito: bauer serve (ou bauer agent --port) deve estar rodando.

    Configuracao no Claw3D:
      gateway.url   = ws://localhost:18789
      adapterType   = bauer  (ou hermes — mesmo protocolo)
    """
    try:
        from .gateway import run_gateway_sync
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    console.print(f"\n[bold]Bauer Gateway[/bold] — WebSocket Claw3D")
    console.print(f"  WS:        ws://{host}:{port}")
    console.print(f"  Backend:   {bauer_url}")
    console.print(f"  Auth:      {'habilitada' if api_key else 'desabilitada'}")
    console.print(
        f"\n[dim]  Configure no Claw3D:")
    console.print(f"    gateway.url         = ws://{host}:{port}")
    console.print(f"    gateway.adapterType = bauer[/dim]\n")

    try:
        run_gateway_sync(bauer_url=bauer_url, host=host, port=port, api_key=api_key)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        console.print("\n[dim]Gateway encerrado.[/dim]")


# ── migrate ──────────────────────────────────────────────────────────────────


@app.command("gateway-channel-add")
def gateway_channel_add_cmd(
    name: str = typer.Argument(..., help="Nome logico do canal"),
    platform: str = typer.Argument(..., help="file, webhook, telegram, discord, slack ou whatsapp"),
    target: str = typer.Argument(..., help="Destino da plataforma"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    metadata_json: str = typer.Option("{}", "--metadata-json", help="Metadados JSON sem segredos; use *_env"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
):
    """Registra ou atualiza um canal real de gateway."""
    import json as _json

    from .gateway_channels import GatewayChannelRegistry

    try:
        metadata = _json.loads(metadata_json)
        if not isinstance(metadata, dict):
            raise ValueError("metadata-json deve ser um objeto JSON")
        channel = GatewayChannelRegistry(workspace).upsert(
            name=name,
            platform=platform,
            target=target,
            enabled=enabled,
            metadata=metadata,
        )
    except Exception as exc:
        console.print(f"[red]Erro configurando gateway channel:[/red] {exc}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]gateway channel salvo[/green] "
        f"name={channel.name} platform={channel.platform} enabled={channel.enabled}"
    )


@app.command("gateway-channels")
def gateway_channels_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    include_disabled: bool = typer.Option(False, "--include-disabled"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista canais reais configurados para entrega do gateway."""
    import json as _json

    from .gateway_channels import GatewayChannelRegistry

    channels = GatewayChannelRegistry(workspace).list_channels(include_disabled=include_disabled)
    payload = [channel.to_public_dict() for channel in channels]
    if as_json:
        console.print(_json.dumps(payload, ensure_ascii=False, indent=2), soft_wrap=True)
        return
    if not channels:
        console.print("[dim]Nenhum gateway channel configurado.[/dim]")
        return
    table = Table(title=f"Gateway channels - {workspace}", show_lines=False)
    table.add_column("Name", style="cyan")
    table.add_column("Platform")
    table.add_column("Enabled")
    table.add_column("Target")
    for channel in channels:
        table.add_row(channel.name, channel.platform, str(channel.enabled), channel.target[:72])
    console.print(table)


@app.command("gateway-channel-delete")
def gateway_channel_delete_cmd(
    name: str = typer.Argument(..., help="Nome logico do canal"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Remove um canal configurado do gateway."""
    from .gateway_channels import GatewayChannelRegistry

    if not yes and not typer.confirm(f"Remover gateway channel '{name}'?", default=False):
        console.print("[dim]Cancelado.[/dim]")
        return
    if not GatewayChannelRegistry(workspace).delete(name):
        console.print(f"[red]Gateway channel nao encontrado:[/red] {name}")
        raise typer.Exit(code=1)
    console.print(f"[green]gateway channel removido[/green] {name}")


@app.command("gateway-send")
def gateway_send_cmd(
    channel: str = typer.Argument(..., help="Canal registrado ou plataforma direta"),
    message: str = typer.Argument(..., help="Texto a enviar"),
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    target: str = typer.Option("", "--target", help="Destino direto quando channel for plataforma"),
    platform: str = typer.Option("", "--platform", help="Forca plataforma ao usar target direto"),
    metadata_json: str = typer.Option("{}", "--metadata-json", help="Metadados JSON sem segredos; use *_env"),
    deliver_now: bool = typer.Option(False, "--deliver-now", help="Entrega exatamente esta mensagem agora"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Resolve destino sem enfileirar"),
    max_attempts: int = typer.Option(3, "--max-attempts"),
):
    """Enfileira uma mensagem outbound para canal real ou plataforma direta."""
    import json as _json

    from .gateway_adapters import SUPPORTED_GATEWAY_CHANNELS
    from .gateway_channels import GatewayChannelRegistry, validate_gateway_metadata
    from .gateway_outbox import GatewayOutbox

    try:
        metadata = _json.loads(metadata_json)
        if not isinstance(metadata, dict):
            raise ValueError("metadata-json deve ser um objeto JSON")
    except Exception as exc:
        console.print(f"[red]JSON invalido:[/red] {exc}")
        raise typer.Exit(code=2)

    try:
        validate_gateway_metadata(metadata)
    except Exception as exc:
        console.print(f"[red]Metadata invalido:[/red] {exc}")
        raise typer.Exit(code=2)

    registry = GatewayChannelRegistry(workspace)
    configured = registry.get(channel)
    resolved_source = "direct"
    if configured and not platform and not target:
        if not configured.enabled:
            console.print(f"[red]Gateway channel desabilitado:[/red] {configured.name}")
            raise typer.Exit(code=1)
        resolved_channel = configured.platform
        resolved_target = configured.target
        resolved_metadata = dict(configured.metadata)
        resolved_metadata.update(metadata)
        resolved_metadata["gateway_channel"] = configured.name
        resolved_source = f"channel:{configured.name}"
    else:
        resolved_channel = (platform or channel).strip().lower()
        resolved_target = target.strip()
        if resolved_channel not in SUPPORTED_GATEWAY_CHANNELS:
            allowed = ", ".join(sorted(SUPPORTED_GATEWAY_CHANNELS))
            console.print(f"[red]Plataforma invalida:[/red] {resolved_channel}. Use: {allowed}")
            raise typer.Exit(code=2)
        if not resolved_target:
            console.print("[red]--target e obrigatorio para envio direto por plataforma.[/red]")
            raise typer.Exit(code=2)
        resolved_metadata = metadata

    summary = {
        "channel": resolved_channel,
        "target": resolved_target,
        "source": resolved_source,
        "metadata": resolved_metadata,
        "payload": {"type": "operator.message", "text": message},
    }
    if dry_run:
        console.print(_json.dumps(summary, ensure_ascii=False, indent=2), soft_wrap=True)
        return

    try:
        outbox = GatewayOutbox(workspace)
        outbox_message = outbox.enqueue(
            channel=resolved_channel,
            target=resolved_target,
            payload={"type": "operator.message", "text": message},
            max_attempts=max(1, int(max_attempts)),
            metadata=resolved_metadata,
        )
        console.print(
            f"[green]gateway mensagem enfileirada[/green] "
            f"id={outbox_message.message_id} channel={resolved_channel} source={resolved_source}"
        )
        if deliver_now:
            result = outbox.deliver_message(outbox_message.message_id)
            console.print(
                "[bold]gateway deliver-now[/bold] "
                f"delivered={len(result.delivered)} failed={len(result.failed)} skipped={len(result.skipped)}"
            )
            if result.failed:
                raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[red]Erro no gateway-send:[/red] {exc}")
        raise typer.Exit(code=1)


@app.command("gateway-outbox")
def gateway_outbox_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    limit: int = typer.Option(20, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Lista mensagens pendentes/recentes do outbox de entrega."""
    import json as _json
    from dataclasses import asdict

    from .gateway_outbox import GatewayOutbox

    messages = GatewayOutbox(workspace).list_messages(limit=limit)
    if as_json:
        console.print(_json.dumps([asdict(message) for message in messages], ensure_ascii=False, indent=2), soft_wrap=True)
        return
    if not messages:
        console.print("[dim]Nenhuma mensagem no gateway outbox.[/dim]")
        return
    table = Table(title=f"Gateway outbox - {workspace}", show_lines=False)
    table.add_column("Message", style="cyan")
    table.add_column("Channel")
    table.add_column("Status")
    table.add_column("Attempts", justify="right")
    table.add_column("Target")
    for message in messages:
        table.add_row(
            message.message_id,
            message.channel,
            message.status,
            f"{message.attempts}/{message.max_attempts}",
            message.target[:60],
        )
    console.print(table)


@app.command("gateway-deliver")
def gateway_deliver_cmd(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    limit: int = typer.Option(20, "--limit"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    watch: bool = typer.Option(False, "--watch", help="Roda em loop como worker de entrega"),
    interval: int = typer.Option(30, "--interval", help="Segundos entre entregas no modo --watch"),
):
    """Processa mensagens pendentes do gateway outbox."""
    import time as _time

    from .gateway_outbox import GatewayOutbox

    outbox = GatewayOutbox(workspace)

    def _deliver_once() -> None:
        result = outbox.deliver_once(limit=limit, dry_run=dry_run)
        console.print(
            "[bold]gateway deliver[/bold] "
            f"delivered={len(result.delivered)} failed={len(result.failed)} skipped={len(result.skipped)}"
        )
        for label, items in (
            ("delivered", result.delivered),
            ("failed", result.failed),
            ("skipped", result.skipped),
        ):
            if items:
                console.print(f"[dim]{label}:[/dim] {', '.join(items)}")

    if not watch:
        _deliver_once()
        return

    console.print(f"[green]Gateway outbox worker iniciado[/green] workspace={workspace} interval={interval}s")
    try:
        while True:
            _deliver_once()
            _time.sleep(max(1, int(interval)))
    except KeyboardInterrupt:
        console.print("\n[dim]Gateway outbox worker encerrado.[/dim]")


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
    from .migrate import HermesMigrator

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
    from .migrate import OpenClawMigrator

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

    from .migrate import HermesMigrator, OpenClawMigrator

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


# ============================================================================
# bauer kanban-migrate / bauer boards * — Wave 2 (Kanban SQLite backend)
# ============================================================================


@app.command("kanban-migrate")
def kanban_migrate_cmd(
    workspace: Path = typer.Option(
        _WORKSPACE_DIR, "--workspace",
        help="Workspace contendo o TASKS.md de origem",
    ),
    board: str = typer.Option(
        "", "--board", "-b",
        help="Board kanban_db de destino. Vazio = board ativo (default).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Le e mostra o que seria migrado, sem escrever no SQLite",
    ),
):
    """Migra workspace/TASKS.md para o store SQLite kanban_db.

    Idempotente: rodar duas vezes nao duplica tasks. Migra:
      - Tasks com ID, status (TODO/IN_PROGRESS/DONE/etc.), titulo, body
      - Comentarios (cada bullet vira uma linha de task_comments)
      - Metadata (priority, assignee → colunas; resto → event 'legacy_metadata')
      - Parent/child links (campo 'parent:' do markdown → task_links)
      - Timestamps originais preservados em events 'legacy_created_at'

    Depois da migracao, use bauer/workspace_manager_sqlite.WorkspaceManagerSqlite
    para acessar o store — todos os campos round-trippam com a API antiga.
    """
    from .kanban_migration import migrate_tasks_md, read_tasks_md

    tasks_md = workspace / "TASKS.md"
    target = board or None
    if not tasks_md.exists():
        console.print(f"[yellow]TASKS.md nao encontrado em {tasks_md}.[/yellow]")
        raise typer.Exit(code=1)

    if dry_run:
        parsed = read_tasks_md(tasks_md)
        from rich.table import Table
        tbl = Table(
            title=f"Dry-run: {len(parsed)} tasks em {tasks_md}",
            show_header=True,
            header_style="bold cyan",
        )
        tbl.add_column("ID", style="dim", no_wrap=True)
        tbl.add_column("Status", no_wrap=True)
        tbl.add_column("Titulo")
        tbl.add_column("Comentarios", justify="right")
        for t in parsed:
            tbl.add_row(t.id, t.status, t.title[:60], str(len(t.comments)))
        console.print(tbl)
        console.print(f"[dim]Use sem --dry-run para escrever no board "
                       f"'{target or 'default'}'.[/dim]")
        return

    report = migrate_tasks_md(tasks_md, board=target)
    console.print(f"[green]Migracao concluida:[/green] {report.summary()}")
    if report.errors:
        from rich.table import Table
        tbl = Table(title="Erros", show_header=True, header_style="bold red")
        tbl.add_column("ID")
        tbl.add_column("Erro")
        for tid, err in report.errors:
            tbl.add_row(tid, err[:80])
        console.print(tbl)


@boards_app.command("list")
def boards_list_cmd():
    """Lista todos os boards kanban_db existentes em ~/.bauer/kanban/boards/."""
    from . import kanban_db as _kb
    from rich.table import Table

    boards = _kb.list_boards()
    active = _kb.get_active_board()
    if not boards:
        console.print("[dim]Nenhum board encontrado. Crie com [bold]bauer boards create <nome>[/bold].[/dim]")
        return

    tbl = Table(title=f"Kanban boards ({len(boards)})", show_header=True,
                header_style="bold cyan")
    tbl.add_column("Nome", no_wrap=True)
    tbl.add_column("Ativo", justify="center")
    tbl.add_column("Tasks", justify="right")
    tbl.add_column("Path", style="dim")
    for name in boards:
        with _kb.connect(name) as conn:
            try:
                row = conn.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()
                count = row["c"] if row else 0
            except Exception:
                count = 0
        is_active = "★" if name == active else ""
        tbl.add_row(name, is_active, str(count), str(_kb.board_path(name)))
    console.print(tbl)


@boards_app.command("create")
def boards_create_cmd(
    name: str = typer.Argument(..., help="Nome do board (alnum/dash/underscore)"),
    activate: bool = typer.Option(
        False, "--activate", "-a",
        help="Define este board como o ativo apos criar",
    ),
):
    """Cria um novo board kanban_db (DB SQLite vazia)."""
    from . import kanban_db as _kb

    if not name.strip():
        console.print("[red]Nome do board nao pode ser vazio.[/red]")
        raise typer.Exit(code=1)

    if name in _kb.list_boards():
        console.print(f"[yellow]Board '[bold]{name}[/bold]' ja existe.[/yellow]")
        if not activate:
            raise typer.Exit(code=0)

    with _kb.connect(name) as conn:
        _kb.init_db(conn)
    console.print(f"[green]✓[/green] Board criado: [bold]{name}[/bold]")
    console.print(f"  Path: [dim]{_kb.board_path(name)}[/dim]")

    if activate:
        _kb.set_active_board(name)
        console.print(f"[green]✓[/green] Board ativo agora: [bold]{name}[/bold]")


@boards_app.command("switch")
def boards_switch_cmd(
    name: str = typer.Argument(..., help="Nome do board a ativar"),
):
    """Define o board ativo (escreve em ~/.bauer/kanban/active_board)."""
    from . import kanban_db as _kb

    if name not in _kb.list_boards():
        console.print(f"[red]Board '[bold]{name}[/bold]' nao existe.[/red]")
        existing = ", ".join(_kb.list_boards()) or "(nenhum)"
        console.print(f"[dim]Boards disponiveis: {existing}[/dim]")
        raise typer.Exit(code=1)

    _kb.set_active_board(name)
    console.print(f"[green]✓[/green] Board ativo: [bold]{name}[/bold]")


@boards_app.command("show")
def boards_show_cmd(
    name: str = typer.Argument("", help="Nome do board (vazio = ativo)"),
):
    """Mostra estatisticas e tasks de um board."""
    from . import kanban_db as _kb
    from rich.table import Table

    target = name or _kb.get_active_board()
    if target not in _kb.list_boards():
        console.print(f"[red]Board '[bold]{target}[/bold]' nao existe.[/red]")
        raise typer.Exit(code=1)

    with _kb.connect(target) as conn:
        tasks = _kb.list_tasks(conn)
        counts: dict[str, int] = {}
        for t in tasks:
            counts[t.status] = counts.get(t.status, 0) + 1

    console.print(f"[bold]Board:[/bold] {target}")
    console.print(f"[dim]Path:[/dim] {_kb.board_path(target)}")
    console.print()
    if not tasks:
        console.print("[dim]Sem tasks.[/dim]")
        return

    summary = Table(show_header=True, header_style="bold cyan")
    summary.add_column("Status")
    summary.add_column("Tasks", justify="right")
    for status in sorted(counts):
        summary.add_row(status, str(counts[status]))
    console.print(summary)
    console.print()

    tbl = Table(title=f"Tasks ({len(tasks)})", show_header=True,
                header_style="bold")
    tbl.add_column("ID", style="dim", no_wrap=True)
    tbl.add_column("Status", no_wrap=True)
    tbl.add_column("Prioridade", no_wrap=True)
    tbl.add_column("Titulo")
    for t in tasks[:25]:
        tbl.add_row(t.id[:12], t.status, t.priority, t.title[:60])
    console.print(tbl)
    if len(tasks) > 25:
        console.print(f"[dim]... +{len(tasks) - 25} tasks (use boards-show "
                      f"com filtros para ver tudo).[/dim]")


@boards_app.command("rm")
def boards_rm_cmd(
    name: str = typer.Argument(..., help="Nome do board a remover"),
    force: bool = typer.Option(False, "--force", "-f",
                                help="Nao pede confirmacao"),
):
    """Remove um board (apaga o arquivo SQLite). Operacao IRREVERSIVEL."""
    from . import kanban_db as _kb

    if name not in _kb.list_boards():
        console.print(f"[yellow]Board '[bold]{name}[/bold]' nao existe.[/yellow]")
        raise typer.Exit(code=1)

    path = _kb.board_path(name)
    if not force:
        if not typer.confirm(
            f"Remover board '{name}' definitivamente? Path: {path}",
            default=False,
        ):
            console.print("[dim]Cancelado.[/dim]")
            return

    try:
        path.unlink(missing_ok=True)
        # Remove o diretorio do board se estiver vazio (mas mantem workspaces/logs).
        try:
            path.parent.rmdir()
        except OSError:
            pass
    except OSError as exc:
        console.print(f"[red]Erro removendo {path}:[/red] {exc}")
        raise typer.Exit(code=1)

    # Se era o board ativo, reseta o marcador.
    if _kb.get_active_board() == name:
        _kb.active_board_marker_path().unlink(missing_ok=True)
        console.print(f"[yellow]Marcador 'active_board' removido — proximo "
                      f"comando usara 'default'.[/yellow]")
    console.print(f"[green]✓[/green] Board removido: [bold]{name}[/bold]")


# ============================================================================
# bauer kanban-{specify, decompose, swarm} — Wave 3 (LLM kanban features)
# ============================================================================


@app.command("kanban-specify")
def kanban_specify_cmd(
    task_id: str = typer.Argument(..., help="Task ID a especificar"),
    board: str = typer.Option("", "--board", "-b", help="Board (vazio = ativo)"),
    config: Path = typer.Option(Path("config.yaml"), "--config",
                                  help="Path do config.yaml"),
):
    """Promove uma triage task para todo via LLM auxiliar.

    A task deve estar em 'triage'. O modelo configurado em
    auxiliary.triage_specifier (ou o modelo principal se vazio) reescreve
    title + body em formato estruturado (Goal/Approach/Acceptance/Out of
    scope) e transiciona para 'todo'.
    """
    from .config_loader import load_config
    from .kanban_specify import specify_task

    cfg = None
    if config.exists():
        try:
            cfg = load_config(str(config))
        except Exception as exc:
            console.print(f"[yellow]Config nao carregada ({exc}); usando "
                           f"autoload[/yellow]")

    target = board or None
    outcome = specify_task(task_id, board=target, cfg=cfg)
    if outcome.ok:
        if outcome.reason == "not_triage":
            console.print(f"[yellow]Task {task_id} ja foi specifield "
                           f"anteriormente.[/yellow]")
            return
        console.print(f"[green]✓[/green] Task [bold]{outcome.task_id}[/bold] "
                       f"specifield e promovida para todo")
        console.print(f"\n[bold]Title:[/bold] {outcome.title}\n")
        console.print(f"[bold]Body:[/bold]\n{outcome.body}\n")
        return

    console.print(f"[red]Falha:[/red] {outcome.reason}")
    raise typer.Exit(code=1)


@app.command("kanban-decompose")
def kanban_decompose_cmd(
    task_id: str = typer.Argument(..., help="Task ID a decompor"),
    board: str = typer.Option("", "--board", "-b", help="Board (vazio = ativo)"),
    config: Path = typer.Option(Path("config.yaml"), "--config",
                                  help="Path do config.yaml"),
):
    """Decompoe uma task complexa em sub-tasks via LLM.

    A task deve estar em 'triage' ou 'todo'. O modelo auxiliary.kanban_decomposer
    retorna um plano de 2-6 sub-tasks com dependencias declaradas. Cada filho
    e criado em 'todo' e linkado via task_links; o root espera todos os leaves
    completarem antes de virar 'ready' (se ainda estiver pendente).

    Se o modelo julgar a task atomica (fanout=false), reescreve title+body em
    place e promove para 'todo' — equivalente a `kanban-specify`.
    """
    from .config_loader import load_config
    from .kanban_decompose import decompose_task

    cfg = None
    if config.exists():
        try:
            cfg = load_config(str(config))
        except Exception:
            pass

    target = board or None
    outcome = decompose_task(task_id, board=target, cfg=cfg)
    if not outcome.ok:
        console.print(f"[red]Falha:[/red] {outcome.reason}")
        raise typer.Exit(code=1)

    if outcome.fanout:
        from rich.table import Table
        tbl = Table(title=f"Decomposed {outcome.task_id} → "
                          f"{len(outcome.child_ids)} children",
                    show_header=True, header_style="bold cyan")
        tbl.add_column("Child ID", style="dim", no_wrap=True)
        tbl.add_column("Title")
        from . import kanban_db as _kb
        with _kb.connect(target) as conn:
            for cid in outcome.child_ids:
                child = _kb.get_task_or_none(conn, cid)
                tbl.add_row(cid[:12], child.title if child else "?")
        console.print(tbl)
        if outcome.rationale:
            console.print(f"\n[dim]Rationale:[/dim] {outcome.rationale}")
    else:
        console.print(f"[yellow]Decomposer julgou atomica;[/yellow] task "
                       f"[bold]{outcome.task_id}[/bold] reescrita e promovida.")
        if outcome.rationale:
            console.print(f"\n[dim]Rationale:[/dim] {outcome.rationale}")


@app.command("kanban-swarm")
def kanban_swarm_cmd(
    goal: str = typer.Argument(..., help="Objetivo do swarm (titulo do root)"),
    workers: list[str] = typer.Option(
        ..., "--worker", "-w",
        help="Titulo de um worker. Repita a flag para varios workers.",
    ),
    verifier: str = typer.Option("", "--verifier", help="Titulo customizado"),
    synthesizer: str = typer.Option("", "--synthesizer", help="Titulo customizado"),
    board: str = typer.Option("", "--board", "-b", help="Board (vazio = ativo)"),
    priority: str = typer.Option("high", "--priority", "-p",
                                  help="critical | high | medium | low"),
):
    """Cria um swarm de agents: root + N workers paralelos + verifier + synthesizer.

    Workers ficam em 'ready' imediatamente (dispatcher pode rodar paralelo).
    Verifier espera todos os workers completarem; synthesizer espera o
    verifier. Coordenacao entre workers via blackboard (comentarios
    estruturados no root).

    Exemplo:
      bauer kanban-swarm "Implementar OAuth" \\
        -w "Auth API" -w "Login UI" -w "Tests" \\
        --verifier "Verify e2e"
    """
    from .kanban_swarm import create_swarm

    target = board or None
    try:
        result = create_swarm(
            goal,
            workers=list(workers),
            verifier=verifier or None,
            synthesizer=synthesizer or None,
            board=target,
            priority=priority,
        )
    except ValueError as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)

    from rich.table import Table
    tbl = Table(title=f"Swarm criado: {result.goal}", show_header=True,
                header_style="bold cyan")
    tbl.add_column("Role", style="dim", no_wrap=True)
    tbl.add_column("ID", no_wrap=True)
    tbl.add_row("Root (done)", result.root_id[:12])
    for idx, wid in enumerate(result.worker_ids, 1):
        tbl.add_row(f"Worker {idx} (ready)", wid[:12])
    tbl.add_row("Verifier (todo)", result.verifier_id[:12])
    tbl.add_row("Synthesizer (todo)", result.synthesizer_id[:12])
    console.print(tbl)
    console.print(f"\n[dim]Use [bold]bauer dispatch once[/bold] para promover "
                   f"e claimar workers. Inspecione com [bold]bauer kanban-swarm-status "
                   f"{result.root_id[:8]}[/bold].[/dim]")


@app.command("kanban-swarm-status")
def kanban_swarm_status_cmd(
    root_id: str = typer.Argument(..., help="Root ID do swarm"),
    board: str = typer.Option("", "--board", "-b", help="Board (vazio = ativo)"),
):
    """Mostra snapshot atual de um swarm: status dos workers + blackboard."""
    from .kanban_swarm import swarm_summary

    target = board or None
    snap = swarm_summary(root_id, board=target)
    if "error" in snap:
        console.print(f"[red]{snap['error']}[/red]")
        raise typer.Exit(code=1)

    from rich.table import Table
    console.print(f"[bold]Swarm:[/bold] {snap['goal']}")
    console.print(f"[dim]Root:[/dim] {snap['root_id']}\n")

    tbl = Table(show_header=True, header_style="bold cyan")
    tbl.add_column("Role", no_wrap=True)
    tbl.add_column("ID", style="dim", no_wrap=True)
    tbl.add_column("Title")
    tbl.add_column("Status", no_wrap=True)
    for idx, w in enumerate(snap["workers"], 1):
        tbl.add_row(f"Worker {idx}", w.get("id", "?")[:12],
                     w.get("title", "?"), w.get("status", "?"))
    v = snap.get("verifier", {})
    tbl.add_row("Verifier", v.get("id", "?")[:12], v.get("title", "?"),
                 v.get("status", "?"))
    s = snap.get("synthesizer", {})
    tbl.add_row("Synthesizer", s.get("id", "?")[:12], s.get("title", "?"),
                 s.get("status", "?"))
    console.print(tbl)

    bb = snap.get("blackboard", {})
    if bb:
        console.print(f"\n[bold]Blackboard:[/bold]")
        for key, value in bb.items():
            console.print(f"  [cyan]{key}[/cyan]: {value}")
    else:
        console.print(f"\n[dim]Blackboard vazia.[/dim]")


# ---------------------------------------------------------------------------
# Wave 5: kanban-diagnostics + kanban-show (with diagnostics inline)
# ---------------------------------------------------------------------------

_DIAG_SEVERITY_COLOR = {
    "critical": "bold red",
    "error": "red",
    "warning": "yellow",
    "info": "dim",
}


def _render_diagnostics(diags, *, header: bool = True) -> None:
    """Print diagnostics to the console (Rich-formatted)."""
    from rich.table import Table
    if not diags:
        if header:
            console.print("[green]✓ Nenhum diagnóstico ativo.[/green]")
        return
    if header:
        console.print(f"\n[bold]Diagnósticos:[/bold] {len(diags)} iss{'ue' if len(diags)==1 else 'ues'}")
    for d in diags:
        color = _DIAG_SEVERITY_COLOR.get(d.severity, "white")
        console.print(
            f"  [{color}][{d.severity.upper()}][/{color}] "
            f"[bold]{d.rule}[/bold] — {d.message}"
        )


@app.command("kanban-show")
def kanban_show_cmd(
    task_id: str = typer.Argument(..., help="ID da task (prefixo aceito)"),
    board: str = typer.Option("", "--board", "-b", help="Board (vazio = ativo)"),
    no_diag: bool = typer.Option(False, "--no-diag", help="Omitir diagnósticos"),
):
    """Exibe detalhes de uma task + diagnósticos inline."""
    import bauer.kanban_db as kb
    from .kanban_diagnostics import compute_task_diagnostics

    target = board or None
    with kb.connect(board=target) as conn:
        kb.init_db(conn)
        task = kb.get_task_or_none(conn, task_id)
        if task is None:
            # Try prefix match
            all_tasks = kb.list_tasks(conn)
            matches = [t for t in all_tasks if t.id.startswith(task_id)]
            if len(matches) == 1:
                task = matches[0]
            elif len(matches) > 1:
                console.print(f"[yellow]Prefixo ambíguo: {[t.id for t in matches]}[/yellow]")
                raise typer.Exit(code=1)
            else:
                console.print(f"[red]Task não encontrada: {task_id!r}[/red]")
                raise typer.Exit(code=1)

        events = kb.list_events(conn, task.id)
        runs = kb.list_runs(conn, task.id)
        all_ids = frozenset(t.id for t in kb.list_tasks(conn))

    # Render task details
    console.print(f"\n[bold cyan]{task.title}[/bold cyan]  [dim]{task.id}[/dim]")
    console.print(f"  Status   : [bold]{task.status}[/bold]")
    console.print(f"  Assignee : {task.assignee or '—'}")
    console.print(f"  Priority : {task.priority}")
    if task.consecutive_failures:
        console.print(f"  Failures : [red]{task.consecutive_failures}[/red]")
    if task.body:
        console.print(f"\n[dim]{task.body[:500]}{'…' if len(task.body) > 500 else ''}[/dim]")

    if runs:
        console.print(f"\n  [bold]Runs ({len(runs)}):[/bold]")
        for r in runs[-3:]:
            outcome = r.get("outcome") or "?"
            color = "green" if outcome == "success" else "red" if outcome in ("error", "crash") else "dim"
            console.print(f"    [{color}]{outcome}[/{color}]  {(r.get('summary') or '')[:80]}")

    if not no_diag:
        diags = compute_task_diagnostics(task, events, runs, all_task_ids=all_ids)
        _render_diagnostics(diags)


@app.command("kanban-diagnostics")
def kanban_diagnostics_cmd(
    board: str = typer.Option("", "--board", "-b", help="Board (vazio = ativo)"),
    severity: str = typer.Option(
        "", "--severity", "-s",
        help="Filtrar por severity: info, warning, error, critical",
    ),
    task_id: str = typer.Option("", "--task", "-t", help="Diagnóstico de task específica"),
):
    """Mostra diagnósticos ativos em todo o board (ou task específica).

    Exit 0 se nenhum diagnóstico. Exit 1 se há erros/críticos.
    """
    import bauer.kanban_db as kb
    from .kanban_diagnostics import compute_board_diagnostics, compute_task_diagnostics

    target = board or None
    with kb.connect(board=target) as conn:
        kb.init_db(conn)
        tasks = kb.list_tasks(conn)
        all_ids = frozenset(t.id for t in tasks)

        if task_id:
            task = kb.get_task_or_none(conn, task_id)
            if task is None:
                console.print(f"[red]Task não encontrada: {task_id!r}[/red]")
                raise typer.Exit(code=1)
            events = kb.list_events(conn, task.id)
            runs = kb.list_runs(conn, task.id)
            diags = compute_task_diagnostics(task, events, runs, all_task_ids=all_ids)
        else:
            events_by_task = {
                t.id: kb.list_events(conn, t.id) for t in tasks
            }
            runs_by_task = {
                t.id: kb.list_runs(conn, t.id) for t in tasks
            }
            diags = compute_board_diagnostics(
                tasks,
                events_by_task=events_by_task,
                runs_by_task=runs_by_task,
            )

    # Filter by severity if requested
    if severity:
        diags = [d for d in diags if d.severity == severity]

    if not diags:
        console.print("[green]✓ Sem diagnósticos ativos.[/green]")
        raise typer.Exit(code=0)

    # Group by task for readability
    by_task: dict[str, list] = {}
    for d in diags:
        by_task.setdefault(d.task_id, []).append(d)

    for tid, task_diags in by_task.items():
        console.print(f"\n[bold]{tid}[/bold]")
        _render_diagnostics(task_diags, header=False)

    has_errors = any(d.severity in {"error", "critical"} for d in diags)
    raise typer.Exit(code=1 if has_errors else 0)


# ---------------------------------------------------------------------------
# bauer skill — install / list / show / remove / render
# ---------------------------------------------------------------------------

@app.command("skill-install")
def skill_install_cmd(
    source: str = typer.Argument(..., help="Path to YAML file, directory, or URL"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if already installed"),
) -> None:
    """Install a skill from a YAML file, directory, or URL.

    Examples:
        bauer skill-install ./my_skill.yaml
        bauer skill-install ./skills/        # installs all *.yaml files
        bauer skill-install https://example.com/skill.yaml
    """
    from .skill_system import get_default_manager, SkillError

    manager = get_default_manager()
    p = Path(source)

    try:
        if source.startswith("http://") or source.startswith("https://"):
            skill = manager.install_from_url(source, force=force)
            console.print(f"[green]✓ Installed:[/green] {skill.summary()}")
        elif p.is_dir():
            skills = manager.install_from_directory(p, force=force)
            if skills:
                for s in skills:
                    console.print(f"[green]✓[/green] {s.summary()}")
                console.print(f"\n[bold]{len(skills)} skill(s) installed.[/bold]")
            else:
                console.print("[yellow]No new skills found (already installed or no *.yaml files).[/yellow]")
        else:
            skill = manager.install_from_file(p, force=force)
            console.print(f"[green]✓ Installed:[/green] {skill.summary()}")
    except SkillError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)


@app.command("skill-list")
def skill_list_cmd(
    tags: str = typer.Option("", "--tags", help="Filter by comma-separated tags"),
    query: str = typer.Option("", "--query", "-q", help="Filter by name/description substring"),
) -> None:
    """List all installed skills."""
    from .skill_system import get_default_manager

    manager = get_default_manager()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    skills = manager.list_skills(tags=tag_list, query=query or None)

    if not skills:
        console.print("[yellow]No skills installed.[/yellow]")
        console.print("Install with: [bold]bauer skill-install <file.yaml>[/bold]")
        return

    from rich.table import Table
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Name", style="bold")
    table.add_column("Version")
    table.add_column("Tags")
    table.add_column("Description")
    for s in sorted(skills, key=lambda x: x.name):
        table.add_row(
            s.name,
            s.version,
            ", ".join(s.tags) if s.tags else "",
            s.description,
        )
    console.print(table)
    console.print(f"\n{len(skills)} skill(s) installed.")


@app.command("skill-show")
def skill_show_cmd(
    name: str = typer.Argument(..., help="Skill name"),
) -> None:
    """Show full details of an installed skill."""
    from .skill_system import get_default_manager, SkillNotFound

    manager = get_default_manager()
    try:
        skill = manager.get(name)
    except SkillNotFound as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(1)

    console.print(f"\n[bold cyan]{skill.name}[/bold cyan] v{skill.version}")
    console.print(f"[dim]{skill.description}[/dim]")
    if skill.author:
        console.print(f"  Author  : {skill.author}")
    if skill.tags:
        console.print(f"  Tags    : {', '.join(skill.tags)}")
    if skill.tools:
        console.print(f"  Tools   : {', '.join(skill.tools)}")
    if skill.model:
        console.print(f"  Model   : {skill.model}")
    if skill.source:
        console.print(f"  Source  : {skill.source}")
    if skill.params:
        console.print("\n[bold]Parameters:[/bold]")
        for pname, pdef in skill.params.items():
            req = "[red]*[/red]" if pdef.required else ""
            default = f" (default: {pdef.default})" if pdef.default else ""
            console.print(f"  {pname}{req}: {pdef.description}{default}")
    console.print("\n[bold]Invoke template:[/bold]")
    from rich.syntax import Syntax
    console.print(Syntax(skill.invoke, "markdown", theme="monokai", word_wrap=True))


@app.command("skill-remove")
def skill_remove_cmd(
    name: str = typer.Argument(..., help="Skill name to remove"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Remove an installed skill."""
    from .skill_system import get_default_manager

    manager = get_default_manager()
    if not manager.exists(name):
        console.print(f"[yellow]Skill '{name}' is not installed.[/yellow]")
        raise typer.Exit(1)

    if not yes:
        confirmed = typer.confirm(f"Remove skill '{name}'?", default=False)
        if not confirmed:
            console.print("Cancelled.")
            return

    removed = manager.remove(name)
    if removed:
        console.print(f"[green]✓ Removed:[/green] {name}")
    else:
        console.print(f"[red]Failed to remove '{name}'.[/red]")
        raise typer.Exit(1)


@app.command("skill-render")
def skill_render_cmd(
    name: str = typer.Argument(..., help="Skill name"),
    params: list[str] = typer.Option(
        [], "--param", "-p",
        help="Parameter in key=value format. Can be repeated.",
    ),
) -> None:
    """Render a skill's invoke template with provided parameter values.

    Example:
        bauer skill-render summarise_code --param path=bauer/agent.py
    """
    from .skill_system import get_default_manager, SkillNotFound, SkillError

    manager = get_default_manager()
    try:
        skill = manager.get(name)
    except SkillNotFound as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(1)

    values: dict[str, str] = {}
    for p in params:
        if "=" in p:
            k, _, v = p.partition("=")
            values[k.strip()] = v.strip()
        else:
            console.print(f"[yellow]Warning: param '{p}' has no '=', ignoring.[/yellow]")

    try:
        rendered = skill.render(values)
    except SkillError as exc:
        console.print(f"[red]Render error:[/red] {exc}")
        raise typer.Exit(1)

    console.print(rendered)


# ---------------------------------------------------------------------------
# Skills Hub — catálogo built-in de skills por categoria
# ---------------------------------------------------------------------------

skills_hub_app = typer.Typer(help="Skills Hub — catálogo built-in de skills curadas.")
app.add_typer(skills_hub_app, name="skills-hub")

skills_bundle_app = typer.Typer(help="Skill bundles — grupos de skills sob um único nome.")
app.add_typer(skills_bundle_app, name="skills-bundle")


@skills_hub_app.command("list")
def hub_list_cmd(
    category: str | None = typer.Option(None, "--category", "-c", help="Filtrar por categoria"),
) -> None:
    """Lista todas as skills disponíveis no hub built-in."""
    from .skills_hub import get_default_hub
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
    from .skills_hub import get_default_hub
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
    from .skills_hub import get_default_hub

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
    from .skills_hub import get_default_hub

    hub = get_default_hub()
    content = hub.read_content(slug)
    if content is None:
        console.print(f"[red]Skill '{slug}' não encontrada.[/red]")
        raise typer.Exit(1)
    console.print(content)


@skills_hub_app.command("categories")
def hub_categories_cmd() -> None:
    """Lista as categorias de skills disponíveis."""
    from .skills_hub import get_default_hub

    hub = get_default_hub()
    cats = hub.categories()
    if not cats:
        console.print("[yellow]Nenhuma categoria encontrada.[/yellow]")
        raise typer.Exit()
    for c in cats:
        console.print(f"  [cyan]{c}[/cyan]")


# ---------------------------------------------------------------------------
# Skill bundles
# ---------------------------------------------------------------------------

@skills_bundle_app.command("list")
def bundle_list_cmd() -> None:
    """Lista bundles salvos em ~/.bauer/skill-bundles/."""
    from .skill_bundles import get_default_bundle_manager
    from rich.table import Table

    mgr = get_default_bundle_manager()
    bundles = mgr.list_bundles()
    if not bundles:
        console.print("[yellow]Nenhum bundle criado. Use 'bauer skills-bundle new'.[/yellow]")
        raise typer.Exit()

    table = Table(title="Skill Bundles", show_lines=False, box=None)
    table.add_column("Slug", style="cyan", no_wrap=True)
    table.add_column("Nome", style="bold")
    table.add_column("Skills")
    table.add_column("Descrição")
    for b in bundles:
        table.add_row(b.slug, b.name, ", ".join(b.skills), b.description)
    console.print(table)


@skills_bundle_app.command("new")
def bundle_new_cmd(
    name: str = typer.Argument(..., help="Nome do bundle"),
    skills: list[str] = typer.Option([], "--skill", "-s", help="Skill slug (repetível)"),
    description: str = typer.Option("", "--desc", "-d", help="Descrição do bundle"),
    instruction: str = typer.Option("", "--instruction", "-i", help="Instrução extra"),
) -> None:
    """Cria um novo skill bundle."""
    from .skill_bundles import SkillBundle, get_default_bundle_manager

    if not skills:
        console.print("[red]Informe ao menos uma skill com --skill <slug>[/red]")
        raise typer.Exit(1)

    mgr = get_default_bundle_manager()
    bundle = SkillBundle(
        name=name,
        description=description,
        skills=list(skills),
        instruction=instruction,
    )
    path = mgr.save(bundle)
    console.print(f"[green]✓[/green] Bundle '{bundle.slug}' salvo em {path}")
    console.print(f"  Skills: {', '.join(skills)}")


@skills_bundle_app.command("delete")
def bundle_delete_cmd(
    name: str = typer.Argument(..., help="Nome ou slug do bundle"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirmar sem perguntar"),
) -> None:
    """Remove um skill bundle."""
    from .skill_bundles import get_default_bundle_manager

    mgr = get_default_bundle_manager()
    if not yes:
        typer.confirm(f"Remover bundle '{name}'?", abort=True)
    ok = mgr.delete(name)
    if ok:
        console.print(f"[green]✓[/green] Bundle '{name}' removido.")
    else:
        console.print(f"[red]Bundle '{name}' não encontrado.[/red]")
        raise typer.Exit(1)


@skills_bundle_app.command("show")
def bundle_show_cmd(
    name: str = typer.Argument(..., help="Nome ou slug do bundle"),
) -> None:
    """Mostra as skills de um bundle e seu conteúdo combinado."""
    from .skill_bundles import get_default_bundle_manager

    mgr = get_default_bundle_manager()
    bundle = mgr.get(name)
    if bundle is None:
        console.print(f"[red]Bundle '{name}' não encontrado.[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]{bundle.name}[/bold]  [dim]{bundle.slug}[/dim]")
    if bundle.description:
        console.print(f"[dim]{bundle.description}[/dim]")
    console.print(f"Skills: [cyan]{', '.join(bundle.skills)}[/cyan]")
    if bundle.instruction:
        console.print(f"\nInstrução:\n{bundle.instruction}")


# --- Bauer Gateway: telegram / discord / gateway -----------------------------

def _gateway_pid_file(workspace: Path) -> Path:
    return workspace / ".bauer_gateway" / "gateway.pid"


@telegram_app.command("start", help="Inicia o canal Telegram (foreground)")
def telegram_start(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    """Sobe só o bridge Telegram. Para todos os canais use `bauer gateway start`."""
    from bauer.telegram_bridge import run_bridge

    console.print("[green]Telegram bridge — foreground (Ctrl+C para parar)[/green]")
    try:
        run_bridge(config)
    except RuntimeError as exc:
        console.print(f"[red]ERRO:[/red] {exc}")
        raise typer.Exit(code=1)


def _kill_bridge_processes(*needles: str) -> int:
    """Mata processos cujo cmdline contém qualquer needle (exceto o atual).

    Necessário porque versões antigas iniciavam o bridge em background sem
    PID file — o processo órfão continua consumindo o getUpdates do bot
    (Telegram 409) e respondendo com o código antigo.
    """
    import os

    import psutil

    killed = 0
    me = os.getpid()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if proc.pid == me:
                continue
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()
            if any(n in cmdline for n in needles):
                proc.terminate()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


@telegram_app.command("stop", help="Para bridges Telegram em execução (inclusive antigos)")
def telegram_stop():
    killed = _kill_bridge_processes("telegram_bridge")
    if killed:
        console.print(f"[green]✓ {killed} processo(s) de bridge encerrado(s).[/green]")
    else:
        console.print("[yellow]Nenhum bridge Telegram em execução.[/yellow]")


@gateway_app.command("stop", help="Para o Bauer Gateway em execução")
def gateway_stop(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    from bauer.config_loader import load_config as _load_cfg

    try:
        workspace = Path(_load_cfg(config).agent.workspace)
    except Exception:
        workspace = Path("workspace")
    pid_file = _gateway_pid_file(workspace)
    killed = 0
    if pid_file.exists():
        try:
            import psutil
            pid = int(pid_file.read_text().strip())
            psutil.Process(pid).terminate()
            killed += 1
        except Exception:
            pass
        pid_file.unlink(missing_ok=True)
    killed += _kill_bridge_processes("gateway_runtime", "telegram_bridge", "discord_bridge")
    if killed:
        console.print(f"[green]✓ Gateway parado ({killed} processo(s)).[/green]")
    else:
        console.print("[yellow]Nenhum gateway em execução.[/yellow]")


@telegram_app.command("test", help="Valida o token do bot (getMe)")
def telegram_test(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    from bauer.channel_base import resolve_token
    from bauer.config_loader import load_config as _load_cfg

    cfg = _load_cfg(config)
    token = resolve_token(cfg.telegram.bot_token, "TELEGRAM_BOT_TOKEN")
    if not token:
        console.print("[red]Token ausente.[/red] Defina TELEGRAM_BOT_TOKEN no .env "
                      "ou rode `bauer gateway init`.")
        raise typer.Exit(code=1)
    import httpx
    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        bot = r.json().get("result", {})
        if r.json().get("ok"):
            console.print(f"[green]✓ Bot @{bot.get('username')} conectado.[/green]")
        else:
            console.print(f"[red]Token inválido:[/red] {r.json().get('description')}")
            raise typer.Exit(code=1)
    except httpx.HTTPError as exc:
        console.print(f"[red]Erro de rede:[/red] {exc}")
        raise typer.Exit(code=1)


@discord_app.command("start", help="Inicia o canal Discord (foreground)")
def discord_start(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    """Sobe só o bridge Discord. Para todos os canais use `bauer gateway start`."""
    from bauer.discord_bridge import run_bridge

    console.print("[green]Discord bridge — foreground (Ctrl+C para parar)[/green]")
    try:
        run_bridge(config)
    except RuntimeError as exc:
        console.print(f"[red]ERRO:[/red] {exc}")
        raise typer.Exit(code=1)


@gateway_app.command("start", help="Inicia todos os canais habilitados + outbox pump")
def gateway_start(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    """Bauer Gateway: canais do config.yaml (telegram/discord) + entrega do outbox."""
    import logging
    import os

    from bauer.gateway_runtime import BauerGatewayRuntime

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    runtime = BauerGatewayRuntime.from_config(config)
    if not runtime.bridges:
        console.print(
            "[yellow]Nenhum canal habilitado.[/yellow] Habilite telegram/discord no "
            "config.yaml ou rode [bold]bauer gateway init[/bold]."
        )
    names = ", ".join(b.name for b in runtime.bridges) or "nenhum canal"
    console.print(f"[green]Bauer Gateway no ar[/green] — {names} + outbox pump. Ctrl+C para parar.")
    pid_file = _gateway_pid_file(Path(runtime.workspace))
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    try:
        runtime.start(block=True)
    finally:
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass


@gateway_app.command("status", help="Status dos canais e do outbox")
def gateway_status(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    from bauer.channel_base import resolve_token
    from bauer.config_loader import load_config as _load_cfg
    from bauer.gateway_outbox import GatewayOutbox

    cfg = _load_cfg(config)
    table = Table(title="Bauer Gateway", show_lines=False)
    table.add_column("Canal")
    table.add_column("Habilitado")
    table.add_column("Token")
    table.add_column("Allowlist")

    tg_token = resolve_token(cfg.telegram.bot_token, "TELEGRAM_BOT_TOKEN")
    dc_token = resolve_token(cfg.discord.bot_token, "DISCORD_BOT_TOKEN")
    table.add_row(
        "telegram",
        "[green]sim[/green]" if cfg.telegram.enabled else "[dim]não[/dim]",
        "[green]ok[/green]" if tg_token else "[red]ausente[/red]",
        f"{len(cfg.telegram.allowed_users)} usuários"
        + (" [yellow](allow_all!)[/yellow]" if cfg.telegram.allow_all else ""),
    )
    table.add_row(
        "discord",
        "[green]sim[/green]" if cfg.discord.enabled else "[dim]não[/dim]",
        "[green]ok[/green]" if dc_token else "[red]ausente[/red]",
        f"{len(cfg.discord.allowed_users)} usuários"
        + (" [yellow](allow_all!)[/yellow]" if cfg.discord.allow_all else ""),
    )
    console.print(table)

    workspace = Path(cfg.agent.workspace)
    try:
        pending = len(GatewayOutbox(workspace).pending(limit=100))
        console.print(f"Outbox: [bold]{pending}[/bold] mensagem(ns) pendente(s)")
    except Exception:
        console.print("Outbox: [dim]vazio/indisponível[/dim]")

    # Estado real do processo (PID vivo? uptime? memória?) — não só PID file
    from bauer.gateway_service import format_uptime, read_process_status
    pid, uptime, mem = read_process_status(Path.cwd())
    if pid is not None:
        console.print(
            f"Runtime: [green]ativo[/green] — PID {pid}, "
            f"uptime {format_uptime(uptime or 0)}, {mem:.0f}MB"
        )
    else:
        console.print(
            "Runtime: [dim]parado[/dim] — `bauer gateway start` (foreground) "
            "ou `bauer gateway service install` (serviço)"
        )


# ── bauer gateway service — serviço do sistema (paridade hermes-gateway) ─────


def _service_manager():
    from bauer.gateway_service import GatewayServiceManager
    return GatewayServiceManager(project_dir=Path.cwd())


@gateway_service_app.command("install", help="Instala E inicia o serviço (systemd/Task Scheduler)")
def gateway_service_install():
    try:
        msg = _service_manager().install()
        console.print(f"[green]✓[/green] {msg}")
        console.print("Acompanhe: [bold]bauer gateway service status[/bold] | logs: [bold]bauer gateway service logs[/bold]")
    except RuntimeError as exc:
        console.print(f"[red]ERRO:[/red] {exc}")
        raise typer.Exit(code=1)


@gateway_service_app.command("uninstall", help="Para e remove o serviço")
def gateway_service_uninstall():
    try:
        console.print(f"[green]✓[/green] {_service_manager().uninstall()}")
    except RuntimeError as exc:
        console.print(f"[red]ERRO:[/red] {exc}")
        raise typer.Exit(code=1)


@gateway_service_app.command("start", help="Inicia o serviço instalado")
def gateway_service_start():
    try:
        console.print(f"[green]✓[/green] {_service_manager().start()}")
    except RuntimeError as exc:
        console.print(f"[red]ERRO:[/red] {exc}")
        raise typer.Exit(code=1)


@gateway_service_app.command("stop", help="Para o serviço (a tarefa/unit continua instalada)")
def gateway_service_stop():
    try:
        console.print(f"[green]✓[/green] {_service_manager().stop()}")
    except RuntimeError as exc:
        console.print(f"[red]ERRO:[/red] {exc}")
        raise typer.Exit(code=1)


@gateway_service_app.command("status", help="Estado do serviço: instalado, ativo, PID, uptime, memória")
def gateway_service_status():
    from bauer.gateway_service import SERVICE_NAME, TASK_NAME, format_uptime

    mgr = _service_manager()
    st = mgr.status()
    name = SERVICE_NAME if st.platform == "systemd" else TASK_NAME
    table = Table(title=f"Bauer Gateway Service — {name}", show_lines=False)
    table.add_column("Campo")
    table.add_column("Valor")
    table.add_row("Plataforma", st.platform)
    table.add_row("Instalado", "[green]sim[/green]" if st.installed else "[red]não[/red]")
    table.add_row(
        "Inicia no boot/logon",
        "[green]sim[/green]" if st.enabled else "[dim]não[/dim]",
    )
    table.add_row(
        "Em execução",
        "[green]sim[/green]" if st.running else "[red]não[/red]",
    )
    if st.pid is not None:
        table.add_row("PID", str(st.pid))
        table.add_row("Uptime", format_uptime(st.uptime_s or 0))
        table.add_row("Memória", f"{st.memory_mb:.0f} MB" if st.memory_mb else "—")
    if st.detail:
        table.add_row("Obs", st.detail)
    console.print(table)
    if not st.installed:
        console.print("Instale com: [bold]bauer gateway service install[/bold]")


@gateway_service_app.command("logs", help="Últimas linhas de log do gateway")
def gateway_service_logs(
    lines: int = typer.Option(50, "--lines", "-n", help="Quantidade de linhas"),
):
    console.print(_service_manager().logs(lines=lines))


# ── bauer daemon service — serviço do sistema ────────────────────────────────


def _daemon_service_cfg(
    board: str = "default",
    workers: int = 2,
    budget_usd: float = 5.0,
    budget_hours: float = 24.0,
) -> "ProcessServiceConfig":
    from bauer.process_service import ProcessServiceConfig, pid_reader_from_file
    return ProcessServiceConfig(
        service_name="bauer-daemon",
        task_name="BauerDaemon",
        description="Bauer Daemon — pool de workers autônomos (kanban tasks)",
        entry_args=[
            "daemon", "_run",
            "--board", board,
            "--workers", str(workers),
            "--budget-usd", str(budget_usd),
            "--budget-hours", str(budget_hours),
        ],
        log_file=Path("logs") / "daemon.log",
        pid_reader=pid_reader_from_file(
            lambda _: _daemon_pid_path(),
            keyword="daemon",
        ),
        cmdline_keyword="daemon",
    )


def _daemon_svc_manager(
    board: str = "default",
    workers: int = 2,
    budget_usd: float = 5.0,
    budget_hours: float = 24.0,
) -> "ProcessServiceManager":
    from bauer.process_service import ProcessServiceManager
    return ProcessServiceManager(_daemon_service_cfg(board, workers, budget_usd, budget_hours))


@daemon_service_app.command("install", help="Instala E inicia o daemon como serviço do sistema")
def daemon_service_install(
    board: str = typer.Option("default", "--board", "-b"),
    workers: int = typer.Option(2, "--workers", "-w"),
    budget_usd: float = typer.Option(5.0, "--budget-usd"),
    budget_hours: float = typer.Option(24.0, "--budget-hours"),
):
    try:
        msg = _daemon_svc_manager(board, workers, budget_usd, budget_hours).install()
        console.print(f"[green]✓[/green] {msg}")
        console.print("Acompanhe: [bold]bauer daemon service status[/bold] | [bold]bauer daemon service logs[/bold]")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@daemon_service_app.command("uninstall", help="Para e remove o serviço do daemon")
def daemon_service_uninstall():
    try:
        console.print(f"[green]✓[/green] {_daemon_svc_manager().uninstall()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@daemon_service_app.command("start", help="Inicia o serviço instalado do daemon")
def daemon_service_start():
    try:
        console.print(f"[green]✓[/green] {_daemon_svc_manager().start()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@daemon_service_app.command("stop", help="Para o serviço do daemon (mantém instalado)")
def daemon_service_stop():
    try:
        console.print(f"[green]✓[/green] {_daemon_svc_manager().stop()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@daemon_service_app.command("status", help="Estado do serviço: instalado, ativo, PID, uptime")
def daemon_service_status():
    from bauer.process_service import format_uptime
    st = _daemon_svc_manager().status()
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="dim", width=14)
    table.add_column()
    table.add_row("Plataforma", st.platform)
    table.add_row("Instalado", "[green]sim[/green]" if st.installed else "[red]não[/red]")
    table.add_row("Ativo", "[green]sim[/green]" if st.running else "[red]não[/red]")
    if st.pid:
        table.add_row("PID", str(st.pid))
    if st.uptime_s is not None:
        table.add_row("Uptime", format_uptime(st.uptime_s))
    if st.memory_mb is not None:
        table.add_row("Memória", f"{st.memory_mb:.0f} MB")
    if st.detail:
        table.add_row("Obs", st.detail)
    console.print(Panel(table, title="bauer-daemon", border_style="cyan"))
    if not st.installed:
        console.print("Instale com: [bold]bauer daemon service install[/bold]")


@daemon_service_app.command("logs", help="Últimas linhas de log do daemon")
def daemon_service_logs(
    lines: int = typer.Option(50, "--lines", "-n"),
):
    console.print(_daemon_svc_manager().logs(lines=lines))


# ── bauer runtime service — serviço do sistema ───────────────────────────────


def _runtime_service_cfg(workspace: "Path | None" = None) -> "ProcessServiceConfig":
    from bauer.process_service import ProcessServiceConfig, pid_reader_from_supervisor_json
    ws = workspace or _PROJECT_WORKSPACE

    def _ws_fn(project_dir: "Path") -> "Path":
        p = Path(ws)
        return p if p.is_absolute() else project_dir / p

    entry_args = ["runtime", "supervise", "--workspace", str(ws)]
    return ProcessServiceConfig(
        service_name="bauer-runtime",
        task_name="BauerRuntime",
        description="Bauer Runtime — supervisor always-on (dispatcher, cron, outbox)",
        entry_args=entry_args,
        log_file=Path(ws) / ".bauer_runtime" / "logs" / "supervisor.log",
        pid_reader=pid_reader_from_supervisor_json(_ws_fn),
        cmdline_keyword="supervise",
    )


def _runtime_svc_manager(workspace: "Path | None" = None) -> "ProcessServiceManager":
    from bauer.process_service import ProcessServiceManager
    return ProcessServiceManager(_runtime_service_cfg(workspace))


@runtime_service_app.command("install", help="Instala E inicia o runtime como serviço do sistema")
def runtime_service_install(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    try:
        msg = _runtime_svc_manager(workspace).install()
        console.print(f"[green]✓[/green] {msg}")
        console.print("Acompanhe: [bold]bauer runtime service status[/bold] | [bold]bauer runtime service logs[/bold]")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@runtime_service_app.command("uninstall", help="Para e remove o serviço do runtime")
def runtime_service_uninstall(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    try:
        console.print(f"[green]✓[/green] {_runtime_svc_manager(workspace).uninstall()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@runtime_service_app.command("start", help="Inicia o serviço instalado do runtime")
def runtime_service_start(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    try:
        console.print(f"[green]✓[/green] {_runtime_svc_manager(workspace).start()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@runtime_service_app.command("stop", help="Para o serviço do runtime (mantém instalado)")
def runtime_service_stop(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    try:
        console.print(f"[green]✓[/green] {_runtime_svc_manager(workspace).stop()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@runtime_service_app.command("status", help="Estado do serviço: instalado, ativo, PID, uptime")
def runtime_service_status(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
):
    from bauer.process_service import format_uptime
    st = _runtime_svc_manager(workspace).status()
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="dim", width=14)
    table.add_column()
    table.add_row("Plataforma", st.platform)
    table.add_row("Instalado", "[green]sim[/green]" if st.installed else "[red]não[/red]")
    table.add_row("Ativo", "[green]sim[/green]" if st.running else "[red]não[/red]")
    if st.pid:
        table.add_row("PID", str(st.pid))
    if st.uptime_s is not None:
        table.add_row("Uptime", format_uptime(st.uptime_s))
    if st.memory_mb is not None:
        table.add_row("Memória", f"{st.memory_mb:.0f} MB")
    if st.detail:
        table.add_row("Obs", st.detail)
    console.print(Panel(table, title="bauer-runtime", border_style="blue"))
    if not st.installed:
        console.print("Instale com: [bold]bauer runtime service install[/bold]")


@runtime_service_app.command("logs", help="Últimas linhas de log do runtime supervisor")
def runtime_service_logs(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    lines: int = typer.Option(50, "--lines", "-n"),
):
    console.print(_runtime_svc_manager(workspace).logs(lines=lines))


# ── bauer serve service — serviço do sistema ─────────────────────────────────


def _serve_pid_path(project_dir: "Path") -> "Path":
    """PID file do servidor HTTP — workspace/.bauer_serve/serve.pid."""
    ws: "Path" = Path("workspace")
    try:
        from .config_loader import load_config
        cfg = load_config(project_dir / "config.yaml")
        loaded = Path(cfg.agent.workspace)
        ws = loaded if loaded.is_absolute() else project_dir / loaded
    except Exception:  # noqa: BLE001
        pass
    return ws / ".bauer_serve" / "serve.pid"


def _serve_service_cfg() -> "ProcessServiceConfig":
    from bauer.process_service import ProcessServiceConfig, pid_reader_from_file
    return ProcessServiceConfig(
        service_name="bauer-serve",
        task_name="BauerServe",
        description="Bauer Agent HTTP Server — REST API e interface web",
        entry_args=["serve"],
        log_file=Path("logs") / "serve.log",
        pid_reader=pid_reader_from_file(_serve_pid_path, keyword="server"),
        cmdline_keyword="serve",
    )


def _serve_svc_manager() -> "ProcessServiceManager":
    from bauer.process_service import ProcessServiceManager
    return ProcessServiceManager(_serve_service_cfg())


@serve_service_app.command("install", help="Instala E inicia o servidor HTTP como serviço do sistema")
def serve_service_install():
    try:
        msg = _serve_svc_manager().install()
        console.print(f"[green]✓[/green] {msg}")
        console.print(
            "Acesse: [bold]http://localhost:8000[/bold]  |  "
            "Acompanhe: [bold]bauer serve service status[/bold]"
        )
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@serve_service_app.command("uninstall", help="Para e remove o serviço do servidor HTTP")
def serve_service_uninstall():
    try:
        console.print(f"[green]✓[/green] {_serve_svc_manager().uninstall()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@serve_service_app.command("start", help="Inicia o serviço instalado do servidor HTTP")
def serve_service_start():
    try:
        console.print(f"[green]✓[/green] {_serve_svc_manager().start()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@serve_service_app.command("stop", help="Para o servidor HTTP (mantém instalado)")
def serve_service_stop():
    try:
        console.print(f"[green]✓[/green] {_serve_svc_manager().stop()}")
    except Exception as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)


@serve_service_app.command("status", help="Estado do serviço: instalado, ativo, PID, uptime")
def serve_service_status():
    from bauer.process_service import format_uptime
    st = _serve_svc_manager().status()
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style="dim", width=14)
    table.add_column()
    table.add_row("Plataforma", st.platform)
    table.add_row("Instalado", "[green]sim[/green]" if st.installed else "[red]não[/red]")
    table.add_row("Ativo", "[green]sim[/green]" if st.running else "[red]não[/red]")
    if st.pid:
        table.add_row("PID", str(st.pid))
    if st.uptime_s is not None:
        table.add_row("Uptime", format_uptime(st.uptime_s))
    if st.memory_mb is not None:
        table.add_row("Memória", f"{st.memory_mb:.0f} MB")
    if st.detail:
        table.add_row("Obs", st.detail)
    console.print(Panel(table, title="bauer-serve", border_style="yellow"))
    if not st.installed:
        console.print("Instale com: [bold]bauer serve service install[/bold]")


@serve_service_app.command("logs", help="Últimas linhas de log do servidor HTTP")
def serve_service_logs(
    lines: int = typer.Option(50, "--lines", "-n"),
):
    console.print(_serve_svc_manager().logs(lines=lines))


# ── bauer gateway init ────────────────────────────────────────────────────────

@gateway_app.command("init", help="Wizard interativo: configura Telegram/Discord")
def gateway_init(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
):
    """Configura canais passo a passo: token, validação live, allowlist, .env."""
    import os
    import time

    import httpx
    import yaml as _yaml
    from rich.prompt import Confirm, Prompt

    console.print(Panel.fit("[bold]Bauer Gateway — setup de canais[/bold]\n"
                            "Tokens vão para o [cyan].env[/cyan]; o resto para o config.yaml."))

    if not config.exists():
        console.print(f"[red]{config} não encontrado.[/red]")
        raise typer.Exit(code=1)
    raw = _yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    env_lines: list[str] = []

    def _ask_token(label: str, env_var: str) -> str:
        """Pede um token com colagem funcionando.

        password=True (getpass) bloqueia paste em vários terminais Windows —
        usuário relatou não conseguir colar. Entrada visível resolve; quem
        já exportou a env var nem precisa digitar.
        """
        existing = os.environ.get(env_var, "").strip()
        if existing:
            masked = existing[:6] + "…" + existing[-4:] if len(existing) > 12 else "***"
            if Confirm.ask(f"{env_var} já está definido ({masked}). Usar esse?", default=True):
                return existing
        console.print("[dim](entrada visível para permitir colar — Ctrl+V / botão direito)[/dim]")
        return Prompt.ask(label, default="").strip()

    # ── Telegram ───────────────────────────────────────────────────────────
    if Confirm.ask("Configurar [bold]Telegram[/bold]?", default=True):
        console.print("\nCrie um bot com o [bold]@BotFather[/bold] no Telegram e copie o token.")
        token = _ask_token("Token do bot", "TELEGRAM_BOT_TOKEN")
        bot_name = ""
        if token:
            try:
                r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
                if r.json().get("ok"):
                    bot_name = r.json()["result"].get("username", "")
                    console.print(f"[green]✓ Token válido — bot @{bot_name}[/green]")
                else:
                    console.print(f"[red]Token rejeitado:[/red] {r.json().get('description')}")
                    token = ""
            except httpx.HTTPError as exc:
                console.print(f"[yellow]Não validou (rede): {exc} — salvando assim mesmo.[/yellow]")
        allowed: list[int] = list((raw.get("telegram") or {}).get("allowed_users", []))
        if token and Confirm.ask(
            "Descobrir seu user id agora? (envie /start ao bot)", default=True
        ):
            console.print(f"[cyan]Aguardando mensagem para @{bot_name} (60s)…[/cyan]")
            deadline = time.time() + 60
            found: set[int] = set()
            offset = 0
            while time.time() < deadline and not found:
                try:
                    r = httpx.get(
                        f"https://api.telegram.org/bot{token}/getUpdates",
                        params={"timeout": 10, "offset": offset + 1}, timeout=20,
                    )
                    for up in r.json().get("result", []):
                        offset = max(offset, up.get("update_id", 0))
                        uid = ((up.get("message") or {}).get("from") or {}).get("id")
                        uname = ((up.get("message") or {}).get("from") or {}).get("username", "?")
                        if uid:
                            found.add(int(uid))
                            console.print(f"[green]✓ Detectado: @{uname} (id {uid})[/green]")
                except httpx.HTTPError:
                    time.sleep(2)
            allowed = sorted(set(allowed) | found)
            if not found:
                console.print("[yellow]Nenhuma mensagem recebida — adicione seu id depois "
                              "em telegram.allowed_users.[/yellow]")
        if token:
            env_lines.append(f"TELEGRAM_BOT_TOKEN={token}")
        tg = raw.setdefault("telegram", {})
        tg["enabled"] = True
        tg["allowed_users"] = allowed

    # ── Discord ────────────────────────────────────────────────────────────
    if Confirm.ask("\nConfigurar [bold]Discord[/bold]?", default=False):
        console.print(
            "\n1. https://discord.com/developers/applications → New Application → Bot\n"
            "2. Copie o token; habilite [bold]MESSAGE CONTENT INTENT[/bold] na aba Bot\n"
            "3. Convide o bot: OAuth2 → URL Generator → scope 'bot' → Send Messages"
        )
        token = _ask_token("Token do bot", "DISCORD_BOT_TOKEN")
        if token:
            try:
                r = httpx.get(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": f"Bot {token}"}, timeout=10,
                )
                if r.status_code == 200:
                    console.print(f"[green]✓ Token válido — bot {r.json().get('username')}[/green]")
                else:
                    console.print(f"[red]Token rejeitado (HTTP {r.status_code}).[/red]")
                    token = ""
            except httpx.HTTPError as exc:
                console.print(f"[yellow]Não validou (rede): {exc} — salvando assim mesmo.[/yellow]")
        user_id = Prompt.ask(
            "Seu user id do Discord (Configurações → Avançado → Modo desenvolvedor → "
            "botão direito no seu nome → Copiar ID)", default=""
        ).strip()
        if token:
            env_lines.append(f"DISCORD_BOT_TOKEN={token}")
        dc = raw.setdefault("discord", {})
        dc["enabled"] = True
        if user_id:
            existing = set(dc.get("allowed_users", []))
            existing.add(user_id)
            dc["allowed_users"] = sorted(existing)

    # ── Persistência ───────────────────────────────────────────────────────
    if env_lines:
        env_path = Path(".env")
        current = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        new_content = current.rstrip("\n")
        for line in env_lines:
            key = line.split("=", 1)[0]
            if f"{key}=" in current:
                console.print(f"[yellow]{key} já existe no .env — não sobrescrevi.[/yellow]")
                continue
            new_content += ("\n" if new_content else "") + line
        env_path.write_text(new_content + "\n", encoding="utf-8")
        console.print(f"[green]✓ Tokens gravados no {env_path}[/green]")

    config.write_text(
        _yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    console.print(f"[green]✓ Seções atualizadas no {config}[/green]")
    console.print("\nPróximo passo: [bold]bauer gateway start[/bold]")


if __name__ == "__main__":
    sys.exit(app())
