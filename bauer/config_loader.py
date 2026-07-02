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
  github      — GitHub Models (inferência gratuita via Azure)
  copilot     — GitHub Copilot API (via assinatura Copilot)
  custom      — Alias para openai com base_url personalizado
  cohere      — Cohere Command (command-r-plus, command-a)
  perplexity  — Perplexity AI com busca na web (sonar, sonar-pro)
  fireworks   — Fireworks AI (llama, mixtral, qwen open-source)
  huggingface — HuggingFace Inference API (300k+ modelos)
  cerebras    — Cerebras (wafer-scale, velocidade máxima)
  sambanova   — Sambanova Cloud (inferência empresarial)
  nvidia      — NVIDIA NIM (A100/H100, modelos curados)
  lmstudio    — LM Studio (alternativa local ao Ollama)
  databricks  — Databricks Mosaic AI (serving endpoints)
  moonshot    — Moonshot / Kimi (contexto longo, chinês)
  alibaba     — Alibaba DashScope / Qwen (qwen-max, qwen-plus)
  vertex      — Google Vertex AI (projetos GCP, modelos Gemini/tuned)
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigError(Exception):
    """Erro de configuração com mensagem amigável."""


class _StrictSection(BaseModel):
    """Base para todas as sections: campos desconhecidos são ERRO, não silêncio.

    Premortem (bug real 2026-06-10): `think: false` no config.yaml foi
    silenciosamente ignorado porque ModelSection não tinha o campo — o usuário
    acreditou que a config estava ativa. extra="forbid" converte typos e campos
    não suportados em erro de validação com hint dos campos válidos.
    """
    model_config = ConfigDict(extra="forbid")


class AgentSection(_StrictSection):
    name: str = "Bauer Agent"
    workspace: str = "./workspace"
    tool_timeout_s: float = Field(ge=0.0, le=600.0, default=30.0)
    # Escada de decisão "código mínimo" (inspirada no Ponytail, MIT) no system
    # prompt padrão — prefere reuso/stdlib/uma-linha a abstração nova, sem
    # cortar validação/segurança/acessibilidade. default True = agressivo.
    minimal_code_mode: bool = True
    # Injeta no system prompt a lista de agents especialistas (agents.yaml)
    # e instrui o modelo a delegar via `delegate_task(agent_name=...)` quando
    # a tarefa combinar com um deles. default True = agressivo, mesma
    # filosofia do minimal_code_mode.
    specialist_delegation: bool = True


class ObservabilitySection(_StrictSection):
    """Rastreamento distribuído opcional via Langfuse.

    Requer: pip install langfuse
    Env vars: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY (preferidos ao config.yaml).
    Host padrão: https://cloud.langfuse.com — ou URL self-hosted.
    """
    langfuse_enabled: bool = False
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_public_key: str = ""   # ou LANGFUSE_PUBLIC_KEY no .env (preferido)
    langfuse_secret_key: str = ""   # ou LANGFUSE_SECRET_KEY no .env (preferido)


class OpenAICompatSection(_StrictSection):
    """Configuração para OpenAI e endpoints OpenAI-compatible (LM Studio, vLLM, etc.)."""
    host: str = "https://api.openai.com"
    timeout_seconds: int = Field(ge=1, le=600, default=60)
    api_key: str = ""  # ou via OPENAI_API_KEY no .env
    # Login via browser (ChatGPT Plus/Pro): backend Responses usado pelo Codex.
    # Vazio = usa o padrão (https://chatgpt.com/backend-api/codex).
    chatgpt_base_url: str = ""


class OpenRouterSection(_StrictSection):
    """Configuração para OpenRouter — acessa 200+ modelos com uma chave só.

    Modelos: "openai/gpt-4o-mini", "anthropic/claude-haiku-3",
             "google/gemini-flash-1.5", "meta-llama/llama-3.3-70b-instruct"
    """
    api_key: str = ""  # ou via OPENROUTER_API_KEY no .env
    timeout_seconds: int = Field(ge=1, le=600, default=60)
    # Opcional: identificação do app no OpenRouter (aparece nos rankings)
    http_referer: str = "https://github.com/bauer-agent"
    x_title: str = "Bauer Agent"


