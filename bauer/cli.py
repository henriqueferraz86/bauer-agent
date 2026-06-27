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

from .paths import get_bauer_home as _get_bauer_home, memory_dir as _memory_dir_fn, runtime_state_path as _runtime_state_path_fn
from .agent import run_agent_session

# P4: paths canônicos movidos p/ bauer/commands/_common.py (compartilhados).
from bauer.commands._common import _RUNTIME_STATE_DEFAULT  # noqa: E402
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
    help="Bauer Agent — runtime adaptativo para LLMs locais e cloud.",
    epilog=(
        "COMECE AQUI:  bauer start (boas-vindas)  ·  bauer init (configurar)  ·  "
        "bauer agent (usar)  ·  bauer model (trocar modelo)  ·  bauer doctor (checar)  ·  "
        "bauer guide (tour).  Os demais comandos sao avancados."
    ),
)

from bauer.commands.config_cmd import config_app  # noqa: E402
from bauer.commands.models_cmd import models_app  # noqa: E402
from bauer.commands.memory_cmd import memory_app  # noqa: E402
from bauer.commands.tools_cmd import tools_app  # noqa: E402
from bauer.commands.project_cmd import project_app  # noqa: E402
from bauer.commands.task_cmd import task_app  # noqa: E402
from bauer.commands.dispatch_cmd import dispatch_app  # noqa: E402
from bauer.commands.ops_cmd import ops_app  # noqa: E402
from bauer.commands.runtime_cmd import runtime_app  # noqa: E402
from bauer.commands.cron_cmd import cron_app  # noqa: E402
from bauer.commands.research_cmd import research_app  # noqa: E402
from bauer.commands.learning_cmd import learning_app  # noqa: E402
from bauer.commands.auth_cmd import auth_app  # noqa: E402
from bauer.commands.orchestrate_cmd import orchestrate_app  # noqa: E402
from bauer.commands.agent_cmd import agent_app  # noqa: E402
from bauer.commands.spec_cmd import spec_app  # noqa: E402
from bauer.commands.company_cmd import company_app  # noqa: E402
from bauer.commands.migrate_cmd import migrate_app  # noqa: E402
from bauer.commands.boards_cmd import boards_app  # noqa: E402
from bauer.commands.daemon_cmd import daemon_app  # noqa: E402

from bauer.commands.traces_cmd import traces_app  # noqa: E402
from bauer.commands.cost_cmd import cost_app  # noqa: E402


from bauer.commands.serve_cmd import serve_app  # noqa: E402

from bauer.commands.telegram_cmd import telegram_app  # noqa: E402
from bauer.commands.discord_cmd import discord_app  # noqa: E402
from bauer.commands.gateway_cmd import gateway_app  # noqa: E402

from bauer.commands.plugin_cmd import plugin_app  # noqa: E402
app.add_typer(plugin_app, name="plugin")

from bauer.commands.factory_cmd import factory_app  # noqa: E402

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
app.add_typer(traces_app, name="traces")
app.add_typer(cost_app, name="cost")
app.add_typer(factory_app, name="factory")

from bauer.commands.home_cmd import home_app  # noqa: E402
app.add_typer(home_app, name="home")

# P4: console único movido para bauer/commands/_common.py (compartilhado com os
# módulos de comando extraídos). Re-importado aqui — uso inalterado em cli.py.
from bauer.commands._common import console  # noqa: E402


# --- onboarding: entrada amigável para quem está começando -------------------


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context):
    """Bauer Agent — runtime adaptativo para LLMs locais e cloud.

    Sem subcomando: mostra a tela de boas-vindas (orienta por onde começar).
    """
    if ctx.invoked_subcommand is not None:
        return
    # `bauer` puro → tela de boas-vindas inteligente em vez da parede de help.
    try:
        from .onboarding import welcome_screen
        welcome_screen(console)
    except Exception:
        console.print("[cyan]Bauer Agent[/cyan] — comece com [bold]bauer init[/bold].")


@app.command("start")
def start_cmd(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
):
    """Tela de boas-vindas — mostra por onde começar conforme seu estado."""
    from .onboarding import welcome_screen
    welcome_screen(console, config_path=config)


