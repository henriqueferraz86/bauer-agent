"""Tests for `bauer/auxiliary_client.py` — slot-based LLM resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bauer.anthropic_client import AnthropicClient
from bauer.auxiliary_client import (
    VALID_SLOTS,
    call_aux_text,
    get_text_auxiliary_client,
)
from bauer.config_loader import (
    AuxiliarySection,
    AuxiliarySlot,
    BauerConfig,
    ModelSection,
)
from bauer.ollama_client import OllamaClient
from bauer.openai_client import OpenAIClient


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _cfg(provider: str = "openai", name: str = "gpt-4o-mini",
         auxiliary: AuxiliarySection | None = None) -> BauerConfig:
    """Build a minimum-viable config with the right `model` section."""
    return BauerConfig(
        model=ModelSection(provider=provider, name=name, requested_context=128_000),
        auxiliary=auxiliary or AuxiliarySection(),
    )


# ---------------------------------------------------------------------------
# Slot validation
# ---------------------------------------------------------------------------


def test_valid_slots_includes_expected():
    """Catch accidental renames — these names appear in user-facing config.yaml."""
    assert "kanban_decomposer" in VALID_SLOTS
    assert "triage_specifier" in VALID_SLOTS
    assert "compression_model" in VALID_SLOTS


def test_unknown_slot_returns_none_pair():
    client, model = get_text_auxiliary_client("bogus_slot", _cfg())
    assert client is None
    assert model is None


# ---------------------------------------------------------------------------
# Fallback to main model
# ---------------------------------------------------------------------------


def test_empty_slot_falls_back_to_main_model():
    """When auxiliary.<slot> is the default (empty), main model is used."""
    cfg = _cfg(provider="openai", name="gpt-4o-mini")
    client, model = get_text_auxiliary_client("kanban_decomposer", cfg)
    assert isinstance(client, OpenAIClient)
    assert model == "gpt-4o-mini"


def test_all_slots_share_same_fallback():
    cfg = _cfg(provider="openai", name="gpt-4o-mini")
    for slot in VALID_SLOTS:
        client, model = get_text_auxiliary_client(slot, cfg)
        assert client is not None, f"{slot} returned no client"
        assert model == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Slot override
# ---------------------------------------------------------------------------


def test_slot_override_routes_to_different_provider():
    """auxiliary.kanban_decomposer overrides the main model."""
    cfg = _cfg(
        provider="openai",
        name="gpt-4o",
        auxiliary=AuxiliarySection(
            kanban_decomposer=AuxiliarySlot(provider="ollama", model="qwen3:0.6b"),
        ),
    )
    client, model = get_text_auxiliary_client("kanban_decomposer", cfg)
    assert isinstance(client, OllamaClient)
    assert model == "qwen3:0.6b"


def test_slot_override_only_affects_that_slot():
    """Overriding `kanban_decomposer` doesn't affect `triage_specifier`."""
    cfg = _cfg(
        provider="openai",
        name="gpt-4o",
        auxiliary=AuxiliarySection(
            kanban_decomposer=AuxiliarySlot(provider="ollama", model="qwen3:0.6b"),
        ),
    )
    _, specifier_model = get_text_auxiliary_client("triage_specifier", cfg)
    _, decomposer_model = get_text_auxiliary_client("kanban_decomposer", cfg)
    assert specifier_model == "gpt-4o"      # fell back to main
    assert decomposer_model == "qwen3:0.6b"  # used override


def test_partial_override_provider_only_falls_back_to_main_model():
    """When slot has provider= but not model=, model falls back to main."""
    cfg = _cfg(
        provider="openai",
        name="gpt-4o-mini",
        auxiliary=AuxiliarySection(
            kanban_decomposer=AuxiliarySlot(provider="ollama", model=""),
        ),
    )
    client, model = get_text_auxiliary_client("kanban_decomposer", cfg)
    assert isinstance(client, OllamaClient)
    # provider overridden, model still falls back to main
    assert model == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Provider coverage
# ---------------------------------------------------------------------------


def test_anthropic_provider_returns_anthropic_client():
    cfg = _cfg(provider="anthropic", name="claude-3-5-sonnet-latest")
    client, model = get_text_auxiliary_client("triage_specifier", cfg)
    assert isinstance(client, AnthropicClient)
    assert model == "claude-3-5-sonnet-latest"


def test_ollama_provider_returns_ollama_client():
    cfg = _cfg(provider="ollama", name="qwen2.5-coder:3b")
    client, _ = get_text_auxiliary_client("compression_model", cfg)
    assert isinstance(client, OllamaClient)