class OpencodeSection(_StrictSection):
    """Configuração para OpenCode Zen — modelos gratuitos sem API key.

    Endpoint: https://opencode.ai/zen/v1 (OpenAI-compatible)
    API key:  public (sem custo, sem cadastro)
    Modelos:  deepseek-v4-flash-free, mimo-v2.5-free, nemotron-3-super-free, big-pickle
    """
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class GroqSection(_StrictSection):
    """Groq — inferência ultra-rápida para modelos open-source.

    Endpoint: https://api.groq.com/openai/v1 (OpenAI-compatible)
    Modelos:  llama-3.3-70b-versatile, llama-3.1-8b-instant, mixtral-8x7b, gemma2-9b-it
    Env var:  GROQ_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=30)


class MistralSection(_StrictSection):
    """Mistral AI — modelos europeus de alta qualidade.

    Endpoint: https://api.mistral.ai/v1 (OpenAI-compatible)
    Modelos:  mistral-large-latest, mistral-small-latest, codestral-latest, open-mixtral-8x22b
    Env var:  MISTRAL_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class XAISection(_StrictSection):
    """xAI — modelos Grok da Elon Musk.

    Endpoint: https://api.x.ai/v1 (OpenAI-compatible)
    Modelos:  grok-3, grok-3-mini, grok-2, grok-beta
    Env var:  XAI_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class TogetherSection(_StrictSection):
    """Together AI — 200+ modelos open-source com preços agressivos.

    Endpoint: https://api.together.xyz/v1 (OpenAI-compatible)
    Modelos:  meta-llama/Llama-3.3-70B-Instruct-Turbo, Qwen/Qwen2.5-72B-Instruct, etc.
    Env var:  TOGETHER_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class DeepSeekSection(_StrictSection):
    """DeepSeek — modelos chineses com custo/benefício imbatível.

    Endpoint: https://api.deepseek.com/v1 (OpenAI-compatible)
    Modelos:  deepseek-chat (V3), deepseek-reasoner (R1)
    Env var:  DEEPSEEK_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class AnthropicSection(_StrictSection):
    """Anthropic — Claude family (wire protocol nativo, não OpenAI-compat).

    Endpoint: https://api.anthropic.com/v1
    Modelos:  claude-3-5-sonnet-20241022, claude-3-5-haiku-20241022, claude-3-opus-20240229
    Env var:  ANTHROPIC_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)
    api_version: str = "2023-06-01"


class GeminiSection(_StrictSection):
    """Google Gemini — via endpoint OpenAI-compatible da Google.

    Endpoint: https://generativelanguage.googleapis.com/v1beta/openai/
    Modelos:  gemini-2.0-flash, gemini-1.5-pro, gemini-1.5-flash, gemini-2.5-pro
    Env var:  GEMINI_API_KEY (ou GOOGLE_API_KEY)
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class AzureSection(_StrictSection):
    """Azure OpenAI — OpenAI wire protocol com autenticação e URL customizadas.

    URL: https://{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}
    Env vars: AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT
    """
    api_key: str = ""
    endpoint: str = ""          # ex: https://meu-recurso.openai.azure.com
    deployment: str = ""        # ex: gpt-4o
    api_version: str = "2024-08-01-preview"
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class GithubSection(_StrictSection):
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


class CopilotSection(_StrictSection):
    """GitHub Copilot API — acesso aos modelos via assinatura Copilot.

    Endpoint: https://api.githubcopilot.com (OpenAI-compatible)
    Auth:     Token OAuth do Copilot (obtido via device flow ou IDE)
    Modelos:  gpt-4o, claude-sonnet-4-5, gemini-2.0-flash, o3-mini
    Env var:  COPILOT_TOKEN
    Nota:     Requer assinatura GitHub Copilot ativa
    """
    token: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class CohereSection(_StrictSection):
    """Cohere — Command family com retrieval e tool use nativos.

    Endpoint: https://api.cohere.com/compatibility/v1 (OpenAI-compatible)
    Modelos:  command-a-03-2025, command-r-plus-08-2024, command-r7b-12-2024
    Env var:  COHERE_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class PerplexitySection(_StrictSection):
    """Perplexity AI — modelos com busca na web integrada.

    Endpoint: https://api.perplexity.ai (OpenAI-compatible)
    Modelos:  sonar-pro, sonar, sonar-reasoning, r1-1776
    Env var:  PERPLEXITY_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class FireworksSection(_StrictSection):
    """Fireworks AI — inferência de alta velocidade para modelos open-source.

    Endpoint: https://api.fireworks.ai/inference/v1 (OpenAI-compatible)
    Modelos:  accounts/fireworks/models/llama-v3p3-70b-instruct
    Env var:  FIREWORKS_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=30)


