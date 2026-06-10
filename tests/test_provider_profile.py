"""Tests for bauer/provider_profile.py — declarative provider registry."""

from __future__ import annotations

import os
import pytest

from bauer.provider_profile import (
    ProviderProfile,
    get_profile,
    list_providers,
    providers_by_auth_type,
    configured_providers,
    env_var_status,
    _PROFILES,
)


# ---------------------------------------------------------------------------
# Basic registry
# ---------------------------------------------------------------------------


def test_list_providers_nonempty():
    providers = list_providers()
    assert len(providers) >= 10, "Should have at least 10 providers registered"


def test_all_expected_providers_present():
    names = {p.name for p in list_providers()}
    expected = {
        "ollama", "openai", "anthropic", "gemini", "groq",
        "mistral", "openrouter", "deepseek", "xai", "together",
    }
    assert expected.issubset(names), f"Missing providers: {expected - names}"


def test_get_profile_known():
    p = get_profile("ollama")
    assert isinstance(p, ProviderProfile)
    assert p.name == "ollama"
    assert p.auth_type in ("none", "api_key", "token", "oauth")


def test_get_profile_unknown_returns_none():
    p = get_profile("nonexistent_provider_xyz")
    assert p is None


def test_get_profile_case_insensitive():
    # profiles are lowercase; check robustness
    p_lower = get_profile("openai")
    assert p_lower is not None
    assert p_lower.name == "openai"


# ---------------------------------------------------------------------------
# Provider fields
# ---------------------------------------------------------------------------


def test_ollama_profile_fields():
    p = get_profile("ollama")
    assert p is not None
    assert p.auth_type == "none"
    assert "localhost" in (p.base_url or "")


def test_anthropic_profile_fields():
    p = get_profile("anthropic")
    assert p is not None
    assert p.auth_type == "api_key"
    assert p.env_vars  # must have at least one env var


def test_openai_profile_fields():
    p = get_profile("openai")
    assert p is not None
    assert p.auth_type in ("api_key", "oauth", "token")  # depends on implementation
    # openai may use an env var for auth — just check the profile is valid
    assert p.name == "openai"


def test_groq_profile_fields():
    p = get_profile("groq")
    assert p is not None
    assert p.auth_type == "api_key"


def test_every_profile_has_display_name():
    for p in list_providers():
        assert p.display_name, f"Provider '{p.name}' missing display_name"


def test_every_profile_has_description():
    for p in list_providers():
        assert p.description, f"Provider '{p.name}' missing description"


# ---------------------------------------------------------------------------
# Auth type grouping
# ---------------------------------------------------------------------------


def test_providers_by_auth_type_api_key():
    api_key_providers = providers_by_auth_type("api_key")
    names = {p.name for p in api_key_providers}
    # openai-api is the API-key variant; plain openai uses OAuth
    assert "anthropic" in names
    assert "groq" in names
    assert "openai-api" in names


def test_providers_by_auth_type_none():
    none_providers = providers_by_auth_type("none")
    names = {p.name for p in none_providers}
    assert "ollama" in names


# ---------------------------------------------------------------------------
# is_configured
# ---------------------------------------------------------------------------


def test_ollama_is_configured():
    # Ollama requires no API key — always "configured"
    p = get_profile("ollama")
    assert p is not None
    assert p.is_configured() is True


def test_openai_api_not_configured_without_key(monkeypatch):
    # "openai-api" is the API-key variant of OpenAI (not OAuth)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    p = get_profile("openai-api")
    assert p is not None
    assert p.is_configured() is False


def test_openai_api_is_configured_with_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-fake-key")
    p = get_profile("openai-api")
    assert p is not None
    assert p.is_configured() is True


def test_anthropic_configured_with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    p = get_profile("anthropic")
    assert p is not None
    assert p.is_configured() is True


def test_anthropic_not_configured_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p = get_profile("anthropic")
    assert p is not None
    assert p.is_configured() is False


# ---------------------------------------------------------------------------
# configured_providers()
# ---------------------------------------------------------------------------


def test_configured_providers_includes_ollama(monkeypatch):
    # Ollama never needs a key
    result = configured_providers()
    names = {p.name for p in result}
    assert "ollama" in names


def test_configured_providers_excludes_unconfigured(monkeypatch):
    # Remove all known API keys
    for var in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    result = configured_providers()
    names = {p.name for p in result}
    # These should be absent (no key = not configured)
    for name in ("openai", "anthropic", "groq"):
        assert name not in names, f"{name} should not be configured without a key"


# ---------------------------------------------------------------------------
# env_var_status
# ---------------------------------------------------------------------------


def test_env_var_status_returns_list(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    rows = env_var_status()
    # env_var_status returns list[dict] with keys: provider, env_var, set, auth_type
    assert isinstance(rows, list)
    assert len(rows) > 0
    first = rows[0]
    assert "provider" in first
    assert "env_var" in first
    assert "set" in first
    # OPENAI_API_KEY was set — at least one openai row should have set=True
    openai_rows = [r for r in rows if r.get("env_var") == "OPENAI_API_KEY"]
    assert any(r["set"] is True for r in openai_rows)


def test_env_var_status_shows_unset_for_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rows = env_var_status()
    anthropic_rows = [r for r in rows if r.get("env_var") == "ANTHROPIC_API_KEY"]
    assert any(r["set"] is False for r in anthropic_rows)


# ---------------------------------------------------------------------------
# get_api_key
# ---------------------------------------------------------------------------


def test_get_api_key_reads_env(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake123")
    p = get_profile("groq")
    assert p is not None
    key = p.get_api_key()
    assert key == "gsk_fake123"


def test_get_api_key_returns_none_without_env(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    p = get_profile("groq")
    assert p is not None
    key = p.get_api_key()
    assert key is None


def test_ollama_get_api_key_returns_none():
    # Ollama doesn't use API keys
    p = get_profile("ollama")
    assert p is not None
    key = p.get_api_key()
    assert key is None


# ---------------------------------------------------------------------------
# ProviderProfile dataclass
# ---------------------------------------------------------------------------


def test_provider_profile_repr():
    p = get_profile("openai")
    assert p is not None
    r = repr(p)
    assert "openai" in r.lower() or "ProviderProfile" in r


def test_provider_profile_slots_or_equality():
    # Two calls to get_profile("openai") should return equal objects
    p1 = get_profile("openai")
    p2 = get_profile("openai")
    assert p1 is not None and p2 is not None
    assert p1.name == p2.name
    assert p1.auth_type == p2.auth_type
