"""Carregador de .env — igual ao Hermes Agent.

Prioridade (maior para menor):
  1. Variáveis já no ambiente do sistema (export, Docker env, etc.)
  2. Arquivo .env na raiz do projeto
  3. config.yaml

Variáveis reconhecidas:
  OPENAI_API_KEY         — OpenAI direta / endpoints OpenAI-compat
  OPENROUTER_API_KEY     — OpenRouter (200+ modelos)
  OPENCODE_API_KEY       — OpenCode Zen (opcional; padrão "public")
  GROQ_API_KEY           — Groq (inferência ultra-rápida)
  MISTRAL_API_KEY        — Mistral AI
  XAI_API_KEY            — xAI Grok
  TOGETHER_API_KEY       — Together AI
  DEEPSEEK_API_KEY       — DeepSeek
  ANTHROPIC_API_KEY      — Anthropic Claude
  GEMINI_API_KEY         — Google Gemini (também: GOOGLE_API_KEY)
  GOOGLE_API_KEY         — Google Gemini (alias)
  AZURE_OPENAI_API_KEY   — Azure OpenAI
  AZURE_OPENAI_ENDPOINT  — Azure OpenAI endpoint URL
  GITHUB_TOKEN           — GitHub Models (PAT com acesso padrão)
  COPILOT_TOKEN          — GitHub Copilot API (token OAuth do Copilot)
  OLLAMA_API_KEY         — Ollama Cloud ou proxy protegido
  BAUER_SERVE_API_KEY    — chave de autenticação do bauer serve
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> dict[str, str]:
    """Lê arquivo .env e injeta vars no os.environ.

    - Não sobrescreve variáveis já presentes no ambiente do sistema.
    - Ignora linhas em branco e comentários (#).
    - Suporta aspas simples e duplas nos valores.
    - Retorna dict com todas as vars carregadas (inclusive as já existentes no env).
    """
    env_path = Path(path)
    loaded: dict[str, str] = {}

    if not env_path.exists():
        return loaded

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        # Remove aspas ao redor do valor (simples ou duplas)
        value = value.strip()
        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1]

        # Sistema tem prioridade — só injeta se a var não existir
        if key not in os.environ:
            os.environ[key] = value

        loaded[key] = os.environ[key]

    return loaded


def apply_env_to_config(cfg) -> None:
    """Aplica variáveis de ambiente ao BauerConfig já carregado (in-place).

    Chamada depois de load_dotenv() para que o .env já esteja no os.environ.
    """
    # --- OpenAI direta ---
    if key := os.environ.get("OPENAI_API_KEY"):
        cfg.openai.api_key = key

    # --- OpenRouter ---
    if key := os.environ.get("OPENROUTER_API_KEY"):
        cfg.openrouter.api_key = key

    # --- Groq ---
    if key := os.environ.get("GROQ_API_KEY"):
        cfg.groq.api_key = key

    # --- Mistral AI ---
    if key := os.environ.get("MISTRAL_API_KEY"):
        cfg.mistral.api_key = key

    # --- xAI Grok ---
    if key := os.environ.get("XAI_API_KEY"):
        cfg.xai.api_key = key

    # --- Together AI ---
    if key := os.environ.get("TOGETHER_API_KEY"):
        cfg.together.api_key = key

    # --- DeepSeek ---
    if key := os.environ.get("DEEPSEEK_API_KEY"):
        cfg.deepseek.api_key = key

    # --- Anthropic Claude ---
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        cfg.anthropic.api_key = key

    # --- Google Gemini ---
    if key := os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        cfg.gemini.api_key = key

    # --- Azure OpenAI ---
    if key := os.environ.get("AZURE_OPENAI_API_KEY"):
        cfg.azure.api_key = key
    if endpoint := os.environ.get("AZURE_OPENAI_ENDPOINT"):
        cfg.azure.endpoint = endpoint

    # --- GitHub Models ---
    if token := os.environ.get("GITHUB_TOKEN"):
        cfg.github.token = token

    # --- GitHub Copilot ---
    if token := os.environ.get("COPILOT_TOKEN"):
        cfg.copilot.token = token

    # --- Ollama host (útil em Docker Compose: OLLAMA_HOST=http://ollama:11434) ---
    if host := os.environ.get("OLLAMA_HOST"):
        cfg.ollama.host = host

    # --- Ollama Cloud / proxy ---
    if key := os.environ.get("OLLAMA_API_KEY"):
        cfg.ollama.api_key = key

    # --- Bauer serve auth key ---
    if key := os.environ.get("BAUER_SERVE_API_KEY"):
        cfg.serve.api_key = key