class HuggingFaceSection(_StrictSection):
    """HuggingFace Inference API — acesso a 300k+ modelos open-source.

    Endpoint padrão: https://api-inference.huggingface.co/v1 (já inclui /v1)
    Para Inference Endpoints dedicados, altere host para a URL do endpoint.
    Env var:  HUGGINGFACE_API_KEY ou HF_TOKEN
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=120)
    host: str = "https://api-inference.huggingface.co/v1"


class CerebrasSection(_StrictSection):
    """Cerebras — inferência mais rápida do mercado (wafer-scale).

    Endpoint: https://api.cerebras.ai/v1 (OpenAI-compatible)
    Modelos:  llama3.3-70b, llama3.1-8b, qwen-3-32b
    Env var:  CEREBRAS_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=30)


class SambanovaSection(_StrictSection):
    """Sambanova Cloud — inferência empresarial de alta escala.

    Endpoint: https://api.sambanova.ai/v1 (OpenAI-compatible)
    Modelos:  Meta-Llama-3.3-70B-Instruct, DeepSeek-R1-Distill-Llama-70B
    Env var:  SAMBANOVA_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class NvidiaSection(_StrictSection):
    """NVIDIA NIM — inferência em A100/H100 via NVIDIA Cloud.

    Endpoint: https://integrate.api.nvidia.com/v1 (OpenAI-compatible)
    Modelos:  meta/llama-3.3-70b-instruct, nvidia/llama-3.3-nemotron-super-49b-v1
    Env var:  NVIDIA_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class LMStudioSection(_StrictSection):
    """LM Studio — alternativa local ao Ollama (endpoint OpenAI-compatible).

    Endpoint padrão: http://localhost:1234/v1
    Env var:  LMSTUDIO_HOST (para host customizado)
    """
    host: str = "http://localhost:1234"
    timeout_seconds: int = Field(ge=1, le=600, default=120)
    api_key: str = ""  # LM Studio local não requer API key


class DatabricksSection(_StrictSection):
    """Databricks Mosaic AI — modelos servidos via MLflow no Databricks workspace.

    Endpoint: https://{host}/serving-endpoints (OpenAI-compatible)
    Auth:     Personal Access Token (PAT) do Databricks
    Env vars: DATABRICKS_TOKEN, DATABRICKS_HOST
    """
    host: str = ""           # ex: https://myworkspace.azuredatabricks.net
    api_key: str = ""        # Databricks PAT (env DATABRICKS_TOKEN tem prioridade)
    timeout_seconds: int = Field(ge=1, le=600, default=120)


