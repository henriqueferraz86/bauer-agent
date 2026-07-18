"""Helpers de runtime da CLI: construcao de client/router/shell, carregamento
de config, selecao de modelo e bootstrap de gateway.

Camada compartilhada (P4 Parte 1): cli.py e os grupos de comando pesados
(agent/serve/orchestrate/models/tools/gateway) importam destes helpers.
Re-exportados em cli.py para preservar `from bauer.cli import _load_or_die` etc.
"""

from __future__ import annotations

from ..config_loader import ConfigError
from ..config_loader import load_config
from ..model_registry import ModelRegistryError
from ..model_registry import load_registry
from ..ollama_client import OllamaClient
from ..preflight import run_doctor
from ..runtime_state import read_state
from ..runtime_state import write_state
from ..shell_runner import ShellRunner
from ..tool_router import ToolRouter
from pathlib import Path
from rich.console import Console
import typer

from ._common import console


def _load_or_die(config_path: Path, models_path: Path):
    # Se o caminho passado não existe, tenta o default global ~/.bauer/config.yaml
    if not config_path.exists():
        from ..paths import config_path as _default_cfg
        fallback = _default_cfg()
        if fallback.exists():
            config_path = fallback
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
    from ..preflight import _CLOUD_CONTEXT_DEFAULTS, _CLOUD_CONTEXT_FALLBACK

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

    # G11: credential pool overlay — keychain → encrypted file → config/env fallback
    try:
        from ..credential_pool import _cpool as _get_cpool
        _pool = _get_cpool()
    except Exception:
        _pool = None

    def _key(provider_name: str, raw: str) -> str:
        if _pool is None:
            return raw
        return _pool.get(provider_name, fallback=raw)

    # Verifica se há token autenticado via bauer auth
    try:
        from ..auth import AuthManager
        auth = AuthManager()
        token = auth.store.load(provider) or auth.store.load(f"{provider}-api")
        if token:
            # Verifica se é JWT do Codex (não serve como API key)
            if token.extra.get("type") == "jwt":
                console.print(
                    "[yellow]Aviso:[/yellow] Token do Codex CLI detectado.\n"
                    "Este token é para uso exclusivo do Codex CLI.\n"
                    "Para usar a API, insira uma API key: [bold]bauer auth login -p openai-api[/bold]"
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
                from ..chatgpt_backend import ChatGPTBackendClient, DEFAULT_CHATGPT_BASE
                _base = getattr(cfg.openai, "chatgpt_base_url", "") or DEFAULT_CHATGPT_BASE
                return ChatGPTBackendClient(
                    access_token=token.access_token,
                    account_id=token.extra.get("chatgpt_account_id") or "",
                    base_url=_base,
                    timeout_seconds=cfg.openai.timeout_seconds,
                    model=cfg.model.name,
                )
            if not token.extra.get("type") == "jwt":
                from ..openai_client import OpenAIClient
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
        from ..openai_client import OpenAIClient
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
        from ..openai_client import OpenAIClient
        # OpenRouter usa OpenAI wire protocol com headers extras de identificação
        extra_headers = {}
        if cfg.openrouter.http_referer:
            extra_headers["HTTP-Referer"] = cfg.openrouter.http_referer
        if cfg.openrouter.x_title:
            extra_headers["X-Title"] = cfg.openrouter.x_title
        return OpenAIClient(
            host="https://openrouter.ai/api",
            timeout_seconds=cfg.openrouter.timeout_seconds,
            api_key=_key("openrouter", cfg.openrouter.api_key),
            model=cfg.model.name,
            extra_headers=extra_headers,
        )

    if provider == "groq":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.groq.com/openai",
            timeout_seconds=cfg.groq.timeout_seconds,
            api_key=_key("groq", cfg.groq.api_key),
            model=cfg.model.name,
        )

    if provider == "mistral":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.mistral.ai",
            timeout_seconds=cfg.mistral.timeout_seconds,
            api_key=_key("mistral", cfg.mistral.api_key),
            model=cfg.model.name,
        )

    if provider == "xai":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.x.ai",
            timeout_seconds=cfg.xai.timeout_seconds,
            api_key=_key("xai", cfg.xai.api_key),
            model=cfg.model.name,
        )

    if provider == "together":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.together.xyz",
            timeout_seconds=cfg.together.timeout_seconds,
            api_key=_key("together", cfg.together.api_key),
            model=cfg.model.name,
        )

    if provider == "deepseek":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.deepseek.com",
            timeout_seconds=cfg.deepseek.timeout_seconds,
            api_key=_key("deepseek", cfg.deepseek.api_key),
            model=cfg.model.name,
        )

    if provider == "gemini":
        from ..openai_client import OpenAIClient
        # Google expõe endpoint OpenAI-compatible
        # Host já contém /v1beta/openai — não adicionar /v1/ extra
        return OpenAIClient(
            host="https://generativelanguage.googleapis.com/v1beta/openai",
            timeout_seconds=cfg.gemini.timeout_seconds,
            api_key=_key("gemini", cfg.gemini.api_key),
            model=cfg.model.name,
            chat_path="/chat/completions",
        )

    if provider == "anthropic":
        from ..anthropic_client import AnthropicClient
        return AnthropicClient(
            api_key=_key("anthropic", cfg.anthropic.api_key),
            timeout_seconds=cfg.anthropic.timeout_seconds,
            api_version=cfg.anthropic.api_version,
            model=cfg.model.name,
        )

    if provider == "azure":
        from ..openai_client import OpenAIClient
        # Azure usa api-key header em vez de Authorization: Bearer
        endpoint = cfg.azure.endpoint.rstrip("/")
        deployment = cfg.azure.deployment or cfg.model.name
        base_url = f"{endpoint}/openai/deployments/{deployment}"
        return OpenAIClient(
            host=base_url,
            timeout_seconds=cfg.azure.timeout_seconds,
            api_key=_key("azure", cfg.azure.api_key),
            model=deployment,
            extra_headers={
                "api-key": cfg.azure.api_key,
                "x-ms-useragent": "bauer-agent/1.0",
            },
            api_version=cfg.azure.api_version,
        )

    if provider == "github":
        from ..openai_client import OpenAIClient
        # GitHub Models: endpoint sem /v1/ prefix
        # POST https://models.inference.ai.azure.com/chat/completions
        return OpenAIClient(
            host="https://models.inference.ai.azure.com",
            timeout_seconds=cfg.github.timeout_seconds,
            api_key=_key("github", cfg.github.token),
            model=cfg.model.name,
            chat_path="/chat/completions",
            extra_headers={
                "X-GitHub-Api-Version": "2023-07-07",
            },
        )

    if provider == "copilot":
        from ..openai_client import OpenAIClient
        # GitHub Copilot: endpoint sem /v1/ prefix
        # POST https://api.githubcopilot.com/chat/completions
        return OpenAIClient(
            host="https://api.githubcopilot.com",
            timeout_seconds=cfg.copilot.timeout_seconds,
            api_key=_key("copilot", cfg.copilot.token),
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
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.cohere.com/compatibility",
            timeout_seconds=cfg.cohere.timeout_seconds,
            api_key=_key("cohere", cfg.cohere.api_key),
            model=cfg.model.name,
        )

    if provider == "perplexity":
        from ..openai_client import OpenAIClient
        # Perplexity não usa /v1/ prefix — POST direto em /chat/completions
        return OpenAIClient(
            host="https://api.perplexity.ai",
            timeout_seconds=cfg.perplexity.timeout_seconds,
            api_key=_key("perplexity", cfg.perplexity.api_key),
            model=cfg.model.name,
            chat_path="/chat/completions",
        )

    if provider == "fireworks":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.fireworks.ai/inference",
            timeout_seconds=cfg.fireworks.timeout_seconds,
            api_key=_key("fireworks", cfg.fireworks.api_key),
            model=cfg.model.name,
        )

    if provider == "huggingface":
        from ..openai_client import OpenAIClient
        host = cfg.huggingface.host.rstrip("/")
        # Host padrão já inclui /v1 — usar chat_path para não duplicar
        if host.endswith("/v1"):
            return OpenAIClient(
                host=host,
                timeout_seconds=cfg.huggingface.timeout_seconds,
                api_key=_key("huggingface", cfg.huggingface.api_key),
                model=cfg.model.name,
                chat_path="/chat/completions",
            )
        return OpenAIClient(
            host=host,
            timeout_seconds=cfg.huggingface.timeout_seconds,
            api_key=_key("huggingface", cfg.huggingface.api_key),
            model=cfg.model.name,
        )

    if provider == "cerebras":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.cerebras.ai",
            timeout_seconds=cfg.cerebras.timeout_seconds,
            api_key=_key("cerebras", cfg.cerebras.api_key),
            model=cfg.model.name,
        )

    if provider == "sambanova":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.sambanova.ai",
            timeout_seconds=cfg.sambanova.timeout_seconds,
            api_key=_key("sambanova", cfg.sambanova.api_key),
            model=cfg.model.name,
        )

    if provider == "nvidia":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://integrate.api.nvidia.com",
            timeout_seconds=cfg.nvidia.timeout_seconds,
            api_key=_key("nvidia", cfg.nvidia.api_key),
            model=cfg.model.name,
        )

    if provider == "lmstudio":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host=cfg.lmstudio.host,
            timeout_seconds=cfg.lmstudio.timeout_seconds,
            api_key=_key("lmstudio", cfg.lmstudio.api_key) or "lm-studio",
            model=cfg.model.name,
        )

    if provider == "databricks":
        from ..openai_client import OpenAIClient
        host = cfg.databricks.host.rstrip("/")
        # Databricks serving-endpoints usa /chat/completions sem /v1
        return OpenAIClient(
            host=f"{host}/serving-endpoints",
            timeout_seconds=cfg.databricks.timeout_seconds,
            api_key=_key("databricks", cfg.databricks.api_key),
            model=cfg.model.name,
            chat_path="/chat/completions",
            extra_headers={"Authorization": f"Bearer {cfg.databricks.api_key}"},
        )

    if provider == "moonshot":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.moonshot.cn",
            timeout_seconds=cfg.moonshot.timeout_seconds,
            api_key=_key("moonshot", cfg.moonshot.api_key),
            model=cfg.model.name,
        )

    if provider == "alibaba":
        from ..openai_client import OpenAIClient
        # DashScope endpoint já inclui /compatible-mode (sem /v1 adicional via host)
        return OpenAIClient(
            host="https://dashscope.aliyuncs.com/compatible-mode",
            timeout_seconds=cfg.alibaba.timeout_seconds,
            api_key=_key("alibaba", cfg.alibaba.api_key),
            model=cfg.model.name,
        )

    if provider == "vertex":
        from ..openai_client import OpenAIClient
        region = cfg.vertex.region or "us-central1"
        project = cfg.vertex.project_id
        vertex_host = (
            f"https://{region}-aiplatform.googleapis.com/v1beta1"
            f"/projects/{project}/locations/{region}/endpoints/openapi"
        )
        return OpenAIClient(
            host=vertex_host,
            timeout_seconds=cfg.vertex.timeout_seconds,
            api_key=_key("vertex", cfg.vertex.access_token),
            model=cfg.model.name,
            chat_path="/chat/completions",
        )

    if provider in ("openai", "custom"):
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host=cfg.openai.host,
            timeout_seconds=cfg.openai.timeout_seconds,
            api_key=_key("openai", cfg.openai.api_key),
            model=cfg.model.name,
        )

    # ── G16a: new providers ────────────────────────────────────────────────
    if provider == "replicate":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.replicate.com",
            timeout_seconds=cfg.replicate.timeout_seconds,
            api_key=_key("replicate", cfg.replicate.api_key),
            model=cfg.model.name,
        )

    if provider == "novita":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.novita.ai/v3/openai",
            timeout_seconds=cfg.novita.timeout_seconds,
            api_key=_key("novita", cfg.novita.api_key),
            model=cfg.model.name,
            chat_path="/chat/completions",
        )

    if provider == "ai21":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.ai21.com/studio",
            timeout_seconds=cfg.ai21.timeout_seconds,
            api_key=_key("ai21", cfg.ai21.api_key),
            model=cfg.model.name,
        )

    if provider == "anyscale":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.endpoints.anyscale.com",
            timeout_seconds=cfg.anyscale.timeout_seconds,
            api_key=_key("anyscale", cfg.anyscale.api_key),
            model=cfg.model.name,
        )

    if provider == "featherless":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.featherless.ai",
            timeout_seconds=cfg.featherless.timeout_seconds,
            api_key=_key("featherless", cfg.featherless.api_key),
            model=cfg.model.name,
        )

    if provider == "hyperbolic":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.hyperbolic.xyz",
            timeout_seconds=cfg.hyperbolic.timeout_seconds,
            api_key=_key("hyperbolic", cfg.hyperbolic.api_key),
            model=cfg.model.name,
        )

    if provider == "inference":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.inference.net",
            timeout_seconds=cfg.inference.timeout_seconds,
            api_key=_key("inference", cfg.inference.api_key),
            model=cfg.model.name,
        )

    if provider == "ncompass":
        from ..openai_client import OpenAIClient
        return OpenAIClient(
            host="https://api.ncompass.tech",
            timeout_seconds=cfg.ncompass.timeout_seconds,
            api_key=_key("ncompass", cfg.ncompass.api_key),
            model=cfg.model.name,
        )

    if provider == "cloudflare":
        from ..openai_client import OpenAIClient
        account_id = cfg.cloudflare.account_id
        cf_host = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
            if account_id
            else "https://api.cloudflare.com/client/v4/accounts/ACCOUNT_ID/ai/v1"
        )
        return OpenAIClient(
            host=cf_host,
            timeout_seconds=cfg.cloudflare.timeout_seconds,
            api_key=_key("cloudflare", cfg.cloudflare.api_key),
            model=cfg.model.name,
            chat_path="/chat/completions",
        )

    if provider == "lepton":
        from ..openai_client import OpenAIClient
        subdomain = cfg.lepton.subdomain or cfg.model.name.replace("/", "-").replace(".", "-")
        lepton_host = f"https://{subdomain}.lepton.run/api/v1"
        return OpenAIClient(
            host=lepton_host,
            timeout_seconds=cfg.lepton.timeout_seconds,
            api_key=_key("lepton", cfg.lepton.api_key),
            model=cfg.model.name,
            chat_path="/chat/completions",
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
        extra_allowed_commands=cfg.tools.extra_allowed_commands,
    )