@pytest.mark.parametrize("provider", [
    "openai", "groq", "mistral", "xai", "together", "deepseek",
    "openrouter", "opencode", "gemini", "github",
])
def test_openai_compat_providers_return_openai_client(provider: str):
    cfg = _cfg(provider=provider, name="some-model")
    client, model = get_text_auxiliary_client("kanban_decomposer", cfg)
    assert isinstance(client, OpenAIClient)
    assert model == "some-model"


def test_unsupported_provider_returns_none_pair():
    """Provider not in the lookup table → graceful (None, None), not raise."""
    cfg = _cfg(
        provider="openai",
        name="gpt-4o-mini",
        auxiliary=AuxiliarySection(
            kanban_decomposer=AuxiliarySlot(
                provider="fictional_unknown_provider", model="x",
            ),
        ),
    )
    client, model = get_text_auxiliary_client("kanban_decomposer", cfg)
    assert client is None
    assert model is None


# ---------------------------------------------------------------------------
# Failure modes — graceful degradation
# ---------------------------------------------------------------------------


def test_no_config_no_autoload_returns_none_pair(monkeypatch: pytest.MonkeyPatch):
    """cfg=None + missing config.yaml → (None, None), never raise."""
    monkeypatch.setenv("BAUER_CONFIG", "/nonexistent/path/config.yaml")
    client, model = get_text_auxiliary_client("kanban_decomposer", cfg=None)
    assert client is None
    assert model is None


def test_client_build_failure_returns_none_pair():
    """If the underlying client raises during construction, we degrade."""
    cfg = _cfg(provider="openai", name="gpt-4o-mini")
    with patch("bauer.auxiliary_client._build_client_for_provider",
               side_effect=RuntimeError("explosion in __init__")):
        client, model = get_text_auxiliary_client("kanban_decomposer", cfg)
        assert client is None
        assert model is None


def test_missing_main_model_no_slot_returns_none_pair():
    """When neither slot nor main config define a model, return (None, None).

    Uses a duck-typed stub instead of BauerConfig: the real ModelSection
    rejects empty providers via Literal, but `_resolve_slot` reads via
    `getattr` so anything with the right attribute names works.
    """
    class _StubModel:
        provider = ""
        name = ""

    class _StubAux:
        kanban_decomposer = AuxiliarySlot()
        triage_specifier = AuxiliarySlot()
        compression_model = AuxiliarySlot()

    class _StubCfg:
        model = _StubModel()
        auxiliary = _StubAux()

    client, model = get_text_auxiliary_client("kanban_decomposer", _StubCfg())
    assert client is None
    assert model is None


# ---------------------------------------------------------------------------
# call_aux_text — convenience wrapper
# ---------------------------------------------------------------------------


def test_call_aux_text_returns_fallback_when_unavailable():
    """call_aux_text returns the supplied fallback when no client builds."""
    class _StubCfg:
        class _M:
            provider = ""
            name = ""
        model = _M()
        auxiliary = AuxiliarySection()

    out = call_aux_text(
        "kanban_decomposer",
        [{"role": "user", "content": "hi"}],
        cfg=_StubCfg(),
        fallback="default-text",
    )
    assert out == "default-text"


def test_call_aux_text_joins_chunks_on_success():
    """When the client streams 'foo' + 'bar', we get 'foobar' back."""
    cfg = _cfg()

    class _FakeClient:
        default_model = "fake"

        def chat_stream(self, model, messages):
            yield "foo"
            yield "bar"

    fake = _FakeClient()
    with patch("bauer.auxiliary_client.get_text_auxiliary_client",
               return_value=(fake, "fake-model")):
        out = call_aux_text("kanban_decomposer", [{"role": "user", "content": "x"}],
                            cfg=cfg)
    assert out == "foobar"


def test_call_aux_text_swallows_runtime_errors():
    """If chat_stream raises mid-iteration, we get the fallback (silent failure)."""
    cfg = _cfg()

    class _FakeClient:
        default_model = "fake"

        def chat_stream(self, model, messages):
            raise RuntimeError("provider down")

    fake = _FakeClient()
    with patch("bauer.auxiliary_client.get_text_auxiliary_client",
               return_value=(fake, "fake-model")):
        out = call_aux_text(
            "kanban_decomposer",
            [{"role": "user", "content": "x"}],
            cfg=cfg,
            fallback="safe-fallback",
        )
    assert out == "safe-fallback"