class MoonshotSection(_StrictSection):
    """Moonshot / Kimi — LLM chinês com foco em contexto longo.

    Endpoint: https://api.moonshot.cn/v1 (OpenAI-compatible)
    Modelos:  moonshot-v1-8k, moonshot-v1-32k, moonshot-v1-128k, kimi-latest
    Env var:  MOONSHOT_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class AlibabaSection(_StrictSection):
    """Alibaba DashScope / Qwen — LLMs da Alibaba Cloud.

    Endpoint: https://dashscope.aliyuncs.com/compatible-mode/v1 (OpenAI-compatible)
    Modelos:  qwen-max, qwen-plus, qwen-turbo, qwen-long, qwen2.5-72b-instruct
    Env vars: ALIBABA_API_KEY ou DASHSCOPE_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class VertexSection(_StrictSection):
    """Google Vertex AI — modelos Gemini/tuned em projetos GCP.

    Endpoint: https://{region}-aiplatform.googleapis.com/v1beta1/projects/{project}/
              locations/{region}/endpoints/openapi (OpenAI-compatible)
    Auth:     Bearer token (gcloud auth print-access-token ou service account)
    Env vars: VERTEX_PROJECT_ID, VERTEX_REGION, VERTEX_ACCESS_TOKEN
    """
    project_id: str = ""          # GCP project ID (env VERTEX_PROJECT_ID)
    region: str = "us-central1"   # GCP region (env VERTEX_REGION)
    access_token: str = ""        # Bearer token (env VERTEX_ACCESS_TOKEN)
    timeout_seconds: int = Field(ge=1, le=600, default=120)


class ReplicateSection(_StrictSection):
    """Replicate — run open-source models in the cloud.

    Endpoint: https://api.replicate.com/v1 (OpenAI-compatible)
    Env var:  REPLICATE_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class NovitaSection(_StrictSection):
    """Novita AI — cost-effective inference for open-source LLMs.

    Endpoint: https://api.novita.ai/v3/openai (OpenAI-compatible)
    Env var:  NOVITA_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class AI21Section(_StrictSection):
    """AI21 Labs — Jamba and Jurassic models.

    Endpoint: https://api.ai21.com/studio/v1 (OpenAI-compatible)
    Env var:  AI21_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class AnyscaleSection(_StrictSection):
    """Anyscale Endpoints — open-source LLMs via Ray infrastructure.

    Endpoint: https://api.endpoints.anyscale.com/v1 (OpenAI-compatible)
    Env var:  ANYSCALE_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class FeatherlessSection(_StrictSection):
    """Featherless AI — lightweight serverless inference.

    Endpoint: https://api.featherless.ai/v1 (OpenAI-compatible)
    Env var:  FEATHERLESS_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class HyperbolicSection(_StrictSection):
    """Hyperbolic — GPU-efficient inference at scale.

    Endpoint: https://api.hyperbolic.xyz/v1 (OpenAI-compatible)
    Env var:  HYPERBOLIC_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class InferenceSection(_StrictSection):
    """Inference.net — fast and affordable LLM inference.

    Endpoint: https://api.inference.net/v1 (OpenAI-compatible)
    Env var:  INFERENCE_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class NcompassSection(_StrictSection):
    """Ncompass — enterprise-grade LLM serving.

    Endpoint: https://api.ncompass.tech/v1 (OpenAI-compatible)
    Env var:  NCOMPASS_API_KEY
    """
    api_key: str = ""
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class CloudflareSection(_StrictSection):
    """Cloudflare Workers AI — run AI at the edge globally.

    Endpoint: https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1
    Env vars: CLOUDFLARE_API_KEY, CLOUDFLARE_ACCOUNT_ID
    """
    api_key: str = ""
    account_id: str = ""    # Cloudflare Account ID (or CLOUDFLARE_ACCOUNT_ID)
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class LeptonSection(_StrictSection):
    """Lepton AI — model serving with simple Python API.

    Endpoint: https://llama3-1-8b.lepton.run/api/v1 (model-specific subdomain)
    Env var:  LEPTON_API_KEY
    """
    api_key: str = ""
    subdomain: str = ""     # model-specific subdomain (e.g. "llama3-1-8b")
    timeout_seconds: int = Field(ge=1, le=600, default=60)


class FallbackModel(BaseModel):
    """Par (provider, name) para fallback automático de modelo."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    name: str