# Toolset enxuto aplicado automaticamente a modelos locais com contexto pequeno
# (as ~79 tools = ~14k tokens estouram e o Ollama trunca o prompt em silêncio).
# Cobre arquivos, shell, web, busca em código e utilitários — os fluxos mais
# comuns. O usuário sobrescreve com tools.tool_allowlist explícito.
_LOCAL_DEFAULT_ALLOWLIST = [
    "read_file", "write_file", "list_dir", "run_command",
    "web_search", "web_fetch", "search_text", "glob_files",
    "datetime_now", "calculate", "memory", "todo",
    "app_factory_init", "app_factory_status",
]
# Abaixo deste contexto, expor todas as tools é arriscado em modelo local.
_AUTO_SLIM_CONTEXT_THRESHOLD = 16384


def _effective_tool_allowlist(cfg) -> "list[str] | None":
    """Decide o tool_allowlist efetivo do router.

    Precedência:
      1. tools.tool_allowlist explícito no config → respeitado como está.
      2. modelo LOCAL (ollama) + contexto pequeno + auto_tool_allowlist ligado
         → aplica _LOCAL_DEFAULT_ALLOWLIST (evita o truncamento silencioso).
      3. caso contrário → None (todas as tools).
    """
    if cfg is None:
        return None
    explicit = list(cfg.tools.tool_allowlist or ())
    if explicit:
        return explicit
    if not getattr(cfg.tools, "auto_tool_allowlist", True):
        return None
    provider = (getattr(cfg.model, "provider", "") or "").lower()
    ctx = int(getattr(cfg.model, "requested_context", 0) or 0)
    if provider == "ollama" and 0 < ctx < _AUTO_SLIM_CONTEXT_THRESHOLD:
        console.print(
            f"[dim]Modelo local + contexto {ctx}: limitando a {len(_LOCAL_DEFAULT_ALLOWLIST)} "
            "tools essenciais para o prompt não truncar. Defina tools.tool_allowlist "
            "ou tools.auto_tool_allowlist=false para mudar.[/dim]"
        )
        return list(_LOCAL_DEFAULT_ALLOWLIST)
    return None


