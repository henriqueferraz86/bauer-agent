"""G13: testes paramétricos para todos os 27+ providers do BauerConfig."""
from __future__ import annotations

import os
import pytest


ALL_PROVIDERS = [
    "openai", "groq", "anthropic", "mistral", "xai", "together", "deepseek",
    "gemini", "openrouter", "cohere", "perplexity", "fireworks", "huggingface",
    "cerebras", "sambanova", "nvidia", "lmstudio", "databricks", "moonshot",
    "alibaba", "ollama", "github", "copilot", "azure", "vertex", "opencode",
    "replicate", "novita", "ai21", "anyscale", "featherless", "hyperbolic",
    "inference", "ncompass",
]

# Providers com seções no BauerConfig (excluindo os novos G16 ainda não adicionados)
CORE_PROVIDERS = [
    "openai", "groq", "anthropic", "mistral", "xai", "together", "deepseek",
    "gemini", "openrouter", "cohere", "perplexity", "fireworks", "huggingface",
    "cerebras", "sambanova", "nvidia", "lmstudio", "databricks", "moonshot",
    "alibaba", "ollama", "github", "copilot", "azure", "vertex", "opencode",
]

ENV_VAR_MAP = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "xai": "XAI_API_KEY",
    "together": "TOGETHER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "cohere": "COHERE_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "sambanova": "SAMBANOVA_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "databricks": "DATABRICKS_TOKEN",
    "moonshot": "MOONSHOT_API_KEY",
    "alibaba": "ALIBABA_API_KEY",
    "github": "GITHUB_TOKEN",
    "copilot": "COPILOT_TOKEN",
    "azure": "AZURE_OPENAI_API_KEY",
    "vertex": "VERTEX_ACCESS_TOKEN",
}


def _make_minimal_config(provider: str = "ollama") -> dict:
    return {"model": {"provider": provider, "name": "test-model"}}


@pytest.mark.parametrize("provider", CORE_PROVIDERS)
def test_provider_section_exists_in_bauerconfig(provider):
    from bauer.config_loader import BauerConfig
    fields = BauerConfig.model_fields
    # provider sections are named exactly as the provider
    assert provider in fields, f"BauerConfig missing section for provider '{provider}'"


@pytest.mark.parametrize("provider", CORE_PROVIDERS)
def test_provider_section_can_be_instantiated(provider):
    """Each provider section should instantiate with no arguments (all defaults)."""
    from bauer.config_loader import BauerConfig
    field_info = BauerConfig.model_fields[provider]
    section_class = field_info.annotation
    if section_class and hasattr(section_class, "model_validate"):
        instance = section_class.model_validate({})
        assert instance is not None


@pytest.mark.parametrize("provider,env_var", list(ENV_VAR_MAP.items()))
def test_env_var_applied_to_config(provider, env_var, tmp_path, monkeypatch):
    """apply_env_to_config maps known env vars to the correct section."""
    monkeypatch.setenv(env_var, "test-secret-key")
    from bauer.env_loader import apply_env_to_config
    from bauer.config_loader import BauerConfig

    cfg = BauerConfig(model={"provider": "ollama", "name": "m"})
    apply_env_to_config(cfg)

    section = getattr(cfg, provider, None)
    if section is None:
        return  # provider not yet in config (G16 ones)

    # Most providers use api_key; github/copilot use token; vertex uses access_token
    secret_attrs = ["api_key", "token", "access_token"]
    found = any(
        getattr(section, attr, None) == "test-secret-key"
        for attr in secret_attrs
    )
    assert found, f"env var {env_var} not applied to cfg.{provider}"


@pytest.mark.parametrize("provider", ["openai", "groq", "mistral", "deepseek", "xai"])
def test_openai_compat_sections_have_timeout(provider):
    from bauer.config_loader import BauerConfig
    cfg = BauerConfig(model={"provider": provider, "name": "m"})
    section = getattr(cfg, provider)
    assert hasattr(section, "timeout_seconds")
    assert section.timeout_seconds >= 1


@pytest.mark.parametrize("provider", ["openai", "mistral", "groq"])
def test_extra_fields_forbidden(provider):
    """extra='forbid' should reject unknown fields on any section."""
    from pydantic import ValidationError
    from bauer.config_loader import BauerConfig
    field_info = BauerConfig.model_fields[provider]
    section_class = field_info.annotation
    if section_class and hasattr(section_class, "model_validate"):
        with pytest.raises(ValidationError):
            section_class.model_validate({"unknown_field_xyz": True})


def test_model_section_rejects_unknown_provider():
    from pydantic import ValidationError
    from bauer.config_loader import ModelSection
    with pytest.raises(ValidationError):
        ModelSection(provider="nonexistent_provider_xyz", name="model")


def test_load_config_minimal(tmp_path):
    import yaml
    from bauer.config_loader import load_config
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({"model": {"provider": "ollama", "name": "llama3"}}))
    cfg = load_config(cfg_file)
    assert cfg.model.provider == "ollama"
    assert cfg.model.name == "llama3"


def test_bauerconfig_defaults_are_sensible():
    from bauer.config_loader import BauerConfig
    cfg = BauerConfig(model={"provider": "ollama", "name": "m"})
    assert cfg.runtime.profile in ("low", "medium", "high")
    assert cfg.logging.level in ("debug", "info", "warning", "error")
    assert cfg.tools.safe_mode is True
    assert cfg.agent.tool_timeout_s > 0


@pytest.mark.parametrize("provider", ["openai", "anthropic", "groq", "mistral", "xai"])
def test_all_api_key_sections_default_empty(provider):
    from bauer.config_loader import BauerConfig
    cfg = BauerConfig(model={"provider": provider, "name": "m"})
    section = getattr(cfg, provider)
    api_key = getattr(section, "api_key", getattr(section, "token", None))
    assert api_key == "" or api_key is None


def test_ollama_default_host():
    from bauer.config_loader import BauerConfig
    cfg = BauerConfig(model={"provider": "ollama", "name": "m"})
    assert "localhost" in cfg.ollama.host or "11434" in cfg.ollama.host


def test_model_section_fallback_providers_default_empty():
    from bauer.config_loader import ModelSection
    m = ModelSection(provider="ollama", name="test")
    assert m.fallback_providers == []


def test_model_section_minimum_context_validation():
    from pydantic import ValidationError
    from bauer.config_loader import ModelSection
    with pytest.raises(ValidationError):
        ModelSection(provider="ollama", name="m", requested_context=4096, minimum_context=8192)


@pytest.mark.parametrize("provider", ["ollama", "lmstudio"])
def test_local_providers_have_host(provider):
    from bauer.config_loader import BauerConfig
    cfg = BauerConfig(model={"provider": provider, "name": "m"})
    section = getattr(cfg, provider)
    assert hasattr(section, "host")
    assert section.host.startswith("http")
