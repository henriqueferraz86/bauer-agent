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
    # Free models first (official OpenRouter convention: ':free').
    ("meta-llama/llama-3.3-70b-instruct:free", "FREE | Llama 3.3 70B"),
    ("openai/gpt-oss-120b:free",               "FREE | GPT OSS 120B"),
    ("openai/gpt-oss-20b:free",                "FREE | GPT OSS 20B"),
    ("qwen/qwen3-coder:free",                  "FREE | Qwen3 Coder"),
    ("cohere/north-mini-code:free",            "FREE | Cohere North Mini Code"),
    ("google/gemma-4-31b-it:free",             "FREE | Gemma 4 31B"),
    ("nvidia/nemotron-3-super-120b-a12b:free", "FREE | Nemotron 3 Super"),
    # Paid models.
    ("openai/gpt-4o-mini",                     "PAID | ChatGPT 4o mini - fast and cheap"),
    ("openai/gpt-4o",                          "PAID | ChatGPT 4o - more capable"),
    ("openai/gpt-4.1",                         "PAID | ChatGPT 4.1"),
    ("anthropic/claude-haiku-3-5",             "PAID | Claude Haiku - fast and cheap"),
    ("anthropic/claude-sonnet-4",              "PAID | Claude Sonnet"),
    ("anthropic/claude-opus-4",                "PAID | Claude Opus"),
    ("google/gemini-flash-1.5",                "PAID | Gemini Flash"),
    ("google/gemini-2.0-flash-001",            "PAID | Gemini 2.0 Flash"),
    ("deepseek/deepseek-chat",                 "PAID | DeepSeek V3"),
    ("qwen/qwen-2.5-72b-instruct",             "PAID | Qwen 2.5 72B"),
]

OPENAI_MODELS: list[tuple[str, str]] = [
    # Família 5 (top tier — lançada 2025-2026)
    ("gpt-5",           "GPT-5 — flagship (mais capaz)"),
    ("gpt-5-mini",      "GPT-5 mini — rápido e barato"),
    # Família 4.5 / 4.1
    ("gpt-4.5",         "GPT-4.5 (Orion) — fev/2025"),
    ("gpt-4.1",         "GPT-4.1 — abr/2025"),
    ("gpt-4.1-mini",    "GPT-4.1 mini — rápido"),
    # Família 4o
    ("gpt-4o",          "GPT-4o — multimodal"),
    ("gpt-4o-mini",     "GPT-4o mini — econômico"),
    ("gpt-4-turbo",     "GPT-4 Turbo — legado"),
    # Família o-series (raciocínio)
    ("o4-mini",         "o4-mini — raciocínio econômico"),
    ("o3",              "o3 — raciocínio profundo"),
    ("o3-mini",         "o3-mini — raciocínio rápido"),
    ("o1",              "o1 — raciocínio (dez/2024)"),
    # Custom — usuario digita nome livre
    ("__custom__",      ">> outro modelo (digitar nome)"),
    # gpt-3.5-turbo removido: descontinuado em jan/2025
]

