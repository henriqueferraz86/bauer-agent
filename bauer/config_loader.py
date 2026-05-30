"""Carregador e validador de config.yaml.

Premortem item 11: config desejada NUNCA deve ser confundida com config aplicada.
Este módulo só carrega o desejado. O aplicado vive em runtime_state.

Providers suportados:
  ollama      — Ollama local (padrão)
  openai      — OpenAI oficial ou endpoint OpenAI-compatible
  openrouter  — OpenRouter (200+ modelos: GPT, Claude, Gemini, Llama…)
  opencode    — OpenCode Zen (modelos gratuitos via opencode.ai/zen/v1)
  groq        — Groq (inferência ultra-rápida: llama3, mixtral, gemma)
  mistral     — Mistral AI (mistral-large, codestral, mixtral)
  xai         — xAI Grok (grok-2, grok-beta)
  together    — Together AI (200+ modelos open-source)
  deepseek    — DeepSeek (deepseek-chat / R1)
  anthropic   — Anthropic Claude (claude-3-5-sonnet, haiku, opus)
  gemini      — Google Gemini (gemini-2.0-flash, 1.5-pro, 1.5-flash)
  azure       — Azure OpenAI (deployment personalizado)
  custom      — Alias para openai com base_url personalizado
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


class ConfigError(Exception):
    """Erro de configuração com mensagem amigável."""


class AgentSection(BaseModel):
    name: str = "Bauer Agent"
    workspace: str = "./workspace"


class OpenAICompatSection(BaseModel):
    """Configuração para OpenAI e endpoints OpenAI-compatible (LM Studio, vLLM, etc.)."""
    host: str = "https://api.openai.com"
    timeout_seconds: int = Field(ge=1, le=600, default=60)
    api_key: str = ""  # ou via OPENAI_API_KEY no .env


class OpenRouterSection(BaseModel):
    """Configuração para OpenRouter — acessa 200+ modelos com uma chave só.

    Modelos: "openai/gpt-4o-mini", "anthropic/claude-haiku-3",
             "google/gemini-flash-1.5", "meta-llama/llama-3.3-70b-instruct"
    """
    api_key: str = ""  # ou via OPENROUTER_API_KEY no .env
    timeout_seconds: int = Field(ge=1, le=600, default=60)
    # Opcional: identificação do app no OpenRouter (aparece nos rankings)
    http_referer: str = "https://github.com/bauer-agent"
    x_title: str = "Bauer Agent"


class OpencodeSection(BaseModel):
    """Configuração para OpenCode Zen — modelos gratuitos sem API key.

    Endpoint: https://opencode.ai/zen/v1 (OpenAI-compatible)
    API key:  public (sem custo, sem cadastro)
    Modelos:  deepseek-v4-flash-free, mimo-v2.5-free, nemotron-3-super-free, big-pickle
    """
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class GroqSection(BaseModel):
    """Groq — inferência ultra-rápida para modelos open-source.

    Endpoint: https://api.groq.com/openai/v1 (OpenAI-compatible)
    Modelos:  llama-3.3-70b-versatile, llama-3.1-8b-instant, mixtral-8x7b, gemma2-9b-it
    Env var:  GROQ_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=30)


