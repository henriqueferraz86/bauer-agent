"""bauer model — seletor interativo de provider e modelo (igual ao hermes model).

Fluxo:
  1. Mostra provider/modelo atual
  2. Usuário escolhe provider
  3. Lista modelos disponíveis (Ollama: da API; cloud: lista curada)
  4. Pede API key se necessário (salva no .env)
  5. Salva provider + modelo no config.yaml
"""

from __future__ import annotations

from pathlib import Path

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console(highlight=False)

# ---------------------------------------------------------------------------
# Modelos curados por provider (igual ao hermes model faz)
# ---------------------------------------------------------------------------

OPENROUTER_MODELS: list[tuple[str, str]] = [
    # (id, descrição)
    ("openai/gpt-4o-mini",                  "ChatGPT 4o mini — rápido e barato"),
    ("openai/gpt-4o",                       "ChatGPT 4o — mais capaz"),
    ("openai/gpt-4.1",                      "ChatGPT 4.1 — mais recente"),
    ("anthropic/claude-haiku-3-5",          "Claude Haiku — rápido e barato"),
    ("anthropic/claude-sonnet-4",           "Claude Sonnet — equilibrado"),
    ("anthropic/claude-opus-4",             "Claude Opus — mais capaz"),
    ("google/gemini-flash-1.5",             "Gemini Flash — rápido"),
    ("google/gemini-2.0-flash-001",         "Gemini 2.0 Flash"),
    ("meta-llama/llama-3.3-70b-instruct",   "Llama 3.3 70B — gratuito via OR"),
    ("deepseek/deepseek-chat",              "DeepSeek V3 — gratuito via OR"),
    ("qwen/qwen-2.5-72b-instruct",          "Qwen 2.5 72B — gratuito via OR"),
]

OPENAI_MODELS: list[tuple[str, str]] = [
    ("gpt-4o-mini",     "GPT-4o mini — rápido e barato (recomendado)"),
    ("gpt-4o",          "GPT-4o — mais capaz"),
    ("gpt-4.1",         "GPT-4.1 — mais recente"),
    ("gpt-4-turbo",     "GPT-4 Turbo"),
    ("gpt-3.5-turbo",   "GPT-3.5 Turbo — muito barato"),
]

GROQ_MODELS: list[tuple[str, str]] = [
    ("llama-3.3-70b-versatile",     "Llama 3.3 70B — gratuito, muito rápido"),
    ("llama-3.1-8b-instant",        "Llama 3.1 8B — ultra rápido"),
    ("gemma2-9b-it",                "Gemma 2 9B"),
    ("mixtral-8x7b-32768",          "Mixtral 8x7B"),
    ("deepseek-r1-distill-llama-70b", "DeepSeek R1 70B"),
]

OPENCODE_MODELS: list[tuple[str, str]] = [
    ("deepseek-v4-flash-free",  "DeepSeek V4 Flash — gratuito, rápido (recomendado)"),
    ("mimo-v2.5-free",          "MiMo 2.5 — raciocínio, gratuito"),
    ("nemotron-3-super-free",   "Nemotron 3 Super — NVIDIA, gratuito"),
    ("big-pickle",              "Big Pickle — opencode flagship, gratuito"),
]

ANTHROPIC_MODELS: list[tuple[str, str]] = [
    ("claude-haiku-3-5-20241022",   "Claude 3.5 Haiku — mais rápido e barato"),
    ("claude-sonnet-4-20250514",    "Claude Sonnet 4 — equilibrado (recomendado)"),
    ("claude-opus-4-20250514",      "Claude Opus 4 — mais capaz"),
    ("claude-3-5-sonnet-20241022",  "Claude 3.5 Sonnet — estável"),
]

