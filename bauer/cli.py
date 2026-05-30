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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
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

app.add_typer(config_app, name="config")
app.add_typer(models_app, name="models")
app.add_typer(memory_app, name="memory")
app.add_typer(tools_app, name="tools")
app.add_typer(project_app, name="project")
app.add_typer(task_app, name="task")
app.add_typer(learning_app, name="learning")
app.add_typer(auth_app, name="auth")
app.add_typer(orchestrate_app, name="orchestrate")
app.add_typer(agent_app, name="agent")
app.add_typer(spec_app, name="spec")
app.add_typer(company_app, name="company")
app.add_typer(migrate_app, name="migrate")

# legacy_windows=False: usa ANSI codes em vez de Win32 API (suporta Unicode/UTF-8)
console = Console(highlight=False, legacy_windows=False)


# --- helpers ----------------------------------------------------------------


def _load_or_die(config_path: Path, models_path: Path):
    try:
        cfg = load_config(config_path)
    except ConfigError as exc:
        console.print(f"[red]Erro de config:[/red]\n{exc}")
        raise typer.Exit(code=2)
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
    state = read_state(state_file)
    is_ollama = cfg.model.provider == "ollama"

    stale = (
        state is None
        or state.get("configured_provider", "ollama") != cfg.model.provider
        or (is_ollama and state.get("configured_model") != cfg.model.name)
        or (is_ollama and state.get("ollama_host") != cfg.ollama.host)
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
):
    """Mostra a config validada (resumo)."""
    try:
        cfg = load_config(config)
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2)
    console.print(cfg.model_dump())


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
):
    """Busca semantica (TF-IDF) nos arquivos de memoria."""
    from rich.table import Table as RichTable

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
            if not token.extra.get("type") == "jwt":
                from .openai_client import OpenAIClient
                api_key = token.api_key or token.access_token
                api_base = token.api_base or cfg.openai.host
                extra_headers: dict[str, str] = {}
                # Providers sem prefixo /v1/ no endpoint de chat
                _NO_V1 = {"copilot", "github", "gemini"}
                chat_path = "/chat/completions" if provider in _NO_V1 else "/v1/chat/completions"
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


def _build_router(cfg, workspace: Path) -> ToolRouter:
    """Cria ToolRouter com shell_runner e web_enabled a partir da config."""
    shell_runner = _build_shell_runner(cfg, workspace)
    web_enabled = cfg.tools.web_enabled if cfg is not None else False
    web_config = cfg.web if cfg is not None else None
    return ToolRouter(workspace, shell_runner=shell_runner, web_enabled=web_enabled, web_config=web_config)


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

    # Resolucao do modelo: --model > --pick > auto (com RAM check so para Ollama)
    if model:
        model_name = model
    elif pick:
        model_name = _pick_model(client, state["configured_model"])
    else:
        if is_ollama_provider:
            model_name = _resolve_model_with_ram_check(
                state["configured_model"], reg, client,
                state["ram_available_mb"], cfg.runtime.safety_margin_mb, _MEMORY_DIR,
            )
        else:
            # Providers cloud: usa o modelo configurado diretamente (sem RAM check local)
            model_name = cfg.model.name

    # Verifica modelo no Ollama apenas quando provider=ollama
    if is_ollama_provider and not client.has_model(model_name):
        console.print(
            f"[red]Modelo '{model_name}' nao encontrado no Ollama.[/red]\n"
            f"Rode: [bold]ollama pull {model_name}[/bold]"
        )
        raise typer.Exit(code=1)

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
    # Constrói router com workspace CORRETO (empresa ou global)
    router = _build_router(cfg, workspace)
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

    # Cliente Ollama separado para roteamento/planejamento (sempre local)
    from bauer.ollama_client import OllamaClient as _OllamaClient
    ollama_client = _OllamaClient(cfg.ollama.host, cfg.ollama.timeout_seconds, cfg.ollama.api_key)
    alive, _ = ollama_client.is_alive()
    if not alive:
        console.print(
            f"[red]O orquestrador precisa do Ollama em {cfg.ollama.host} "
            f"para os modelos de roteamento (qwen3:0.6b, smollm3, phi4-mini).[/red]\n"
            f"Verifique se o Ollama esta rodando."
        )
        raise typer.Exit(code=1)

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
    orch_cfg = OrchestratorConfig(
        planner_model=planner or cfg.router.router_model,
        synthesizer_model=synthesizer or cfg.router.reasoning_model,
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


@orchestrate_app.command("list")
def orchestrate_list():
    """Lista tarefas do orquestrador com progresso salvo (prontas para --resume)."""
    from .orchestrator import AgentOrchestrator, OrchestratorConfig
    from unittest.mock import MagicMock as _MM

    # Cria instância mínima só para usar list_saved_progress
    orch = AgentOrchestrator(_MM(), _MM(), _MM(), OrchestratorConfig())

    entries = orch.list_saved_progress()
    if not entries:
        console.print("[dim]Nenhuma tarefa com progresso salvo em .orchestrate_progress/[/dim]")
        console.print("[dim]Tarefas aparecem aqui quando interrompidas antes de concluir.[/dim]")
        return

    table = Table(title=f"Tarefas salvas ({len(entries)})", show_lines=True)
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
        "IN_PROGRESS": "yellow",
        "DONE": "green",
        "BLOCKED": "red",
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


@task_app.command("board")
def task_board(
    workspace: Path = typer.Option(_PROJECT_WORKSPACE, "--workspace"),
    compact: bool = typer.Option(False, "--compact", "-c", help="Mostra apenas ID e titulo (sem descricao)"),
):
    """Exibe o Kanban board no terminal — colunas TODO / IN PROGRESS / DONE / BLOCKED."""
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
            "IN_PROGRESS": "🔄",
            "DONE":        "✅",
            "BLOCKED":     "🚫",
        }
        _BAR_FULL  = "█"
        _BAR_EMPTY = "░"
        _ELLIPSIS  = "…"
    else:
        _ICONS = {
            "TODO":        "[ ]",
            "IN_PROGRESS": "[~]",
            "DONE":        "[x]",
            "BLOCKED":     "[!]",
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
        ("IN_PROGRESS", "IN PROGRESS", "yellow"),
        ("DONE",        "DONE",        "green"),
        ("BLOCKED",     "BLOCKED",     "red"),
    ]

    _CARD_COLOR = {
        "TODO":        "white",
        "IN_PROGRESS": "yellow",
        "DONE":        "green",
        "BLOCKED":     "red",
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
        icon = _ICONS[status]

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


# --- serve ------------------------------------------------------------------


@app.command()
def serve(
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
    """
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
    run_server(fastapi_app, host=serve_host, port=serve_port)


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

    Sem --provider: exibe menu interativo com todos os 13 providers.

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


@app.command("gateway")
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
    no_config: bool = typer.Option(False, "--no-config", help="Pula migração de config.yaml"),
    no_history: bool = typer.Option(False, "--no-history", help="Pula importação do histórico"),
    no_agents: bool = typer.Option(False, "--no-agents", help="Pula criação de agent"),
):
    """Importa configuracoes e historico do Hermes Agent para o Bauer.

    Migra:
      - config.yaml  (provider, modelo, host Ollama)
      - Historico de conversas → memory/sessions/hermes-*.jsonl
      - Toolsets → agent 'hermes-default' em agents.yaml
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
    table.add_row("Sessões de histórico", str(summary.get("session_count", 0)))
    table.add_row("Mensagens no histórico", str(summary.get("total_messages", 0)))
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


if __name__ == "__main__":
    sys.exit(app())