def _build_router(cfg, workspace: Path, llm_client=None, session_id: str = "") -> ToolRouter:
    """Cria ToolRouter com shell_runner, web e llm_client a partir da config.

    Se llm_client não for passado, constrói um a partir da config (best-effort).
    Isso garante que as tools que dependem do modelo — vision_analyze,
    video_analyze, browser_vision, mixture_of_agents, delegate_task — tenham
    'cérebro' em TODOS os fluxos da CLI, não só no chat/agent. Sem isso, esses
    comandos caíam com 'llm_client não configurado'. Construir o client não faz
    rede (só instancia o objeto); falha silenciosa → degrada para None.

    Helper ÚNICO/compartilhado para ler shell_enabled/web_enabled do config —
    qualquer caller que monte ToolRouter por fora disso (ex.: o gateway antes
    desta função ganhar o parâmetro session_id) fica com essas flags sempre
    False, ignorando o config.yaml silenciosamente.
    """
    if llm_client is None and cfg is not None:
        try:
            llm_client = _build_client(cfg)
        except Exception:
            llm_client = None  # best-effort: tools de modelo degradam com erro claro
    # G18.4: cliente multimodal dedicado SÓ quando auxiliary.vision_model foi
    # configurado explicitamente (provider ou model não-vazio). Senão fica None
    # e o router cai no llm_client principal (com check de capability).
    vision_client = None
    if cfg is not None:
        try:
            _vm = getattr(getattr(cfg, "auxiliary", None), "vision_model", None)
            if _vm is not None and (getattr(_vm, "provider", "") or getattr(_vm, "model", "")):
                from ..auxiliary_client import get_text_auxiliary_client
                vision_client, _ = get_text_auxiliary_client("vision_model", cfg)
        except Exception:
            vision_client = None
    shell_runner = _build_shell_runner(cfg, workspace)
    web_enabled = cfg.tools.web_enabled if cfg is not None else False
    web_config = cfg.web if cfg is not None else None
    import os as _os
    postiz_api_key = _os.environ.get("POSTIZ_API_KEY", "").strip() or (
        cfg.postiz.api_key.strip() if cfg is not None else ""
    )
    postiz_api_url = cfg.postiz.api_url if cfg is not None else ""
    return ToolRouter(
        workspace,
        shell_runner=shell_runner,
        web_enabled=web_enabled,
        web_config=web_config,
        llm_client=llm_client,
        vision_client=vision_client,
        model_name=cfg.model.name if cfg is not None else "",
        max_tool_calls=cfg.tools.max_tool_calls if cfg is not None else 500,
        session_id=session_id,
        tool_allowlist=_effective_tool_allowlist(cfg),
        postiz_api_key=postiz_api_key,
        postiz_api_url=postiz_api_url,
    )