GEMINI_MODELS: list[tuple[str, str]] = [
    ("gemini-2.0-flash",            "Gemini 2.0 Flash — rápido (recomendado)"),
    ("gemini-2.5-flash-preview-05-20", "Gemini 2.5 Flash Preview — mais capaz"),
    ("gemini-2.5-pro-preview-05-06",   "Gemini 2.5 Pro Preview — flagship"),
    ("gemini-1.5-flash",            "Gemini 1.5 Flash — estável"),
    ("gemini-1.5-pro",              "Gemini 1.5 Pro — estável e capaz"),
]

MISTRAL_MODELS: list[tuple[str, str]] = [
    ("mistral-small-latest",        "Mistral Small — rápido e barato"),
    ("mistral-medium-latest",       "Mistral Medium — equilibrado (recomendado)"),
    ("mistral-large-latest",        "Mistral Large — mais capaz"),
    ("codestral-latest",            "Codestral — especializado em código"),
    ("open-mistral-nemo",           "Mistral Nemo — open, 12B"),
]

XAI_MODELS: list[tuple[str, str]] = [
    ("grok-3-mini-fast",   "Grok 3 Mini Fast — rápido e barato"),
    ("grok-3-mini",        "Grok 3 Mini — equilíbrio custo/qualidade"),
    ("grok-3-fast",        "Grok 3 Fast — rápido"),
    ("grok-3",             "Grok 3 — mais capaz (recomendado)"),
]

TOGETHER_MODELS: list[tuple[str, str]] = [
    ("meta-llama/Llama-3.3-70B-Instruct-Turbo",  "Llama 3.3 70B Turbo — rápido (recomendado)"),
    ("meta-llama/Llama-3.1-8B-Instruct-Turbo",   "Llama 3.1 8B Turbo — ultra rápido"),
    ("deepseek-ai/DeepSeek-V3",                   "DeepSeek V3 — muito capaz"),
    ("Qwen/Qwen2.5-72B-Instruct-Turbo",           "Qwen 2.5 72B Turbo"),
    ("mistralai/Mixtral-8x7B-Instruct-v0.1",      "Mixtral 8x7B"),
]

DEEPSEEK_MODELS: list[tuple[str, str]] = [
    ("deepseek-chat",      "DeepSeek V3 — geral, muito capaz (recomendado)"),
    ("deepseek-reasoner",  "DeepSeek R1 — raciocínio avançado"),
]

GITHUB_MODELS: list[tuple[str, str]] = [
    ("gpt-4o-mini",                            "GPT-4o mini — rápido e barato (recomendado)"),
    ("gpt-4o",                                 "GPT-4o — mais capaz"),
    ("meta-llama-3.3-70b-instruct",            "Llama 3.3 70B"),
    ("mistral-small",                          "Mistral Small"),
    ("phi-4",                                  "Phi-4 — Microsoft"),
    ("ai21-jamba-1.5-large",                   "Jamba 1.5 Large — AI21"),
]