class MistralSection(BaseModel):
    """Mistral AI — modelos europeus de alta qualidade.

    Endpoint: https://api.mistral.ai/v1 (OpenAI-compatible)
    Modelos:  mistral-large-latest, mistral-small-latest, codestral-latest, open-mixtral-8x22b
    Env var:  MISTRAL_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class XAISection(BaseModel):
    """xAI — modelos Grok da Elon Musk.

    Endpoint: https://api.x.ai/v1 (OpenAI-compatible)
    Modelos:  grok-3, grok-3-mini, grok-2, grok-beta
    Env var:  XAI_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class TogetherSection(BaseModel):
    """Together AI — 200+ modelos open-source com preços agressivos.

    Endpoint: https://api.together.xyz/v1 (OpenAI-compatible)
    Modelos:  meta-llama/Llama-3.3-70B-Instruct-Turbo, Qwen/Qwen2.5-72B-Instruct, etc.
    Env var:  TOGETHER_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class DeepSeekSection(BaseModel):
    """DeepSeek — modelos chineses com custo/benefício imbatível.

    Endpoint: https://api.deepseek.com/v1 (OpenAI-compatible)
    Modelos:  deepseek-chat (V3), deepseek-reasoner (R1)
    Env var:  DEEPSEEK_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class AnthropicSection(BaseModel):
    """Anthropic — Claude family (wire protocol nativo, não OpenAI-compat).

    Endpoint: https://api.anthropic.com/v1
    Modelos:  claude-3-5-sonnet-20241022, claude-3-5-haiku-20241022, claude-3-opus-20240229
    Env var:  ANTHROPIC_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)
    api_version: str = "2023-06-01"


class GeminiSection(BaseModel):
    """Google Gemini — via endpoint OpenAI-compatible da Google.

    Endpoint: https://generativelanguage.googleapis.com/v1beta/openai/
    Modelos:  gemini-2.0-flash, gemini-1.5-pro, gemini-1.5-flash, gemini-2.5-pro
    Env var:  GEMINI_API_KEY (ou GOOGLE_API_KEY)
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class AzureSection(BaseModel):
    """Azure OpenAI — OpenAI wire protocol com autenticação e URL customizadas.

    URL: https://{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}
    Env vars: AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT
    """
    api_key: str = ""
    endpoint: str = ""          # ex: https://meu-recurso.openai.azure.com
    deployment: str = ""        # ex: gpt-4o
    api_version: str = "2024-08-01-preview"
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class GithubSection(BaseModel):
    """GitHub Models — inferência de IA via infraestrutura Azure da Microsoft.

    Endpoint: https://models.inference.ai.azure.com (OpenAI-compatible)
    Auth:     GitHub Personal Access Token (PAT) com permissão padrão
    Modelos:  gpt-4o, gpt-4o-mini, Phi-4, Meta-Llama-3.3-70B-Instruct,
              Mistral-large-2411, DeepSeek-R1, Cohere-command-r-plus
    Env var:  GITHUB_TOKEN
    Tier:     Gratuito com limites generosos (ideal para testes e dev)
    Docs:     https://docs.github.com/en/github-models
    """
    token: str = ""             # GitHub PAT (ghp_... ou github_pat_...)
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class CopilotSection(BaseModel):
    """GitHub Copilot API — acesso aos modelos via assinatura Copilot.

    Endpoint: https://api.githubcopilot.com (OpenAI-compatible)
    Auth:     Token OAuth do Copilot (obtido via device flow ou IDE)
    Modelos:  gpt-4o, claude-sonnet-4-5, gemini-2.0-flash, o3-mini
    Env var:  COPILOT_TOKEN
    Nota:     Requer assinatura GitHub Copilot ativa
    """
    token: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class ModelSection(BaseModel):
    provider: Literal[
        "ollama", "openai", "openrouter", "opencode", "custom",
        "groq", "mistral", "xai", "together", "deepseek",
        "anthropic", "gemini", "azure",
        "github", "copilot",
    ] = "ollama"
    name: str
    requested_context: int = Field(ge=512, le=1_000_000)
    minimum_context: int = Field(ge=512, le=1_000_000, default=8192)
    auto_downgrade_context: bool = True

    @field_validator("minimum_context")
    @classmethod
    def _min_le_req(cls, v: int, info) -> int:
        req = info.data.get("requested_context")
        if req is not None and v > req:
            raise ValueError(
                f"minimum_context ({v}) não pode ser maior que requested_context ({req})"
            )
        return v


class OllamaSection(BaseModel):
    host: str = "http://localhost:11434"
    timeout_seconds: int = Field(ge=1, le=600, default=30)
    api_key: str = ""  # Bearer token para Ollama remoto protegido por proxy


class ServeSection(BaseModel):
    host: str = "0.0.0.0"
    port: int = Field(ge=1, le=65535, default=8000)
    api_key: str = ""  # Bearer token para proteger o bauer serve (ou BAUER_SERVE_API_KEY no .env)
    workers: int = Field(ge=1, le=8, default=1)
    rate_limit_requests: int = Field(ge=0, default=60)   # max requests por IP por janela; 0 = desativado
    rate_limit_window_s: float = Field(ge=1.0, default=60.0)  # janela em segundos


class RuntimeSection(BaseModel):
    profile: Literal["low", "medium", "high"] = "low"
    ram_limit_mb: int = Field(ge=512, default=4096)
    safety_margin_mb: int = Field(ge=0, default=1024)