def build_fallback_clients(cfg) -> list:
    """Constrói clientes de fallback de cfg.model.fallback_models (sem console).

    Versão compartilhada (gateway/serve) do que o CLI faz em
    agent_cmd._build_fallback_clients: para cada fallback, monta um cfg
    derivado (mesmas credenciais, provider/model trocados, sem recursão) e um
    client. Dedup contra o primário e entradas repetidas. Falha de montagem é
    tolerável (pula). Retorna lista de ``(client, model_name)``.
    """
    import logging as _logging
    _log = _logging.getLogger("bauer.fallback")
    clients: list = []
    if cfg is None:
        return clients
    fb_models = getattr(cfg.model, "fallback_models", []) or []
    seen: set = {(cfg.model.provider, cfg.model.name)}
    for fb in fb_models:
        prov = fb.provider if hasattr(fb, "provider") else (fb or {}).get("provider", "")
        name = fb.name if hasattr(fb, "name") else (fb or {}).get("name", "")
        if not prov or not name or (prov, name) in seen:
            continue
        seen.add((prov, name))
        try:
            raw = cfg.model_dump()
            raw["model"]["provider"] = prov
            raw["model"]["name"] = name
            raw["model"]["fallback_models"] = []
            raw["model"]["fallback_providers"] = []
            from ..config_loader import BauerConfig as _BauerCfg
            fb_cfg = _BauerCfg(**raw)
            from ..env_loader import apply_env_to_config as _aenv
            _aenv(fb_cfg)
            clients.append((_build_client(fb_cfg), name))
        except Exception as exc:  # noqa: BLE001 — fallback mal configurado é tolerável
            _log.debug("build_fallback_clients: pulou %s/%s: %s", prov, name, exc)
    return clients


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
    from ..model_registry import contexto_seguro
    from ..memory_manager import MemoryManager

    info = reg.get(model_name)
    if info is None:
        return model_name

    # Verifica histórico de MODEL_EXPERIENCE antes da RAM
    try:
        from ..learning_engine import LearningEngine
        engine = LearningEngine(memory_dir)
        exps = engine.load_experience()
        from ..machine_id import machine_id as get_machine_id
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
    from ..ollama_client import OllamaError as _OllamaError

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