PROVIDERS: list[tuple[str, str, str]] = [
    # (id, nome, descrição)
    ("ollama",      "Ollama",          "Modelos locais gratuitos (requer Ollama rodando)"),
    ("opencode",    "OpenCode Zen",    "Modelos gratuitos via opencode.ai — sem API key"),
    ("openrouter",  "OpenRouter",      "200+ modelos: GPT, Claude, Gemini — 1 chave"),
    ("openai",      "OpenAI",          "ChatGPT diretamente (sk-...)"),
    ("anthropic",   "Anthropic",       "Claude Haiku, Sonnet, Opus"),
    ("gemini",      "Google Gemini",   "Gemini Flash, Pro (GEMINI_API_KEY)"),
    ("groq",        "Groq",            "Llama ultra-rápido, gratuito com limites"),
    ("mistral",     "Mistral AI",      "Mistral Small/Medium/Large, Codestral"),
    ("xai",         "xAI Grok",        "Grok 3 — modelos da xAI/Elon Musk"),
    ("together",    "Together AI",     "Llama, Mistral, Qwen — hospedagem aberta"),
    ("deepseek",    "DeepSeek",        "DeepSeek V3 e R1 — China, preço baixo"),
    ("github",      "GitHub Models",   "GPT-4o, Llama via GitHub (requer GITHUB_TOKEN)"),
    ("copilot",     "GitHub Copilot",  "Copilot API — requer 'bauer auth login -p copilot'"),
    ("custom",      "Custom",          "Qualquer endpoint OpenAI-compatible (LM Studio, vLLM…)"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_env(env_path: Path) -> dict[str, str]:
    """Lê .env e retorna dict de key=value."""
    result: dict[str, str] = {}
    if not env_path.exists():
        return result
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def _write_env_key(env_path: Path, key: str, value: str) -> None:
    """Escreve ou atualiza uma chave no .env."""
    lines: list[str] = []
    updated = False

    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
                lines.append(f"{key}={value}")
                updated = True
            else:
                lines.append(line)

    if not updated:
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _patch_config(config_path: Path, provider: str, model_name: str, extra: dict | None = None) -> None:
    """Atualiza provider + model.name no config.yaml preservando o resto."""
    raw: dict = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    raw.setdefault("model", {})
    raw["model"]["provider"] = provider
    raw["model"]["name"] = model_name

    # Para groq: salva host na seção openai (é um endpoint openai-compatible)
    if extra:
        for section, kv in extra.items():
            raw.setdefault(section, {})
            raw[section].update(kv)

    config_path.write_text(yaml.dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _pick_from_list(items: list[tuple[str, str]], title: str) -> str | None:
    """Exibe tabela numerada e retorna o id escolhido."""
    table = Table(title=title, show_lines=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("modelo / provider", style="cyan")
    table.add_column("descrição")

    for i, (id_, desc) in enumerate(items, 1):
        table.add_row(str(i), id_, desc)

    console.print(table)

    raw = Prompt.ask(
        "[bold]Escolha pelo número[/bold] (ou Enter para cancelar)",
        default="",
    ).strip()

    if not raw:
        return None
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(items):
            return items[idx][0]
    except ValueError:
        # Digitou o nome direto
        for id_, _ in items:
            if raw == id_:
                return raw
    return raw  # aceita qualquer string (modelo customizado)


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------

def run_model_switcher(config_path: Path) -> None:
    """Seletor interativo de provider + modelo. Salva config.yaml e .env."""
    env_path = config_path.parent / ".env"

    # Lê config atual
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {}

    current_provider = raw.get("model", {}).get("provider", "ollama")
    current_model = raw.get("model", {}).get("name", "—")

    console.print(Panel(
        f"[dim]Provider atual:[/dim] [cyan]{current_provider}[/cyan]\n"
        f"[dim]Modelo atual:  [/dim] [cyan]{current_model}[/cyan]",
        title="[bold]bauer model[/bold]",
        border_style="cyan",
    ))

    # --- escolha do provider ---
    provider_id = _pick_from_list(
        [(p, desc) for p, _, desc in PROVIDERS],
        "Escolha o provider",
    )
    if not provider_id:
        console.print("[dim]Cancelado.[/dim]")
        return

    # Normaliza: "groq" é "openai" internamente com host do Groq
    internal_provider = "openai" if provider_id == "groq" else provider_id
    # opencode permanece "opencode" (suporte nativo no _build_client)

    # --- escolha do modelo ---
    model_name: str | None = None
    env_vars: dict[str, str] = {}
    config_extra: dict | None = None

    if provider_id == "ollama":
        model_name = _pick_ollama_model(raw.get("ollama", {}).get("host", "http://localhost:11434"))

    elif provider_id == "opencode":
        console.print(
            "\n[cyan]OpenCode Zen[/cyan] — modelos gratuitos, sem API key necessária.\n"
            "[dim]Endpoint: https://opencode.ai/zen/v1 (OpenAI-compatible)[/dim]\n"
        )
        model_name = _pick_from_list(OPENCODE_MODELS, "Modelos OpenCode Zen (gratuitos)")
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        internal_provider = "opencode"

    elif provider_id == "openrouter":
        model_name = _pick_from_list(OPENROUTER_MODELS, "Modelos OpenRouter")
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        env_key, env_label, link = "OPENROUTER_API_KEY", "OPENROUTER_API_KEY", "https://openrouter.ai/keys"
        env_vars, config_extra = _ask_api_key(env_path, env_key, env_label, link)
        internal_provider = "openrouter"

    elif provider_id == "openai":
        model_name = _pick_from_list(OPENAI_MODELS, "Modelos OpenAI")
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        env_key, env_label, link = "OPENAI_API_KEY", "OPENAI_API_KEY", "https://platform.openai.com/api-keys"
        env_vars, config_extra = _ask_api_key(env_path, env_key, env_label, link)
        config_extra = config_extra or {}
        config_extra.setdefault("openai", {})["host"] = "https://api.openai.com"

    elif provider_id == "anthropic":
        model_name = _pick_from_list(ANTHROPIC_MODELS, "Modelos Anthropic (Claude)")
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        env_vars, config_extra = _ask_api_key(
            env_path, "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
            "https://console.anthropic.com/settings/keys",
        )
        internal_provider = "anthropic"

    elif provider_id == "gemini":
        model_name = _pick_from_list(GEMINI_MODELS, "Modelos Google Gemini")
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        env_vars, config_extra = _ask_api_key(
            env_path, "GEMINI_API_KEY", "GEMINI_API_KEY (ou GOOGLE_API_KEY)",
            "https://aistudio.google.com/app/apikey",
        )
        internal_provider = "gemini"

    elif provider_id == "groq":
        model_name = _pick_from_list(GROQ_MODELS, "Modelos Groq")
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        env_vars, config_extra = _ask_api_key(
            env_path, "GROQ_API_KEY", "GROQ_API_KEY",
            "https://console.groq.com/keys",
        )
        internal_provider = "groq"

    elif provider_id == "mistral":
        model_name = _pick_from_list(MISTRAL_MODELS, "Modelos Mistral AI")
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        env_vars, config_extra = _ask_api_key(
            env_path, "MISTRAL_API_KEY", "MISTRAL_API_KEY",
            "https://console.mistral.ai/api-keys",
        )
        internal_provider = "mistral"

    elif provider_id == "xai":
        model_name = _pick_from_list(XAI_MODELS, "Modelos xAI (Grok)")
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        env_vars, config_extra = _ask_api_key(
            env_path, "XAI_API_KEY", "XAI_API_KEY",
            "https://console.x.ai",
        )
        internal_provider = "xai"

    elif provider_id == "together":
        model_name = _pick_from_list(TOGETHER_MODELS, "Modelos Together AI")
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        env_vars, config_extra = _ask_api_key(
            env_path, "TOGETHER_API_KEY", "TOGETHER_API_KEY",
            "https://api.together.xyz/settings/api-keys",
        )
        internal_provider = "together"

    elif provider_id == "deepseek":
        model_name = _pick_from_list(DEEPSEEK_MODELS, "Modelos DeepSeek")
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        env_vars, config_extra = _ask_api_key(
            env_path, "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY",
            "https://platform.deepseek.com/api-keys",
        )
        internal_provider = "deepseek"

    elif provider_id == "github":
        model_name = _pick_from_list(GITHUB_MODELS, "Modelos GitHub Models")
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        console.print(
            "\n[dim]GitHub Models usa seu GITHUB_TOKEN (Personal Access Token).[/dim]\n"
            "[dim]Crie em: https://github.com/settings/tokens[/dim]\n"
        )
        env_vars, config_extra = _ask_api_key(
            env_path, "GITHUB_TOKEN", "GITHUB_TOKEN",
            "https://github.com/settings/tokens",
        )
        internal_provider = "github"

    elif provider_id == "copilot":
        console.print(
            "\n[cyan]GitHub Copilot[/cyan] requer autenticação via Device Flow.\n"
            "[dim]Execute o comando abaixo e volte aqui depois:[/dim]\n"
            "  [bold]bauer auth login -p copilot[/bold]\n"
        )
        model_name = Prompt.ask(
            "[bold]Nome do modelo Copilot[/bold]",
            default="gpt-4o",
        ).strip()
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        internal_provider = "copilot"

    elif provider_id == "custom":
        host = Prompt.ask("[bold]Host do servidor[/bold]", default="http://localhost:1234").strip()
        model_name = Prompt.ask("[bold]Nome do modelo[/bold]").strip()
        if not model_name:
            console.print("[dim]Cancelado.[/dim]")
            return
        use_key = Confirm.ask("O servidor requer API key?", default=False)
        if use_key:
            key = Prompt.ask("[bold]API key[/bold]", password=True).strip()
            if key:
                _write_env_key(env_path, "OPENAI_API_KEY", key)
                console.print(f"[green]OPENAI_API_KEY salvo em {env_path}[/green]")
        config_extra = {"openai": {"host": host}}

    if not model_name:
        console.print("[dim]Cancelado.[/dim]")
        return

    # --- salva .env ---
    for k, v in env_vars.items():
        _write_env_key(env_path, k, v)
        console.print(f"[green]{k} salvo em {env_path}[/green]")

    # --- salva config.yaml ---
    _patch_config(config_path, internal_provider, model_name, config_extra)

    # Instrucao pós-salvo: bauer agent lê config na hora (sem restart);
    # só o bauer serve (HTTP) precisa de restart para pegar a mudança.
    _next_step = (
        "Execute [bold]bauer agent[/bold] — o CLI ja usa o novo modelo."
        if internal_provider != "ollama"
        else "Execute [bold]bauer agent[/bold] ou reinicie [bold]bauer serve[/bold] para aplicar."
    )
    console.print(Panel(
        f"[green]✓[/green] Provider: [cyan]{internal_provider}[/cyan]\n"
        f"[green]✓[/green] Modelo:   [cyan]{model_name}[/cyan]\n\n"
        f"[dim]{_next_step}[/dim]",
        title="[bold green]Salvo[/bold green]",
        border_style="green",
    ))


def _pick_ollama_model(ollama_host: str) -> str | None:
    """Lista modelos do Ollama local e deixa escolher."""
    console.print(f"[dim]Buscando modelos em {ollama_host}…[/dim]")
    try:
        import httpx
        r = httpx.get(f"{ollama_host.rstrip('/')}/api/tags", timeout=5)
        r.raise_for_status()
        models_raw = r.json().get("models", [])
        models = [(m["name"], f"{m.get('size', 0) // 1_000_000_000:.1f}GB") for m in models_raw]
    except Exception as exc:
        console.print(f"[yellow]Ollama offline ou sem resposta ({exc}).[/yellow]")
        models = []

    if models:
        choice = _pick_from_list(models, "Modelos Ollama instalados")
        return choice
    else:
        console.print("[dim]Nenhum modelo encontrado. Digite o nome manualmente:[/dim]")
        return Prompt.ask("[bold]Nome do modelo[/bold]", default="qwen2.5:14b").strip() or None


def _ask_api_key(
    env_path: Path,
    env_key: str,
    env_label: str,
    link: str,
) -> tuple[dict[str, str], dict | None]:
    """Verifica se a chave já existe; se não, pede ao usuário.

    Retorna (env_vars_para_salvar, None).
    """
    existing = _read_env(env_path).get(env_key, "")
    if existing:
        console.print(f"[dim]{env_label} já configurado em .env ✓[/dim]")
        return {}, None

    console.print(f"\n[yellow]Você precisa de uma API key: {link}[/yellow]")
    try:
        import getpass as _getpass
        key = _getpass.getpass(f"{env_label} (Enter para pular): ").strip()
    except Exception:
        # Fallback se getpass não funcionar no terminal (ex: pipe/CI)
        key = Prompt.ask(f"[bold]{env_label}[/bold] (Enter para pular)", default="").strip()

    if key:
        return {env_key: key}, None
    else:
        console.print(f"[dim]Sem chave. Adicione {env_key}=sua-chave no .env depois.[/dim]")
        return {}, None
