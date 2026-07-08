"""Wizard interativo de primeiro uso para o Bauer Agent.

Guia o usuário por provider, API key, modelo e workspace na primeira
execução, gerando um config.yaml válido e um .env com os secrets.

Uso programático::

    from bauer.init_wizard import run_init_wizard
    run_init_wizard()

Uso via CLI::

    bauer init
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Providers com defaults
# ---------------------------------------------------------------------------

_PROVIDER_DEFAULTS: dict[str, dict] = {
    "ollama": {
        "label": "Ollama (local, gratuito)",
        "env_var": None,
        "default_model": "qwen2.5-coder:3b",
        "host_key": "ollama.host",
        "host_default": "http://localhost:11434",
        "needs_key": False,
    },
    "openai": {
        "label": "OpenAI (GPT-4o, GPT-4o-mini)",
        "env_var": "OPENAI_API_KEY",
        "default_model": "gpt-4o-mini",
        "needs_key": True,
    },
    "anthropic": {
        "label": "Anthropic Claude (claude-3-5-haiku, claude-sonnet-4-6)",
        "env_var": "ANTHROPIC_API_KEY",
        "default_model": "claude-haiku-4-5-20251001",
        "needs_key": True,
    },
    "groq": {
        "label": "Groq (ultra-rapido, llama3/mixtral gratuito)",
        "env_var": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
        "needs_key": True,
    },
    "openrouter": {
        "label": "OpenRouter (200+ modelos, uma chave so)",
        "env_var": "OPENROUTER_API_KEY",
        "default_model": "google/gemini-flash-1.5",
        "needs_key": True,
    },
    "deepseek": {
        "label": "DeepSeek (deepseek-chat / R1)",
        "env_var": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "needs_key": True,
    },
    "gemini": {
        "label": "Google Gemini (gemini-2.0-flash)",
        "env_var": "GEMINI_API_KEY",
        "default_model": "gemini-2.0-flash",
        "needs_key": True,
    },
    "mistral": {
        "label": "Mistral AI (mistral-large, codestral)",
        "env_var": "MISTRAL_API_KEY",
        "default_model": "mistral-large-latest",
        "needs_key": True,
    },
}

_PROVIDERS = list(_PROVIDER_DEFAULTS.keys())


# ---------------------------------------------------------------------------
# I/O helpers (mockable for tests)
# ---------------------------------------------------------------------------

def _ask(prompt: str, default: str = "", secret: bool = False,
         io_ask: Callable[[str], str] | None = None) -> str:
    """Ask the user a question and return their answer."""
    if io_ask is not None:
        return io_ask(prompt) or default
    hint = f" [{default}]" if default else ""
    full_prompt = f"{prompt}{hint}: "
    if secret:
        try:
            import getpass
            val = getpass.getpass(full_prompt)
        except Exception:
            val = input(full_prompt)
    else:
        val = input(full_prompt)
    return val.strip() or default


def _choose(prompt: str, options: list[str],
            io_ask: Callable[[str], str] | None = None) -> str:
    """Show a numbered menu and return the selected option."""
    for i, opt in enumerate(options, 1):
        pdef = _PROVIDER_DEFAULTS.get(opt, {})
        label = pdef.get("label", opt)
        print(f"  {i}. {opt} — {label}")
    while True:
        raw = _ask(f"\n{prompt} (1-{len(options)})", default="1", io_ask=io_ask)
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        except ValueError:
            pass
        print(f"  Por favor escolha um numero entre 1 e {len(options)}.")


# ---------------------------------------------------------------------------
# Config / .env writers
# ---------------------------------------------------------------------------

def _write_env(env_path: Path, key: str, value: str) -> None:
    """Append or update a KEY=value line in the .env file."""
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _update_config_yaml(config_path: Path, provider: str, model: str, workspace: str) -> None:
    """Write / update the minimal config.yaml with the chosen provider and model."""
    try:
        import yaml
    except ImportError:
        yaml = None  # type: ignore[assignment]

    if yaml and config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    elif config_path.exists():
        raw = {}
    else:
        raw = {}

    raw.setdefault("model", {})["provider"] = provider
    raw.setdefault("model", {})["name"] = model
    raw["model"].setdefault("requested_context", 8192)
    raw.setdefault("agent", {})["workspace"] = workspace

    config_path.parent.mkdir(parents=True, exist_ok=True)
    if yaml:
        config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
    else:
        # Minimal fallback without pyyaml
        lines = [
            "model:",
            f"  provider: {provider}",
            f"  name: {model}",
            "agent:",
            f"  workspace: {workspace}",
        ]
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def run_init_wizard(
    config_path: str | Path = "config.yaml",
    env_path: str | Path = ".env",
    force: bool = False,
    io_ask: Callable[[str], str] | None = None,
    print_fn: Callable[[str], None] = print,
) -> bool:
    """Run the interactive first-use wizard.

    Returns True on success, False if the user cancelled.

    Parameters
    ----------
    config_path:
        Path to write/update config.yaml.
    env_path:
        Path to write/update .env with API keys.
    force:
        Skip the overwrite confirmation if config.yaml already exists.
    io_ask:
        Override for user input (used in tests).
    print_fn:
        Override for output (used in tests).
    """
    config_path = Path(config_path)
    env_path = Path(env_path)

    print_fn("\n=== Bauer Agent — Configuracao inicial ===\n")

    # Warn if config already exists
    if config_path.exists() and not force:
        print_fn(f"  config.yaml ja existe em {config_path.resolve()}")
        ans = _ask("  Sobrescrever? (s/N)", default="n", io_ask=io_ask).lower()
        if ans not in ("s", "sim", "y", "yes"):
            print_fn("  Configuracao cancelada.")
            return False

    # Step 1 — choose provider
    print_fn("Passo 1: Escolha o provider de LLM\n")
    provider = _choose("Provider", _PROVIDERS, io_ask=io_ask)
    pdef = _PROVIDER_DEFAULTS[provider]
    print_fn(f"  Selecionado: {provider}\n")

    # Step 2 — API key (if needed)
    env_key: str | None = pdef.get("env_var")
    if pdef.get("needs_key") and env_key:
        existing = os.environ.get(env_key, "")
        if existing:
            print_fn(f"Passo 2: Chave de API — encontrada em {env_key} (variavel de ambiente)\n")
        else:
            print_fn(f"Passo 2: Chave de API para {provider}")
            print_fn(f"  (sera gravada em {env_path} como {env_key})\n")
            api_key = _ask(f"  {env_key}", secret=True, io_ask=io_ask)
            if api_key:
                _write_env(env_path, env_key, api_key)
                print_fn(f"  Chave gravada em {env_path}\n")
            else:
                print_fn("  Chave vazia — voce pode configurar depois via 'bauer config set'\n")
    else:
        if provider == "ollama":
            host_key = pdef.get("host_key", "ollama.host")
            host_default = pdef.get("host_default", "http://localhost:11434")
            print_fn("Passo 2: Host do Ollama\n")
            host_val = _ask("  Host", default=host_default, io_ask=io_ask)
            if host_val != host_default:
                try:
                    from .config_admin import set_config_value
                    set_config_value(host_key, host_val, config_path)
                except Exception:
                    pass  # config ainda nao existe, sera criado abaixo
            print_fn("")

    # Step 3 — model name
    default_model = pdef.get("default_model", "")
    print_fn("Passo 3: Nome do modelo")
    model = _ask("  Modelo", default=default_model, io_ask=io_ask)
    print_fn("")

    # Step 4 — workspace directory
    default_workspace = "./workspace"
    print_fn("Passo 4: Diretorio de workspace (onde arquivos e sessoes sao gravados)")
    workspace = _ask("  Workspace", default=default_workspace, io_ask=io_ask)
    print_fn("")

    # Write config
    _update_config_yaml(config_path, provider, model, workspace)

    # Step 5 — validate
    print_fn("Validando configuracao...")
    try:
        from .config_loader import load_config
        cfg = load_config(str(config_path))
        print_fn(f"  config.yaml OK — provider={cfg.model.provider}, model={cfg.model.name}")
    except Exception as exc:
        print_fn(f"  AVISO: config.yaml pode ter problemas: {exc}")

    # Summary
    print_fn("\n=== Configuracao concluida ===")
    print_fn(f"  config.yaml : {config_path.resolve()}")
    if env_path.exists():
        print_fn(f"  .env        : {env_path.resolve()}")
    print_fn(f"  Provider    : {provider}")
    print_fn(f"  Modelo      : {model}")
    print_fn(f"  Workspace   : {workspace}")
    print_fn("\nProximos passos:")
    print_fn("  bauer doctor          # verificar saude do ambiente")
    print_fn("  bauer chat            # iniciar chat interativo")
    if provider == "ollama":
        print_fn("  ollama pull " + model + "   # baixar o modelo se ainda nao tiver")
    print_fn("")

    return True