def _start_gateway_thread_cli(
    bauer_url: str,
    host: str,
    port: int,
    api_key: str,
    console: Console,
) -> None:
    """Inicia o gateway WebSocket em daemon thread e imprime status."""
    try:
        from ..gateway import start_gateway_thread
        start_gateway_thread(bauer_url=bauer_url, host=host, port=port, api_key=api_key)
        console.print(
            f"[dim]  Claw3D Gateway: [bold]ws://{host}:{port}[/bold] "
            f"(adapterType=bauer) — configure no Claw3D[/dim]"
        )
    except RuntimeError as exc:
        console.print(f"[yellow]  Gateway WebSocket indisponivel: {exc}[/yellow]")


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


def heuristic_route_kit(cfg):
    """(profiles, client_factory) para o roteamento heurístico por turno na CLI.

    Espelho do bloco de routing do serve (server.py `_client_for_profile`):
    quando ``model.router_enabled=True`` e há ``model.profiles``, retorna os
    profiles resolvidos e uma factory ``(provider) -> client|None`` com cache
    por provider. ``(None, None)`` quando o routing está desligado — o caller
    segue o caminho de sempre sem nenhum custo extra.
    """
    try:
        enabled = bool(getattr(cfg.model, "router_enabled", False))
        if not enabled:
            return None, None
        from ..model_router import profiles_from_config
        profiles = profiles_from_config(cfg)
        if not profiles:
            return None, None
    except Exception as exc:  # noqa: BLE001 — routing é opt-in; falha → off
        from ..logging_config import log_suppressed
        log_suppressed("cli.heuristic_route_kit", exc)
        return None, None

    _cache: dict = {}

    def _client_for(provider: str):
        """Client p/ provider ≠ do principal (o caller reusa o client vivo da
        sessão quando o provider coincide). None em falha → turno usa o default."""
        if not provider:
            return None
        if provider in _cache:
            return _cache[provider]
        try:
            from ..config_loader import BauerConfig
            from ..env_loader import apply_env_to_config
            raw = cfg.model_dump()
            raw["model"]["provider"] = provider
            vcfg = BauerConfig(**raw)
            apply_env_to_config(vcfg)
            c = _build_client(vcfg)
            _cache[provider] = c
            return c
        except Exception as exc:  # noqa: BLE001
            from ..logging_config import log_suppressed
            log_suppressed("cli.route_client_factory", exc)
            return None

    return profiles, _client_for