class ModelSection(_StrictSection):
    provider: Literal[
        "ollama", "openai", "openrouter", "opencode", "custom",
        "groq", "mistral", "xai", "together", "deepseek",
        "anthropic", "gemini", "azure",
        "github", "copilot",
        "cohere", "perplexity", "fireworks", "huggingface",
        "cerebras", "sambanova", "nvidia", "lmstudio",
        "databricks", "moonshot", "alibaba", "vertex",
        # G16a — new providers
        "replicate", "novita", "ai21", "anyscale",
        "featherless", "hyperbolic", "inference", "ncompass",
        "cloudflare", "lepton",
    ] = "ollama"
    name: str
    requested_context: int = Field(ge=512, le=1_000_000, default=8192)
    minimum_context: int = Field(ge=512, le=1_000_000, default=8192)
    auto_downgrade_context: bool = True
    think: bool | None = None  # Ollama only: desativa thinking mode (gemma4, qwq, etc.)
    fallback_models: list[FallbackModel] = Field(
        default_factory=list,
        description="Modelos alternativos (provider+name) quando o principal falha.",
    )
    # Mantido para compatibilidade — ignorado se fallback_models estiver preenchido
    fallback_providers: list[str] = Field(
        default_factory=list,
        description="[deprecated] Use fallback_models.",
    )

    @field_validator("minimum_context")
    @classmethod
    def _min_le_req(cls, v: int, info) -> int:
        req = info.data.get("requested_context")
        if req is not None and v > req:
            raise ValueError(
                f"minimum_context ({v}) não pode ser maior que requested_context ({req})"
            )
        return v


class OllamaSection(_StrictSection):
    host: str = "http://localhost:11434"
    timeout_seconds: int = Field(ge=1, le=600, default=30)
    api_key: str = ""  # Bearer token para Ollama remoto protegido por proxy


class ServeSection(_StrictSection):
    host: str = "127.0.0.1"  # default seguro — bind apenas local; use 0.0.0.0 só com api_key
    port: int = Field(ge=1, le=65535, default=8000)
    api_key: str = ""  # Bearer token para proteger o bauer serve (ou BAUER_SERVE_API_KEY no .env)
    workers: int = Field(ge=1, le=8, default=1)
    rate_limit_requests: int = Field(ge=0, default=60)   # max requests por IP/key por janela; 0 = desativado
    rate_limit_window_s: float = Field(ge=1.0, default=60.0)  # janela em segundos
    rate_limit_per_key: bool = False   # True = limitar por API key em vez de por IP
    cors_origins: list[str] = []       # origens CORS permitidas; vazio = CORS desativado; ["*"] = todas
    enable_gzip: bool = True           # compressão GZip para respostas > 1 KB
    enable_access_log: bool = False    # gravar JSON access log por request (método, path, status, latência)


class RuntimeSection(_StrictSection):
    profile: Literal["low", "medium", "high"] = "low"
    ram_limit_mb: int = Field(ge=512, default=4096)
    safety_margin_mb: int = Field(ge=0, default=1024)


class RouterSection(_StrictSection):
    enabled: bool = False
    router_model: str = "qwen3:0.6b"
    code_model: str = "smollm3"
    reasoning_model: str = "phi4-mini"
    direct_model: str = "qwen3:0.6b"


class LoggingSection(_StrictSection):
    level: Literal["debug", "info", "warning", "error"] = "info"
    file: str | None = "./logs/bauer.log"


