"""G16a: testes paramétricos para os 10 novos providers OpenAI-compat.

Providers: replicate, novita, ai21, anyscale, featherless, hyperbolic,
inference, ncompass, cloudflare, lepton.
"""
from __future__ import annotations

import pytest


NEW_PROVIDERS = [
    "replicate", "novita", "ai21", "anyscale", "featherless",
    "hyperbolic", "inference", "ncompass", "cloudflare", "lepton",
]

ENV_VAR_MAP = {
    "replicate": "REPLICATE_API_KEY",
    "novita": "NOVITA_API_KEY",
    "ai21": "AI21_API_KEY",
    "anyscale": "ANYSCALE_API_KEY",
    "featherless": "FEATHERLESS_API_KEY",
    "hyperbolic": "HYPERBOLIC_API_KEY",
    "inference": "INFERENCE_API_KEY",
    "ncompass": "NCOMPASS_API_KEY",
    "cloudflare": "CLOUDFLARE_API_KEY",
    "lepton": "LEPTON_API_KEY",
}

# Fragmento esperado no host de cada provider (sanity do _build_client).
HOST_FRAGMENT = {
    "replicate": "replicate.com",
    "novita": "novita.ai",
    "ai21": "ai21.com",
    "anyscale": "anyscale.com",
    "featherless": "featherless.ai",
    "hyperbolic": "hyperbolic.xyz",
    "inference": "inference.net",
    "ncompass": "ncompass.tech",
    "cloudflare": "cloudflare.com",
    "lepton": "lepton.run",
}


# ---------------------------------------------------------------------------
# Seções no BauerConfig
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", NEW_PROVIDERS)
def test_section_exists_in_bauerconfig(provider):
    from bauer.config_loader import BauerConfig
    assert provider in BauerConfig.model_fields, f"falta secao '{provider}'"


@pytest.mark.parametrize("provider", NEW_PROVIDERS)
def test_section_instantiates_with_defaults(provider):
    from bauer.config_loader import BauerConfig
    section_class = BauerConfig.model_fields[provider].annotation
    instance = section_class.model_validate({})
    assert instance is not None
    assert hasattr(instance, "api_key")
    assert hasattr(instance, "timeout_seconds")


@pytest.mark.parametrize("provider", NEW_PROVIDERS)
def test_model_section_accepts_provider(provider):
    from bauer.config_loader import ModelSection
    m = ModelSection(provider=provider, name="some-model")
    assert m.provider == provider


# ---------------------------------------------------------------------------
# Mapeamento de env vars
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider,env_var", list(ENV_VAR_MAP.items()))
def test_env_var_applied(provider, env_var, monkeypatch):
    monkeypatch.setenv(env_var, "secret-123")
    from bauer.env_loader import apply_env_to_config
    from bauer.config_loader import BauerConfig
    cfg = BauerConfig(model={"provider": "ollama", "name": "m"})
    apply_env_to_config(cfg)
    section = getattr(cfg, provider)
    assert section.api_key == "secret-123"


def test_cloudflare_account_id_from_env(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct-xyz")
    from bauer.env_loader import apply_env_to_config
    from bauer.config_loader import BauerConfig
    cfg = BauerConfig(model={"provider": "ollama", "name": "m"})
    apply_env_to_config(cfg)
    assert cfg.cloudflare.account_id == "acct-xyz"


# ---------------------------------------------------------------------------
# _build_client retorna OpenAIClient com host correto
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provider", NEW_PROVIDERS)
def test_build_client_returns_openai_client(provider):
    pytest.importorskip("typer")  # _build_client vive em bauer.cli (depende de typer)
    from bauer.cli import _build_client
    from bauer.openai_client import OpenAIClient
    from bauer.config_loader import BauerConfig
    cfg = BauerConfig(model={"provider": provider, "name": "test-model"})
    setattr(getattr(cfg, provider), "api_key", "k-123")
    client = _build_client(cfg)
    assert isinstance(client, OpenAIClient)


@pytest.mark.parametrize("provider", NEW_PROVIDERS)
def test_build_client_host_matches_provider(provider):
    pytest.importorskip("typer")
    from bauer.cli import _build_client
    from bauer.config_loader import BauerConfig
    cfg = BauerConfig(model={"provider": provider, "name": "test-model"})
    setattr(getattr(cfg, provider), "api_key", "k-123")
    if provider == "cloudflare":
        cfg.cloudflare.account_id = "acct1"
    client = _build_client(cfg)
    host = getattr(client, "host", "")
    assert HOST_FRAGMENT[provider] in host, f"{provider}: host inesperado {host!r}"
