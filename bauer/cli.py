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
task_app = typer.Typer(help="Gerenciamento de tarefas (TASKS.md)")
from bauer.commands.dispatch_cmd import dispatch_app  # noqa: E402
from bauer.commands.ops_cmd import ops_app  # noqa: E402
runtime_app = typer.Typer(help="Supervisor always-on: dispatcher, cron, outbox e kanban")
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
daemon_app = typer.Typer(help="BauerDaemon — pool de workers autonomos que processam tasks do kanban")
daemon_service_app = typer.Typer(
    help="Daemon como SERVICO do sistema (systemd/Task Scheduler) — sobe no boot, reinicia em crash"
)
daemon_app.add_typer(daemon_service_app, name="service")

from bauer.commands.traces_cmd import traces_app  # noqa: E402
from bauer.commands.cost_cmd import cost_app  # noqa: E402

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

from bauer.commands.telegram_cmd import telegram_app  # noqa: E402
from bauer.commands.discord_cmd import discord_app  # noqa: E402
gateway_app = typer.Typer(help="Bauer Gateway — todos os canais de chat + entrega do outbox")
gateway_service_app = typer.Typer(
    help="Gateway como SERVIÇO do sistema (systemd/Task Scheduler) — sobe no boot, reinicia em crash"
)
gateway_app.add_typer(gateway_service_app, name="service")

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


@serve_app.callback()
def serve(
    ctx: typer.Context,
    config: Path = typer.Option(Path("config.yaml"), "--config", help="Caminho do config.yaml"),
    models: Path = typer.Option(Path("models.yaml"), "--models", help="Caminho do models.yaml"),
    workspace: Path = typer.Option(_WORKSPACE_DIR, "--workspace"),
    state_file: Path = typer.Option(_RUNTIME_STATE_DEFAULT, "--state-file"),
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
        config_path=config,
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

def _gateway_pid_file(workspace: Path) -> Path:
    return workspace / ".bauer_gateway" / "gateway.pid"




def _gateway_workspace(config: Path) -> Path:
    try:
        from bauer.config_loader import load_config as _load_cfg
        return Path(_load_cfg(config).agent.workspace)
    except Exception:
        return Path("workspace")


def _gateway_start_background(config: Path) -> None:
    """Sobe o gateway destacado (detach) e libera o terminal. Log em arquivo."""
    import os as _os
    import subprocess as _sp

    workspace = _gateway_workspace(config)
    pid_file = _gateway_pid_file(workspace)

    # Já rodando? Não sobe outro.
    if pid_file.exists():
        try:
            import psutil
            pid = int(pid_file.read_text(encoding="utf-8").strip())
            if psutil.pid_exists(pid):
                console.print(
                    f"[yellow]Gateway já rodando[/yellow] pid={pid}. "
                    f"Use [bold]bauer gateway status[/bold] ou [bold]bauer gateway stop[/bold]."
                )
                return
        except Exception:
            pass  # PID stale — segue e sobe um novo

    log_path = workspace / ".bauer_gateway" / "gateway.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab")

    # O filho roda a versão FOREGROUND (sem --background) e escreve o próprio PID.
    cmd = [
        sys.executable, "-m", "bauer.cli", "gateway", "start",
        "--config", str(Path(config).resolve()),
    ]
    popen_kwargs: dict = {
        "stdout": log_handle,
        "stderr": _sp.STDOUT,
        "stdin": _sp.DEVNULL,
        "close_fds": True,
        "cwd": str(Path(__file__).resolve().parent.parent),
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

    # O filho grava o próprio pid no pid_file; aguarda um instante e lê o real.
    import time as _time
    real_pid = proc.pid
    for _ in range(20):
        _time.sleep(0.1)
        try:
            real_pid = int(pid_file.read_text(encoding="utf-8").strip())
            break
        except Exception:
            continue

    console.print(
        f"[green]✓ Bauer Gateway iniciado em background[/green] pid={real_pid}"
    )
    console.print(f"[dim]Log:[/dim] {log_path}")
    console.print(
        "[dim]Monitorar: [/dim][bold]bauer gateway status[/bold]"
        "[dim]  ·  Parar: [/dim][bold]bauer gateway stop[/bold]"
    )








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






@gateway_app.command("start", help="Inicia todos os canais habilitados + outbox pump")
def gateway_start(
    config: Path = typer.Option(Path("config.yaml"), "--config", "-c"),
    background: bool = typer.Option(
        False, "--background", "-b",
        help="Roda em background (detach) e libera o terminal",
    ),
):
    """Bauer Gateway: canais do config.yaml (telegram/discord) + entrega do outbox.

    Use --background / -b para rodar destacado (log em workspace/.bauer_gateway/
    gateway.log). Pare com `bauer gateway stop`.
    """
    if background:
        _gateway_start_background(config)
        return

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
    return GatewayServiceManager()  # usa ~/.bauer/ como working dir


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
) -> "ProcessServiceConfig":  # noqa: F821
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
) -> "ProcessServiceManager":  # noqa: F821
    from bauer.process_service import ProcessServiceManager
    from .paths import get_bauer_home
    return ProcessServiceManager(_daemon_service_cfg(board, workers, budget_usd, budget_hours), project_dir=get_bauer_home())


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


def _runtime_service_cfg(workspace: "Path | None" = None) -> "ProcessServiceConfig":  # noqa: F821
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


def _runtime_svc_manager(workspace: "Path | None" = None) -> "ProcessServiceManager":  # noqa: F821
    from bauer.process_service import ProcessServiceManager
    from .paths import get_bauer_home
    return ProcessServiceManager(_runtime_service_cfg(workspace), project_dir=get_bauer_home())


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


def _serve_service_cfg() -> "ProcessServiceConfig":  # noqa: F821
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


def _serve_svc_manager() -> "ProcessServiceManager":  # noqa: F821
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


# ── G11: bauer credential ────────────────────────────────────────────────────

from bauer.commands.credential_cmd import credential_app  # noqa: E402
app.add_typer(credential_app, name="credential")










# --- home --------------------------------------------------------------------








if __name__ == "__main__":
    sys.exit(app())