@app.command("guide")
def guide_cmd():
    """Tour rápido pelos modos do Bauer (chat, agent, model, gateway)."""
    from .onboarding import guide_tour
    guide_tour(console)


# --- helpers ----------------------------------------------------------------


# P4: helpers de runtime movidos p/ bauer/commands/_runtime.py (re-exportados).
from bauer.commands._runtime import (  # noqa: E402,F401
    _load_or_die,
    _get_or_run_state,
    _build_client,
    _build_shell_runner,
    _build_router,
    _resolve_model_with_ram_check,
    _pick_model,
    _start_gateway_thread_cli,
    _kill_bridge_processes,
)




# --- comandos ---------------------------------------------------------------


@app.command()
def doctor(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    models: Path = typer.Option(Path("models.yaml"), "--models", help="Caminho do models.yaml"),
    state_file: Path = typer.Option(
        _RUNTIME_STATE_DEFAULT,
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

    # --- Web search (G18) --------------------------------------------------------
    try:
        from .web.dispatcher import WebDispatcher
        _web = WebDispatcher(getattr(cfg, "web", None))
        _wb = _web.detected_backends()
        _wt = Table(show_header=False, box=None, padding=(0, 1))
        _wt.add_row("Busca:", f"{_wb['search']}  [dim]({_wb['search_reason']})[/dim]")
        _wt.add_row("Extração:", f"{_wb['extract']}  [dim]({_wb['extract_reason']})[/dim]")
        console.print(Panel(_wt, title="Web search", border_style="cyan"))
        if _wb["search"] == "wikipedia":
            console.print(
                "  [dim]Só Wikipedia (fatos, open-source). Para busca web geral: "
                "pip install 'bauer-agent[web]' (ddgs) ou suba um SearXNG.[/dim]"
            )
    except Exception as _web_exc:
        console.print(f"[dim]Web search: não foi possível detectar ({_web_exc})[/dim]")

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
    config: Path = typer.Option(None, "--config", "-c", help="Caminho do config.yaml (default: ~/.bauer/config.yaml)"),
    env: Path = typer.Option(None, "--env", help="Caminho do .env (default: ~/.bauer/.env)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Sobrescrever config existente sem confirmacao"),
):
    """Wizard de primeiro uso — configura provider, modelo e workspace em ~/.bauer/."""
    from .paths import config_path as _cfg_path, get_bauer_home as _gbh
    from .init_wizard import run_init_wizard
    _bh = _gbh()
    config = config or _cfg_path()
    env = env or (_bh / ".env")
    ok = run_init_wizard(
        config_path=config,
        env_path=env,
        force=yes,
    )
    if not ok:
        raise typer.Exit(code=1)

    # Leva o iniciante do zero ao primeiro chat sem precisar digitar mais nada.
    if not yes and sys.stdin.isatty():
        try:
            go = console.input(
                "\n[bold]Checar o ambiente e abrir o agente agora?[/bold] [dim](S/n)[/dim] "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            go = "n"
        if go in ("", "s", "sim", "y", "yes"):
            import subprocess as _sp
            _base = [sys.executable, "-m", "bauer.cli"]
            console.print("\n[dim]› bauer doctor[/dim]")
            try:
                _sp.run([*_base, "doctor", "--config", str(config)])
            except Exception:
                pass
            console.print("\n[dim]› bauer agent  (digite /exit para sair)[/dim]\n")
            try:
                _sp.run([*_base, "agent", "--config", str(config)])
            except Exception:
                pass
        else:
            console.print(
                "\n[dim]Quando quiser: [/dim][bold]bauer agent[/bold]"
                "[dim] (usar) · [/dim][bold]bauer guide[/bold][dim] (tour).[/dim]\n"
            )


@app.command()
def status(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    state_file: Path = typer.Option(
        _RUNTIME_STATE_DEFAULT,
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
    config: Path = typer.Option(None, "--config", help="Caminho do config.yaml (default: ~/.bauer/config.yaml)"),
):
    """Seletor interativo de provider e modelo — igual ao 'hermes model'.

    Lista providers (Ollama, OpenRouter, OpenAI, Groq, Custom) e modelos disponíveis.
    Salva a escolha no config.yaml e a API key no .env automaticamente.
    """
    from .paths import config_path as _cfg_path
    from .model_switcher import run_model_switcher
    run_model_switcher(config or _cfg_path())


@app.command()
def chat(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    models: Path = typer.Option(Path("models.yaml"), "--models", help="Caminho do models.yaml"),
    state_file: Path = typer.Option(
        _RUNTIME_STATE_DEFAULT,
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




































# --- memory -----------------------------------------------------------------

# P4: _MEMORY_DIR / _FILE_ALIASES movidos p/ bauer/commands/_common.py.
from bauer.commands._common import _MEMORY_DIR, _FILE_ALIASES  # noqa: E402






























# --- tools ------------------------------------------------------------------

# P4: _WORKSPACE_DIR / _COMPANIES_DIR movidos p/ bauer/commands/_common.py.
from bauer.commands._common import _WORKSPACE_DIR, _COMPANIES_DIR  # noqa: E402














# ---------------------------------------------------------------------------
# bauer plugin — plugin manager com suporte a plugin.yaml manifests
# ---------------------------------------------------------------------------









# --- agent ------------------------------------------------------------------










# --- agent sub-commands (create / list / run / delete) ----------------------












# --- orchestrate ------------------------------------------------------------






















# --- traces -----------------------------------------------------------------





# --- cost -------------------------------------------------------------------





# --- project ----------------------------------------------------------------

# P4: movido p/ bauer/commands/_common.py (compartilhado com grupos extraídos).
from bauer.commands._common import _PROJECT_WORKSPACE  # noqa: E402









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




















# --- ops --------------------------------------------------------------------








# --- runtime supervisor -------------------------------------------------------


















# --- daemon -----------------------------------------------------------------






















# --- cron -------------------------------------------------------------------


















# --- research ---------------------------------------------------------------






# --- dispatch ---------------------------------------------------------------
















# --- desktop ----------------------------------------------------------------


def _desktop_serve_cmd(config: Path, host: str, port: int, api_key: str) -> list[str]:
    """Monta o comando do serve sidecar usado pelo `bauer desktop`."""
    import sys
    cmd = [
        sys.executable, "-m", "bauer.cli", "serve",
        "--config", str(config), "--host", host, "--port", str(port),
    ]
    if api_key:
        cmd += ["--api-key", api_key]
    return cmd


@app.command()
def desktop(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    host: str = typer.Option("127.0.0.1", "--host", help="Host de escuta do serve sidecar"),
    port: int = typer.Option(8799, "--port", help="Porta do serve sidecar"),
    no_open: bool = typer.Option(False, "--no-open", help="Não abre o navegador automaticamente"),
    dev: bool = typer.Option(False, "--dev", help="Modo dev: abre o Vite (:5173); rode o serve à parte"),
    api_key: str = typer.Option("", "--api-key", help="X-API-Key do serve (se houver auth)"),
    timeout: float = typer.Option(25.0, "--timeout", help="Segundos aguardando o serve responder /health"),
):
    """Abre o Bauer Agent Desktop (SPA) no navegador, subindo o `bauer serve` como sidecar.

    A interface gráfica (8 telas: Projetos, Chat, Kanban, Modelos, Gateway,
    Observabilidade, Logs, Config) é servida pelo próprio serve em ``/``.

    Modo dev (``--dev``): assume um ``bauer serve`` já rodando e abre o Vite dev
    server (http://127.0.0.1:5173); rode ``npm run dev`` em ``desktop/``.
    """
    import subprocess
    import webbrowser

    from .desktop_api import wait_for_health

    if dev:
        url = "http://127.0.0.1:5173"
        console.print(f"[bold]Bauer Desktop (dev)[/bold] — {url}")
        console.print("[dim]Rode `bauer serve` num terminal e `npm run dev` em desktop/.[/dim]")
        if not no_open:
            webbrowser.open(url)
        return

    url = f"http://{host}:{port}"
    console.print(f"[bold]Bauer Desktop[/bold] — iniciando serve em {url} …")
    proc = subprocess.Popen(_desktop_serve_cmd(config, host, port, api_key))
    try:
        if not wait_for_health(f"{url}/health", timeout=timeout):
            console.print("[red]O serve não respondeu a tempo.[/red] Veja se o Ollama/provider está ok.")
            proc.terminate()
            raise typer.Exit(code=1)
        console.print(f"[green]Pronto[/green] — {url}  (Ctrl+C encerra)")
        if not no_open:
            webbrowser.open(url)
        proc.wait()
    except KeyboardInterrupt:
        console.print("\n[dim]Encerrando o serve…[/dim]")
    finally:
        if proc.poll() is None:
            proc.terminate()


# --- serve ------------------------------------------------------------------




# --- learning ---------------------------------------------------------------




















# --- auth -------------------------------------------------------------------










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

# P4: movido p/ bauer/commands/_common.py (compartilhado com grupos extraídos).
from bauer.commands._common import _SPECS_DIR  # noqa: E402














# ─────────────────────────────────────────────────────────────────────────────
# company — gestão multi-empresa
# ─────────────────────────────────────────────────────────────────────────────
# _COMPANIES_DIR já definido no topo do módulo como workspace/companies/
















# ── App Factory (Spec-Driven Development) ──────────────────────────────────────










def main():
    app()


# ── gateway helpers ───────────────────────────────────────────────────────────



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


@app.command("shell")
def shell_cmd(
    port: int = typer.Option(7782, "--port", "-p", help="Porta do shell WebSocket"),
    host: str = typer.Option("127.0.0.1", "--host", help="Interface de escuta"),
    shell: str = typer.Option(None, "--shell", help="Shell a usar (default: cmd.exe no Windows, bash no Unix)"),
):
    """Inicia um shell interativo sobre WebSocket (G14 — PTY bridge).

    Sobe um servidor FastAPI que expoe ws://<host>:<port>/ws/shell, fazendo
    bridge entre o WebSocket e um processo shell real (cmd.exe / bash).
    Compatível com Windows (usa asyncio subprocess, sem pty POSIX).

    Protocolo JSON:
      client → {"type": "input",  "data": "ls\\n"}
      server → {"type": "output", "data": "..."}  /  {"type": "exit", "code": 0}
    """
    try:
        from .shell_server import main as shell_main
    except ImportError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    argv = ["--port", str(port), "--host", host]
    if shell:
        argv += ["--shell", shell]
    console.print(f"\n[bold]Bauer Shell[/bold] → ws://{host}:{port}/ws/shell")
    console.print("[dim]  Ctrl+C para encerrar.[/dim]\n")
    try:
        shell_main(argv)
    except KeyboardInterrupt:
        console.print("\n[dim]Shell encerrado.[/dim]")


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

from bauer.commands.skills_hub_cmd import skills_hub_app  # noqa: E402
app.add_typer(skills_hub_app, name="skills-hub")

from bauer.commands.skills_bundle_cmd import skills_bundle_app  # noqa: E402
app.add_typer(skills_bundle_app, name="skills-bundle")












# ---------------------------------------------------------------------------
# Skill bundles
# ---------------------------------------------------------------------------









# --- Bauer Gateway: telegram / discord / gateway -----------------------------

























# ── bauer gateway service — serviço do sistema (paridade hermes-gateway) ─────
















# ── bauer daemon service — serviço do sistema ────────────────────────────────


















# ── bauer runtime service — serviço do sistema ───────────────────────────────


















# ── bauer serve service — serviço do sistema ─────────────────────────────────




















# ── bauer gateway init ────────────────────────────────────────────────────────



# ── G11: bauer credential ────────────────────────────────────────────────────

from bauer.commands.credential_cmd import credential_app  # noqa: E402
app.add_typer(credential_app, name="credential")










# --- home --------------------------------------------------------------------








if __name__ == "__main__":
    sys.exit(app())