class RouterSection(BaseModel):
    enabled: bool = False
    router_model: str = "qwen3:0.6b"
    code_model: str = "smollm3"
    reasoning_model: str = "phi4-mini"
    direct_model: str = "qwen3:0.6b"


class LoggingSection(BaseModel):
    level: Literal["debug", "info", "warning", "error"] = "info"
    file: str | None = "./logs/bauer.log"


class ToolsSection(BaseModel):
    shell_enabled: bool = False
    web_enabled: bool = False
    safe_mode: bool = True
    timeout_seconds: int = Field(ge=1, le=300, default=30)
    max_output_kb: int = Field(ge=1, le=1000, default=50)


class WebSection(BaseModel):
    """Configuração de backends web para web_search e web_fetch.

    Backends de busca (search_backend):
      ddgs     — DuckDuckGo via biblioteca ddgs (padrão, sem config)
      searxng  — SearXNG self-hosted (requer searxng_url)
      brave    — Brave Search API (requer brave_api_key ou BRAVE_API_KEY no .env)

    Backends de extração (extract_backend):
      httpx    — httpx + BeautifulSoup (padrão, leve)
      crawl4ai — crawl4ai (LLM-friendly Markdown, requer: pip install crawl4ai)
    """
    search_backend: str = "ddgs"
    extract_backend: str = "httpx"
    searxng_url: str = "http://localhost:8080"
    brave_api_key: str = ""          # ou BRAVE_API_KEY no .env
    max_results: int = Field(ge=1, le=20, default=5)
    max_chars: int = Field(ge=100, le=50_000, default=5000)
    timeout_seconds: int = Field(ge=1, le=60, default=15)


class BauerConfig(BaseModel):
    agent: AgentSection = AgentSection()
    model: ModelSection
    ollama: OllamaSection = OllamaSection()
    openai: OpenAICompatSection = OpenAICompatSection()
    openrouter: OpenRouterSection = OpenRouterSection()
    opencode: OpencodeSection = OpencodeSection()
    groq: GroqSection = GroqSection()
    mistral: MistralSection = MistralSection()
    xai: XAISection = XAISection()
    together: TogetherSection = TogetherSection()
    deepseek: DeepSeekSection = DeepSeekSection()
    anthropic: AnthropicSection = AnthropicSection()
    gemini: GeminiSection = GeminiSection()
    azure: AzureSection = AzureSection()
    github: GithubSection = GithubSection()
    copilot: CopilotSection = CopilotSection()
    runtime: RuntimeSection = RuntimeSection()
    logging: LoggingSection = LoggingSection()
    tools: ToolsSection = ToolsSection()
    web: WebSection = WebSection()
    serve: ServeSection = ServeSection()
    router: RouterSection = RouterSection()


def load_config(path: str | Path = "config.yaml") -> BauerConfig:
    """Lê e valida config.yaml. Aplica .env automaticamente. Levanta ConfigError em falha."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(
            f"Arquivo de config não encontrado: {p}\n"
            f"Crie um config.yaml ou indique o caminho com --config."
        )

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML inválido em {p}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Conteúdo de {p} precisa ser um mapeamento YAML no topo.")

    try:
        cfg = BauerConfig(**raw)
    except ValidationError as exc:
        # Pydantic produz mensagens longas; reformata pra ficar útil.
        problems = "\n".join(
            f"  - {'/'.join(str(x) for x in e['loc'])}: {e['msg']}"
            for e in exc.errors()
        )
        raise ConfigError(f"Config inválida em {p}:\n{problems}") from exc

    # Aplica .env (procura na mesma pasta do config.yaml e na cwd)
    from .env_loader import apply_env_to_config, load_dotenv
    for env_candidate in [p.parent / ".env", Path(".env")]:
        if env_candidate.exists():
            load_dotenv(env_candidate)
            break
    apply_env_to_config(cfg)

    return cfg


def validate_config_file(path: str | Path = "config.yaml") -> tuple[bool, str]:
    """Versão pronta-pra-CLI: retorna (ok, mensagem)."""
    try:
        cfg = load_config(path)
    except ConfigError as exc:
        return False, str(exc)
    return True, (
        f"OK — provider={cfg.model.provider}, modelo={cfg.model.name}, "
        f"requested_context={cfg.model.requested_context}, "
        f"profile={cfg.runtime.profile}"
    )