class ToolsSection(_StrictSection):
    shell_enabled: bool = False
    web_enabled: bool = False
    safe_mode: bool = True
    timeout_seconds: int = Field(ge=1, le=300, default=30)
    max_output_kb: int = Field(ge=1, le=1000, default=50)
    max_tool_calls: int = Field(ge=1, default=500)
    # Cap de tool calls DENTRO DE UM ÚNICO TURNO do chat interativo (proteção
    # anti-loop de bauer/agent.py::MAX_TOOL_TURNS) — diferente de
    # max_tool_calls acima, que é o teto por SESSÃO inteira do ToolRouter.
    # Mensagem de erro quando estoura: "Limite de N tool calls atingido
    # neste turno."
    max_tool_turns: int = Field(ge=1, default=150)
    # Toolset enxuto: se não-vazio, SÓ estas tools são expostas ao modelo.
    # Encolhe o prompt (as 79 tools = ~14k tokens) — essencial p/ modelos locais
    # em CPU. Vazio = todas as tools. Ex.: [web_search, web_fetch, read_file,
    # list_dir, run_command, datetime_now, calculate].
    tool_allowlist: list[str] = Field(default_factory=list)
    # Comandos de SHELL extras liberados para run_command, além da allowlist
    # fixa embutida (git, python, npm, pytest...) em bauer/shell_runner.py.
    # Vazio = só a allowlist padrão. Ex.: [docker, docker-compose, kubectl].
    # Ainda passam pela denylist (sempre bloqueada) e pelo safe_mode (risco
    # médio exige confirm=true).
    extra_allowed_commands: list[str] = Field(default_factory=list)


class LoopSection(_StrictSection):
    """Orçamento de segurança e política de aprovação do modo `/loop`.

    O `/loop` roda o agente sozinho, turno após turno, sem confirmação
    humana a cada passo — estes limites existem para conter o "blast
    radius" de uma tarefa que entra em loop ou sai do previsto.
    """
    max_minutes: int = Field(ge=1, default=30)
    max_tool_calls: int = Field(ge=1, default=120)
    max_cost_usd: float = Field(ge=0.0, default=2.0)
    approval_mode: Literal["threshold", "deny_all", "yolo"] = "threshold"
    approval_risk_threshold: float = Field(ge=0.0, le=1.0, default=0.4)


class McpServerEntry(_StrictSection):
    """Configuração de um servidor MCP individual.

    Exemplo em config.yaml:
        mcp:
          servers:
            filesystem:
              command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
              timeout: 30
            meu_server:
              command: ["python", "-m", "meu_mcp_server"]
              env:
                MY_VAR: valor
              cwd: /tmp/projeto
    """
    command: list[str] | str = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout: float = Field(ge=1.0, le=300.0, default=30.0)
    cwd: str | None = None

    @field_validator("command")
    @classmethod
    def _normalize_command(cls, v: list[str] | str) -> list[str]:
        if isinstance(v, str):
            return v.split()
        return v


class McpSection(_StrictSection):
    """Configuração de servidores MCP (Model Context Protocol) stdio.

    Servidores são iniciados sob demanda quando a tool mcp_call é usada.
    """
    servers: dict[str, McpServerEntry] = Field(default_factory=dict)


class AuxiliarySlot(_StrictSection):
    """One auxiliary-client slot — points to a (provider, model) pair.

    Both fields default to empty, meaning "use the main model.name from
    config.yaml". Set them per-slot to route specific tasks (decomposition,
    triage, compression) to a cheaper / faster model than the main agent.

    Example in config.yaml::

        auxiliary:
          kanban_decomposer:
            provider: groq
            model: llama-3.3-70b-versatile
          triage_specifier:
            provider: openai
            model: gpt-4o-mini
          compression_model: {}   # falls back to main model
    """
    provider: str = ""   # Empty → fall back to main model.provider
    model: str = ""      # Empty → fall back to main model.name


class AuxiliarySection(_StrictSection):
    """Auxiliary LLM slots — cheap/fast models for routine subtasks.

    Each slot is consumed by a specific Bauer subsystem:
      - `kanban_decomposer`  — kanban_decompose.decompose_task() (Wave 3)
      - `triage_specifier`   — kanban_specify.specify_task() (Wave 3)
      - `compression_model`  — context compression
      - `background_reviewer`— background_review (G10)
      - `approval_model`     — llm_approval de tools de alto risco (G4)
      - `vision_model`       — tools de visão: browser_vision/vision_analyze/
                               video_analyze (G18.4; ex: ollama 'llava')

    All slots default to empty → the main `model.name` is used. This keeps
    the system working out of the box; users opt-in to per-slot routing as
    they tune for cost.
    """
    kanban_decomposer:  AuxiliarySlot = AuxiliarySlot()
    triage_specifier:   AuxiliarySlot = AuxiliarySlot()
    compression_model:  AuxiliarySlot = AuxiliarySlot()
    background_reviewer: AuxiliarySlot = AuxiliarySlot()
    approval_model:     AuxiliarySlot = AuxiliarySlot()
    vision_model:       AuxiliarySlot = AuxiliarySlot()