# Modelos aceitos pelo backend Codex do ChatGPT (login via browser).
# O endpoint /backend-api/codex/responses só aceita modelos Codex — não GPT genérico.
CHATGPT_CODEX_MODELS: list[tuple[str, str]] = [
    ("codex-mini-latest", "Codex Mini — padrão do Codex CLI (recomendado)"),
    ("o4-mini",           "o4-mini — raciocínio, disponível em contas Pro"),
    ("o3-mini",           "o3-mini — raciocínio, disponível em contas Pro"),
    ("__custom__",        ">> outro modelo (digitar nome)"),
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
    # --- Gratuitos (sem billing) ---
    ("ollama",      "Ollama",           "GRATIS | Modelos locais (requer Ollama rodando)"),
    ("opencode",    "OpenCode Zen",     "GRATIS | Modelos via opencode.ai — sem API key"),
    ("groq",        "Groq",             "GRATIS | Llama 3.3 70B ultra-rápido (console.groq.com)"),
    ("github",      "GitHub Models",    "GRATIS | GPT-4o, Llama via GitHub (requer GITHUB_TOKEN)"),
    # --- Assinatura (usa conta ChatGPT, sem créditos de API) ---
    ("openai",      "ChatGPT (browser)", "ASSINA | Login com conta ChatGPT Plus/Pro — sem API key (experimental)"),
    # --- Pagos (requerem billing/API key) ---
    ("openai-api",  "OpenAI API Key",   "PAGO   | ChatGPT com API key (sk-...) — platform.openai.com"),
    ("anthropic",   "Anthropic",        "PAGO   | Claude Haiku, Sonnet, Opus"),
    ("gemini",      "Google Gemini",    "PAGO   | Gemini Flash, Pro (GEMINI_API_KEY)"),
    ("openrouter",  "OpenRouter",       "PAGO   | 200+ modelos: GPT, Claude, Gemini — 1 chave"),
    ("mistral",     "Mistral AI",       "PAGO   | Mistral Small/Medium/Large, Codestral"),
    ("xai",         "xAI Grok",         "PAGO   | Grok 3 — modelos da xAI/Elon Musk"),
    ("together",    "Together AI",      "PAGO   | Llama, Mistral, Qwen — hospedagem aberta"),
    ("deepseek",    "DeepSeek",         "PAGO   | DeepSeek V3 e R1 — China, preço baixo"),
    ("copilot",     "GitHub Copilot",   "PAGO   | Copilot API — requer 'bauer auth login -p copilot'"),
    ("custom",      "Custom",           "       | Qualquer endpoint OpenAI-compatible (LM Studio, vLLM…)"),
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
    """Exibe tabela numerada e retorna o id escolhido.

    - Numero da tabela -> id correspondente
    - Texto livre -> aceita como modelo customizado
    - Sentinel '__custom__' -> abre prompt secundario pedindo o nome
    - Enter vazio -> cancela
    """
    table = Table(title=title, show_lines=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("modelo / provider", style="cyan")
    table.add_column("descrição")

    for i, (id_, desc) in enumerate(items, 1):
        # Estiliza a linha de custom diferente
        display_id = id_ if id_ != "__custom__" else "[yellow]custom[/yellow]"
        table.add_row(str(i), display_id, desc)

    console.print(table)
    console.print(
        "[dim]Dica: digite o numero, ou o nome do modelo direto (ex: 'gpt-5.5'). "
        "[/dim][yellow]0[/yellow][dim] ou [/dim][yellow]voltar[/yellow][dim] para voltar.[/dim]"
    )

    raw = Prompt.ask(
        "[bold]Escolha[/bold] ([yellow]0[/yellow]=voltar, Enter cancela)",
        default="",
    ).strip()

    # Cancela / volta: Enter vazio, "0", ou palavras-chave de navegação.
    if not raw or raw.lower() in {"voltar", "v", "back", "sair", "cancelar", "q"} or raw == "0":
        return None

    chosen: str | None = None
    try:
        idx = int(raw) - 1
        if 0 <= idx < len(items):
            chosen = items[idx][0]
        else:
            chosen = raw
    except ValueError:
        # Digitou o nome direto (texto livre — aceita como customizado)
        chosen = raw

    # Sentinel "__custom__" -> abre prompt secundario pedindo nome do modelo
    if chosen == "__custom__":
        custom_name = Prompt.ask(
            "[bold]Nome exato do modelo[/bold] (ex: gpt-5.5, o4-mini-high, gpt-5-pro)",
            default="",
        ).strip()
        if not custom_name:
            console.print("[dim]Cancelado.[/dim]")
            return None
        console.print(f"[dim]Usando modelo customizado: [cyan]{custom_name}[/cyan][/dim]")
        return custom_name

    return chosen


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------

def _openrouter_desc(m: dict) -> str:
    if m.get("is_free"):
        ctx = m.get("context_window")
        ctx_s = f" · {ctx // 1000}k ctx" if ctx else ""
        return f"GRÁTIS{ctx_s}"
    cost = m.get("cost_in")
    cost_s = f"${cost}/M" if cost else "—"
    return f"PAGO · {cost_s} entrada"


# Teto de exibição para resultados de busca — o catálogo tem 300+ modelos
# pagos; sem teto, um termo genérico ("gpt") devolveria dezenas de linhas
# irrelevantes na mesma tabela numerada.
_OPENROUTER_SEARCH_LIMIT = 60


def _search_openrouter_model(catalog: list[dict]) -> str | None:
    """Busca por substring do ID no catálogo INTEIRO (sem o corte de 40 pagos)."""
    query = Prompt.ask(
        "[bold]Buscar[/bold] (parte do ID — ex: 'claude', 'llama', 'gpt-4')",
        default="",
    ).strip().lower()
    if not query:
        return _pick_openrouter_model()

    matches = [m for m in catalog if query in m["id"].lower()]
    if not matches:
        console.print(f"[yellow]Nenhum modelo com '{query}' no ID.[/yellow]")
        console.print(
            "[dim]Dica: o ID no OpenRouter segue o formato 'autor/modelo' "
            "(ex: 'anthropic/claude-sonnet-4') — tente um termo mais curto.[/dim]"
        )
        return _pick_openrouter_model()

    matches.sort(key=lambda m: (0 if m.get("is_free") else 1, m.get("cost_in") or 999.0))
    shown = matches[:_OPENROUTER_SEARCH_LIMIT]
    items: list[tuple[str, str]] = (
        [(m["id"], _openrouter_desc(m)) for m in shown]
        + [("__custom__", ">> digitar ID do modelo")]
    )
    console.print(
        f"[dim]{len(matches)} resultado(s) para '{query}'"
        + (f" — mostrando os {len(shown)} mais baratos" if len(matches) > len(shown) else "")
        + "[/dim]"
    )
    return _pick_from_list(items, f"Busca OpenRouter: '{query}'")


def _pick_openrouter_model() -> str | None:
    """Busca catálogo live do OpenRouter e exibe seletor interativo.

    Mostra gratuitos (todos) + pagos (top 40 por custo) — a curadoria existe
    porque o catálogo completo tem 300+ modelos e uma tabela numerada com
    todos seria inutilizável. Quem procura um modelo específico fora desse
    recorte (ex.: visto em openrouter.ai/models) usa a opção 'buscar por
    nome', que varre o catálogo INTEIRO — sem o corte de 40.
    """
    console.print("[dim]Buscando catálogo OpenRouter…[/dim]")
    try:
        from .models_dev import fetch_openrouter_catalog
        catalog = fetch_openrouter_catalog()
    except Exception as exc:
        console.print(f"[yellow]Não foi possível buscar catálogo live: {exc}[/yellow]")
        console.print("[dim]Usando lista curada.[/dim]")
        return _pick_from_list(OPENROUTER_MODELS, "Modelos OpenRouter (lista curada)")

    free = [m for m in catalog if m.get("is_free")]
    paid = [m for m in catalog if not m.get("is_free")]

    # Ordena pagos por custo ascendente, limita a 40
    paid.sort(key=lambda m: m.get("cost_in") or 999.0)
    paid_top = paid[:40]

    items: list[tuple[str, str]] = (
        [(m["id"], _openrouter_desc(m)) for m in free]
        + [(m["id"], _openrouter_desc(m)) for m in paid_top]
        + [("__search__", ">> buscar por nome (ex: 'claude', 'llama')")]
        + [("__custom__", ">> digitar ID do modelo exato")]
    )

    console.print(
        f"[dim]{len(free)} gratuitos · {len(paid_top)} pagos mostrados "
        f"(de {len(catalog)} no catálogo, {len(paid)} pagos no total)[/dim]"
    )
    chosen = _pick_from_list(items, "Modelos OpenRouter")
    if chosen == "__search__":
        return _search_openrouter_model(catalog)
    return chosen


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
        console.print("[dim]Cancelado — voltando ao chat.[/dim]")
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
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        internal_provider = "opencode"

    elif provider_id == "openrouter":
        model_name = _pick_openrouter_model()
        if not model_name:
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        env_key, env_label, link = "OPENROUTER_API_KEY", "OPENROUTER_API_KEY", "https://openrouter.ai/keys"
        env_vars, config_extra = _ask_api_key(env_path, env_key, env_label, link)
        internal_provider = "openrouter"

    elif provider_id == "openai":
        # Login via browser — usa a assinatura ChatGPT (backend Responses,
        # igual ao Codex CLI). Billa na conta ChatGPT, sem créditos de API.
        from .auth import AuthManager
        auth = AuthManager()
        # Reusa token salvo se ainda válido — só reautentica se faltar ou expirar.
        existing = auth.store.load("openai")
        token = None
        if existing and not existing.is_expired and existing.access_token:
            console.print(
                "\n[green]✓ Já autenticado com conta ChatGPT[/green] "
                "[dim](token salvo, sem novo login)[/dim]"
            )
            token = existing
            auth.close()
        elif existing and existing.refresh_token:
            # Token expirado mas renovável — refresca sem abrir o browser.
            console.print("\n[dim]Token ChatGPT expirado. Renovando...[/dim]")
            refreshed = auth.refresh("openai")
            if refreshed:
                console.print("[green]✓ Token ChatGPT renovado[/green] [dim](sem novo login)[/dim]")
                token = refreshed
                auth.close()
            else:
                console.print("[yellow]Não foi possível renovar — fazendo login novo.[/yellow]")
        if token is None:
            console.print(
                "\n[cyan]ChatGPT (login browser)[/cyan] — abrirá o browser para você logar com sua conta ChatGPT.\n"
                "[dim]Usa sua assinatura ChatGPT Plus/Pro — sem API key (sk-...).[/dim]\n"
                "[yellow]Experimental:[/yellow] [dim]depende do backend do ChatGPT; requer assinatura ativa.[/dim]\n"
            )
            try:
                token = auth.login_oauth("openai")   # vai direto ao browser
                auth.close()
                _acct = token.extra.get("chatgpt_account_id") if hasattr(token, "extra") else ""
                console.print("[green]✓ Autenticado com conta ChatGPT[/green]")
                if _acct:
                    console.print(f"[dim]  account_id: {_acct}[/dim]\n")
                else:
                    console.print(
                        "[yellow]  Aviso:[/yellow] [dim]account_id não encontrado no token — "
                        "o backend pode recusar. Confirme assinatura ChatGPT ativa.[/dim]\n"
                    )
            except Exception as _auth_err:
                console.print(f"[red]Erro na autenticação:[/red] {_auth_err}")
                console.print("[dim]Tente manualmente: bauer auth login -p openai[/dim]")
                return

        console.print(
            "[dim]Modelos disponíveis via assinatura ChatGPT Plus/Pro (sem API key).[/dim]"
        )
        model_name = _pick_from_list(CHATGPT_CODEX_MODELS, "Modelos ChatGPT (assinatura)")
        if not model_name:
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        internal_provider = "openai"

    elif provider_id == "openai-api":
        # API Key OpenAI — sk-...
        model_name = _pick_from_list(OPENAI_MODELS, "Modelos OpenAI (API Key)")
        if not model_name:
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        env_key, env_label, link = "OPENAI_API_KEY", "OPENAI_API_KEY", "https://platform.openai.com/api-keys"
        env_vars, config_extra = _ask_api_key(env_path, env_key, env_label, link)
        config_extra = config_extra or {}
        config_extra.setdefault("openai", {})["host"] = "https://api.openai.com"
        internal_provider = "openai"  # ambos mapeiam para o mesmo provider interno

    elif provider_id == "anthropic":
        model_name = _pick_from_list(ANTHROPIC_MODELS, "Modelos Anthropic (Claude)")
        if not model_name:
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        env_vars, config_extra = _ask_api_key(
            env_path, "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY",
            "https://console.anthropic.com/settings/keys",
        )
        internal_provider = "anthropic"

    elif provider_id == "gemini":
        model_name = _pick_from_list(GEMINI_MODELS, "Modelos Google Gemini")
        if not model_name:
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        env_vars, config_extra = _ask_api_key(
            env_path, "GEMINI_API_KEY", "GEMINI_API_KEY (ou GOOGLE_API_KEY)",
            "https://aistudio.google.com/app/apikey",
        )
        internal_provider = "gemini"

    elif provider_id == "groq":
        model_name = _pick_from_list(GROQ_MODELS, "Modelos Groq")
        if not model_name:
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        env_vars, config_extra = _ask_api_key(
            env_path, "GROQ_API_KEY", "GROQ_API_KEY",
            "https://console.groq.com/keys",
        )
        internal_provider = "groq"

    elif provider_id == "mistral":
        model_name = _pick_from_list(MISTRAL_MODELS, "Modelos Mistral AI")
        if not model_name:
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        env_vars, config_extra = _ask_api_key(
            env_path, "MISTRAL_API_KEY", "MISTRAL_API_KEY",
            "https://console.mistral.ai/api-keys",
        )
        internal_provider = "mistral"

    elif provider_id == "xai":
        model_name = _pick_from_list(XAI_MODELS, "Modelos xAI (Grok)")
        if not model_name:
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        env_vars, config_extra = _ask_api_key(
            env_path, "XAI_API_KEY", "XAI_API_KEY",
            "https://console.x.ai",
        )
        internal_provider = "xai"

    elif provider_id == "together":
        model_name = _pick_from_list(TOGETHER_MODELS, "Modelos Together AI")
        if not model_name:
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        env_vars, config_extra = _ask_api_key(
            env_path, "TOGETHER_API_KEY", "TOGETHER_API_KEY",
            "https://api.together.xyz/settings/api-keys",
        )
        internal_provider = "together"

    elif provider_id == "deepseek":
        model_name = _pick_from_list(DEEPSEEK_MODELS, "Modelos DeepSeek")
        if not model_name:
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        env_vars, config_extra = _ask_api_key(
            env_path, "DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY",
            "https://platform.deepseek.com/api-keys",
        )
        internal_provider = "deepseek"

    elif provider_id == "github":
        model_name = _pick_from_list(GITHUB_MODELS, "Modelos GitHub Models")
        if not model_name:
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
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
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        internal_provider = "copilot"

    elif provider_id == "custom":
        host = Prompt.ask("[bold]Host do servidor[/bold]", default="http://localhost:1234").strip()
        model_name = Prompt.ask("[bold]Nome do modelo[/bold]").strip()
        if not model_name:
            console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
            return run_model_switcher(config_path)
        use_key = Confirm.ask("O servidor requer API key?", default=False)
        if use_key:
            key = Prompt.ask("[bold]API key[/bold]", password=True).strip()
            if key:
                _write_env_key(env_path, "OPENAI_API_KEY", key)
                console.print(f"[green]OPENAI_API_KEY salvo em {env_path}[/green]")
        config_extra = {"openai": {"host": host}}

    if not model_name:
        console.print("[dim]↩ Voltando à lista de providers…[/dim]\n")
        return run_model_switcher(config_path)

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


_EXPECTED_KEY_PREFIXES = {
    "OPENAI_API_KEY":     ("sk-",     "OpenAI"),
    "ANTHROPIC_API_KEY":  ("sk-ant-", "Anthropic"),
    "GROQ_API_KEY":       ("gsk_",    "Groq"),
    "MISTRAL_API_KEY":    (None,      "Mistral"),       # sem prefixo fixo
    "XAI_API_KEY":        ("xai-",    "xAI"),
    "TOGETHER_API_KEY":   (None,      "Together"),
    "DEEPSEEK_API_KEY":   ("sk-",     "DeepSeek"),
    "GEMINI_API_KEY":     (None,      "Gemini"),
    "OPENROUTER_API_KEY": ("sk-or-",  "OpenRouter"),
}

_KNOWN_PREFIXES = [("sk-ant-", "Anthropic"), ("sk-or-", "OpenRouter"),
                    ("gsk_", "Groq"), ("xai-", "xAI"), ("sk-", "OpenAI/DeepSeek")]


def _detect_key_provider(key: str) -> str:
    """Retorna nome do provider que parece dono da chave, ou ''."""
    for prefix, name in _KNOWN_PREFIXES:
        if key.startswith(prefix):
            return name
    return ""


def _ask_api_key(
    env_path: Path,
    env_key: str,
    env_label: str,
    link: str,
) -> tuple[dict[str, str], dict | None]:
    """Verifica se a chave já existe; se não, pede ao usuário.

    - Se existir, mostra os primeiros chars + nome do provider detectado
    - Se o prefixo nao bate com o esperado, AVISA e oferece substituir
    - Sempre permite ao usuario digitar uma nova chave (Enter para manter)

    Retorna (env_vars_para_salvar, None).
    """
    existing = _read_env(env_path).get(env_key, "")
    expected_prefix, expected_name = _EXPECTED_KEY_PREFIXES.get(env_key, (None, env_label))

    if existing:
        detected = _detect_key_provider(existing)
        prefix_show = existing[:6] + "..." + existing[-4:] if len(existing) > 12 else existing[:6] + "..."

        # Verifica se a chave parece do provider esperado
        mismatch = False
        if expected_prefix and not existing.startswith(expected_prefix):
            mismatch = True

        if mismatch:
            console.print(
                f"\n[red]⚠ AVISO:[/red] {env_label} no .env nao parece ser do {expected_name}.\n"
                f"  Chave atual: [yellow]{prefix_show}[/yellow]"
                + (f" (parece ser do [cyan]{detected}[/cyan])" if detected else "")
                + f"\n  Esperado:    prefixo [green]{expected_prefix}[/green] ({expected_name})\n"
                f"  Link p/ obter chave correta: {link}\n"
            )
        else:
            console.print(
                f"[dim]{env_label} ja configurado: {prefix_show}"
                + (f" ({detected})" if detected else "")
                + "[/dim]"
            )

        # Pergunta se quer trocar (com default sensato baseado no mismatch)
        try:
            from rich.prompt import Confirm as _Confirm
            default_replace = bool(mismatch and getattr(console, "is_terminal", False))
            replace = _Confirm.ask(
                "Substituir por uma nova chave?",
                default=default_replace,
            )
        except Exception:
            replace = default_replace

        if not replace:
            return {}, None
        # cai pra leitura abaixo

    console.print(f"\n[yellow]Cole sua API key do {expected_name}: {link}[/yellow]")
    console.print(
        "[dim]Cada caractere vai aparecer como '*'. "
        "Cole com Ctrl+V (ou Shift+Insert) e aperte Enter.[/dim]"
    )

    # Tres camadas de fallback (do melhor pro pior):
    # 1. prompt_toolkit.prompt(is_password=True) — mostra '*' por caractere (alvo)
    # 2. Rich Prompt(password=True) — esconde tudo, mas funciona em pipes/CI
    # 3. getpass — fallback extremo
    key = ""
    try:
        from prompt_toolkit import prompt as _pt_prompt
        key = _pt_prompt(f"{env_label} (Enter para pular): ", is_password=True).strip()
    except Exception:
        try:
            key = Prompt.ask(
                f"[bold]{env_label}[/bold] (Enter para pular)",
                password=True,
                default="",
                show_default=False,
            ).strip()
        except Exception:
            import getpass as _getpass
            key = _getpass.getpass(f"{env_label} (sem echo, Enter para pular): ").strip()

    if not key:
        console.print(f"[dim]Sem chave. Adicione {env_key}=sua-chave no .env depois.[/dim]")
        return {}, None

    # Detecta chave colada duas vezes (paste duplo em getpass sem feedback)
    # Estrategia: se o prefixo esperado aparece 2x e a primeira metade == segunda metade
    if expected_prefix and key.count(expected_prefix) >= 2:
        second_pos = key.index(expected_prefix, 1)
        half1 = key[:second_pos]
        half2 = key[second_pos:]
        if half1 == half2:
            console.print(
                f"[yellow]⚠ Chave parece colada duas vezes "
                f"(detectei '{expected_prefix}' aparecendo {key.count(expected_prefix)}x).[/yellow]\n"
                f"[green]Cortei automaticamente para a primeira ocorrencia ({len(half1)} chars).[/green]"
            )
            key = half1

    # Valida prefixo da nova chave; permite forcar mesmo assim
    if expected_prefix and not key.startswith(expected_prefix):
        detected_new = _detect_key_provider(key)
        console.print(
            f"[yellow]⚠ Esta chave nao tem prefixo {expected_prefix} esperado para {expected_name}.[/yellow]"
            + (f" Parece ser do {detected_new}." if detected_new else "")
        )
        try:
            from rich.prompt import Confirm as _Confirm
            if not _Confirm.ask("Salvar mesmo assim?", default=False):
                console.print("[dim]Chave nao salva.[/dim]")
                return {}, None
        except Exception:
            pass

    return {env_key: key}, None