class WebSection(_StrictSection):
    """Configuração de backends web para web_search e web_fetch.

    Backends de busca (search_backend):
      auto      — auto-detecção: brave → searxng → ddgs → wikipedia  (padrão)
      ddgs      — DuckDuckGo via biblioteca ddgs (open, sem chave; requer pip install ddgs)
      searxng   — SearXNG self-hosted (open-source/AGPL, metabusca; requer searxng_url)
      wikipedia — Wikipedia MediaWiki API (open/CC BY-SA, sem chave, sem dependência;
                  preciso para fatos/entidades; é o fallback garantido do auto)
      brave     — Brave Search API (requer brave_api_key ou BRAVE_API_KEY no .env)

    Backends de extração (extract_backend):
      auto     — auto-detecção: crawl4ai → httpx  (padrão)
      httpx    — httpx + BeautifulSoup (leve, sempre disponível)
      crawl4ai — crawl4ai (LLM-friendly Markdown, requer: pip install crawl4ai)
    """
    search_backend: str = "auto"
    extract_backend: str = "auto"
    searxng_url: str = "http://localhost:8080"
    brave_api_key: str = ""          # ou BRAVE_API_KEY no .env
    wikipedia_lang: str = "en"       # idioma da Wikipedia (en = mais completa; pt, es, ...)
    max_results: int = Field(ge=1, le=20, default=5)
    max_chars: int = Field(ge=100, le=50_000, default=5000)
    timeout_seconds: int = Field(ge=1, le=60, default=15)
    cache_ttl_seconds: int = Field(ge=0, le=86_400, default=300)  # cache de busca/extração (0 = off)


class TelegramSection(_StrictSection):
    """Canal Telegram — bot conversacional via long-polling.

    Token: prefira TELEGRAM_BOT_TOKEN no .env (bot_token aqui é fallback).
    Segurança: allowed_users vazio NEGA todo mundo; para liberar geral é
    preciso allow_all: true explícito (não recomendado).
    """
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    enabled: bool = False
    bot_token: str = ""                  # ou TELEGRAM_BOT_TOKEN no .env (preferido)
    allowed_users: list[int] = Field(default_factory=list)  # ids numéricos do Telegram
    allow_all: bool = False              # true = responde qualquer usuário (cuidado)
    poll_interval: float = Field(ge=0.5, le=60.0, default=2.0)
    max_msgs_per_minute: int = Field(ge=1, le=600, default=20)
    model_allowlist: list[str] = Field(default_factory=list)  # se preenchida, só esses modelos aparecem no /model


class DiscordSection(_StrictSection):
    """Canal Discord — bot conversacional via Gateway WebSocket.

    Requer extra: pip install 'bauer-agent[gateway]' (websockets).
    O bot precisa do intent MESSAGE_CONTENT habilitado no Developer Portal.
    Token: prefira DISCORD_BOT_TOKEN no .env.
    """
    enabled: bool = False
    bot_token: str = ""                  # ou DISCORD_BOT_TOKEN no .env (preferido)
    allowed_users: list[str] = Field(default_factory=list)     # ids de usuário (snowflakes)
    allowed_guilds: list[str] = Field(default_factory=list)    # vazio = qualquer guild
    allowed_channels: list[str] = Field(default_factory=list)  # vazio = qualquer canal
    allow_all: bool = False
    mention_only: bool = True            # em guild só responde se mencionado; DM sempre responde
    max_msgs_per_minute: int = Field(ge=1, le=600, default=20)


class GatewaySection(_StrictSection):
    """Bauer Gateway — runtime unificado de canais + entrega do outbox."""
    outbox_drain_interval_s: int = Field(ge=1, le=3600, default=15)


class BauerConfig(_StrictSection):
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
    cohere: CohereSection = CohereSection()
    perplexity: PerplexitySection = PerplexitySection()
    fireworks: FireworksSection = FireworksSection()
    huggingface: HuggingFaceSection = HuggingFaceSection()
    cerebras: CerebrasSection = CerebrasSection()
    sambanova: SambanovaSection = SambanovaSection()
    nvidia: NvidiaSection = NvidiaSection()
    lmstudio: LMStudioSection = LMStudioSection()
    databricks: DatabricksSection = DatabricksSection()
    moonshot: MoonshotSection = MoonshotSection()
    alibaba: AlibabaSection = AlibabaSection()
    vertex: VertexSection = VertexSection()
    # G16a — new providers
    replicate: ReplicateSection = ReplicateSection()
    novita: NovitaSection = NovitaSection()
    ai21: AI21Section = AI21Section()
    anyscale: AnyscaleSection = AnyscaleSection()
    featherless: FeatherlessSection = FeatherlessSection()
    hyperbolic: HyperbolicSection = HyperbolicSection()
    inference: InferenceSection = InferenceSection()
    ncompass: NcompassSection = NcompassSection()
    cloudflare: CloudflareSection = CloudflareSection()
    lepton: LeptonSection = LeptonSection()
    runtime: RuntimeSection = RuntimeSection()
    logging: LoggingSection = LoggingSection()
    tools: ToolsSection = ToolsSection()
    loop: LoopSection = LoopSection()
    web: WebSection = WebSection()
    mcp: McpSection = McpSection()
    serve: ServeSection = ServeSection()
    router: RouterSection = RouterSection()
    auxiliary: AuxiliarySection = AuxiliarySection()
    telegram: TelegramSection = TelegramSection()
    discord: DiscordSection = DiscordSection()
    gateway: GatewaySection = GatewaySection()
    observability: ObservabilitySection = ObservabilitySection()


def _valid_fields_for(section_name: str) -> str:
    """Retorna lista legível dos campos válidos de uma section do BauerConfig."""
    field_info = BauerConfig.model_fields.get(section_name)
    if field_info is None:
        # Nível raiz: lista as sections válidas
        return ", ".join(sorted(BauerConfig.model_fields))
    annotation = field_info.annotation
    if annotation is not None and hasattr(annotation, "model_fields"):
        return ", ".join(sorted(annotation.model_fields))
    return ""


def load_config(path: str | Path = "config.yaml") -> BauerConfig:
    """Lê e valida config.yaml. Aplica .env automaticamente. Levanta ConfigError em falha.

    Ordem de busca (quando o caminho padrão não existe):
      1. Caminho fornecido (ou "config.yaml" no cwd)
      2. ~/.bauer/config.yaml  ($BAUER_HOME/config.yaml)
    """
    p = Path(path)
    if not p.exists():
        from .paths import config_path as _config_path
        fallback = _config_path()
        if fallback.exists():
            p = fallback
        else:
            raise ConfigError(
                f"Arquivo de config não encontrado: {p}\n"
                f"Também tentei: {fallback}\n"
                f"Execute 'bauer init' para criar a configuração inicial."
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
        lines: list[str] = []
        for e in exc.errors():
            loc = "/".join(str(x) for x in e["loc"])
            if e.get("type") == "extra_forbidden":
                # Campo desconhecido — quase sempre typo. Lista os campos válidos
                # da section para o usuário se localizar sem abrir o código.
                section_name = str(e["loc"][0]) if e["loc"] else ""
                valid = _valid_fields_for(section_name)
                hint = f" Campos válidos de '{section_name}': {valid}" if valid else ""
                lines.append(f"  - {loc}: campo desconhecido (typo?).{hint}")
            else:
                lines.append(f"  - {loc}: {e['msg']}")
        raise ConfigError(f"Config inválida em {p}:\n" + "\n".join(lines)) from exc

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
