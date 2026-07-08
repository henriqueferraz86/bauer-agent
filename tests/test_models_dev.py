"""Testes para bauer/models_dev.py — catálogo models.dev offline-first."""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import bauer.models_dev as md


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_CATALOG = {
    "anthropic": {
        "name": "Anthropic",
        "env": ["ANTHROPIC_API_KEY"],
        "api": "https://api.anthropic.com",
        "models": {
            "claude-3-5-sonnet-20241022": {
                "name": "Claude 3.5 Sonnet",
                "family": "claude-3",
                "tool_call": True,
                "reasoning": False,
                "attachment": True,
                "open_weights": False,
                "limit": {"context": 200000, "output": 8192},
                "cost": {"input": 3.0, "output": 15.0},
                "modalities": {"input": ["text", "image"], "output": ["text"]},
            },
            "claude-3-haiku-20240307": {
                "name": "Claude 3 Haiku",
                "family": "claude-3",
                "tool_call": True,
                "reasoning": False,
                "attachment": False,
                "limit": {"context": 200000, "output": 4096},
                "cost": {"input": 0.25, "output": 1.25},
                "modalities": {"input": ["text"], "output": ["text"]},
            },
            "claude-tts-model": {  # ruído — deve ser filtrado por list_agentic_models
                "name": "Claude TTS",
                "family": "",
                "tool_call": False,
                "limit": {"context": 0, "output": 0},
            },
        },
    },
    "groq": {
        "name": "Groq",
        "env": ["GROQ_API_KEY"],
        "api": "https://api.groq.com",
        "models": {
            "llama-3.1-70b-versatile": {
                "name": "Llama 3.1 70B",
                "family": "llama-3",
                "tool_call": True,
                "reasoning": False,
                "attachment": False,
                "limit": {"context": 131072, "output": 8192},
                "cost": {"input": 0.59, "output": 0.79},
                "modalities": {"input": ["text"], "output": ["text"]},
            },
        },
    },
}


@pytest.fixture(autouse=True)
def reset_cache():
    """Limpa o cache in-memory antes de cada teste."""
    md._models_dev_cache = {}
    md._models_dev_cache_time = 0
    yield
    md._models_dev_cache = {}
    md._models_dev_cache_time = 0


@pytest.fixture()
def patched_catalog(tmp_path, monkeypatch):
    """Injeta catalog fake sem tocar na rede."""
    monkeypatch.setattr(md, "_models_dev_cache", FAKE_CATALOG.copy())
    monkeypatch.setattr(md, "_models_dev_cache_time", time.time())
    monkeypatch.setattr(md, "_get_cache_path", lambda: tmp_path / "test_cache.json")
    return FAKE_CATALOG


# ---------------------------------------------------------------------------
# fetch_models_dev
# ---------------------------------------------------------------------------

def test_fetch_returns_inmem_cache(patched_catalog):
    result = md.fetch_models_dev()
    assert "anthropic" in result
    assert "groq" in result


def test_fetch_from_disk_cache(tmp_path, monkeypatch):
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps(FAKE_CATALOG), encoding="utf-8")
    monkeypatch.setattr(md, "_get_cache_path", lambda: cache_file)

    result = md.fetch_models_dev()
    assert "anthropic" in result


def test_fetch_network_fallback(tmp_path, monkeypatch):
    """Sem cache → busca na rede (mockada)."""
    monkeypatch.setattr(md, "_get_cache_path", lambda: tmp_path / "nonexistent.json")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = FAKE_CATALOG
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=mock_resp) as mock_get:
        result = md.fetch_models_dev()
        mock_get.assert_called_once()
    assert "anthropic" in result


def test_fetch_network_failure_uses_stale_disk(tmp_path, monkeypatch):
    """Falha de rede → usa cache de disco expirado."""
    cache_file = tmp_path / "cache.json"
    cache_file.write_text(json.dumps(FAKE_CATALOG), encoding="utf-8")
    import os
    old_time = time.time() - 7200  # 2h atrás (expirado)
    os.utime(cache_file, (old_time, old_time))
    monkeypatch.setattr(md, "_get_cache_path", lambda: cache_file)

    with patch("httpx.get", side_effect=Exception("network error")):
        result = md.fetch_models_dev()
    assert "anthropic" in result  # degradou para cache expirado


def test_fetch_returns_empty_on_total_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "_get_cache_path", lambda: tmp_path / "nonexistent.json")
    with patch("httpx.get", side_effect=Exception("network error")):
        result = md.fetch_models_dev()
    assert result == {}


# ---------------------------------------------------------------------------
# lookup_context_window
# ---------------------------------------------------------------------------

def test_lookup_context_exact(patched_catalog):
    ctx = md.lookup_context_window("anthropic", "claude-3-5-sonnet-20241022")
    assert ctx == 200000


def test_lookup_context_case_insensitive(patched_catalog):
    ctx = md.lookup_context_window("anthropic", "CLAUDE-3-5-SONNET-20241022")
    assert ctx == 200000


def test_lookup_context_unknown_provider(patched_catalog):
    ctx = md.lookup_context_window("nonexistent", "some-model")
    assert ctx is None


def test_lookup_context_unknown_model(patched_catalog):
    ctx = md.lookup_context_window("anthropic", "claude-nonexistent")
    assert ctx is None


# ---------------------------------------------------------------------------
# get_model_info
# ---------------------------------------------------------------------------

def test_get_model_info_full(patched_catalog):
    info = md.get_model_info("anthropic", "claude-3-5-sonnet-20241022")
    assert info is not None
    assert info.id == "claude-3-5-sonnet-20241022"
    assert info.context_window == 200000
    assert info.tool_call is True
    assert info.supports_vision() is True
    assert info.cost_input == 3.0
    assert info.cost_output == 15.0
    assert info.has_cost_data() is True


def test_get_model_info_not_found(patched_catalog):
    assert md.get_model_info("anthropic", "ghost-model") is None


def test_get_model_info_unknown_provider(patched_catalog):
    assert md.get_model_info("unknown_prov", "some-model") is None


# ---------------------------------------------------------------------------
# get_provider_info
# ---------------------------------------------------------------------------

def test_get_provider_info(patched_catalog):
    info = md.get_provider_info("anthropic")
    assert info is not None
    assert info.id == "anthropic"
    assert "ANTHROPIC_API_KEY" in info.env
    assert info.model_count == 3


def test_get_provider_info_not_found(patched_catalog):
    assert md.get_provider_info("nonexistent") is None


# ---------------------------------------------------------------------------
# list_provider_models / list_agentic_models
# ---------------------------------------------------------------------------

def test_list_provider_models(patched_catalog):
    models = md.list_provider_models("anthropic")
    assert "claude-3-5-sonnet-20241022" in models
    assert "claude-3-haiku-20240307" in models
    assert "claude-tts-model" in models  # não filtrado aqui


def test_list_agentic_models_filters_noise(patched_catalog):
    models = md.list_agentic_models("anthropic")
    # tool_call=True e sem padrão de ruído
    assert "claude-3-5-sonnet-20241022" in models
    assert "claude-3-haiku-20240307" in models
    # TTS deve ser filtrado (tool_call=False)
    assert "claude-tts-model" not in models


def test_list_agentic_models_empty_provider(patched_catalog):
    assert md.list_agentic_models("nonexistent") == []


# ---------------------------------------------------------------------------
# get_model_capabilities
# ---------------------------------------------------------------------------

def test_get_model_capabilities(patched_catalog):
    caps = md.get_model_capabilities("anthropic", "claude-3-5-sonnet-20241022")
    assert caps is not None
    assert caps["supports_tools"] is True
    assert caps["supports_vision"] is True
    assert caps["supports_reasoning"] is False
    assert caps["context_window"] == 200000
    assert caps["max_output_tokens"] == 8192


def test_get_model_capabilities_not_found(patched_catalog):
    assert md.get_model_capabilities("anthropic", "ghost") is None


# ---------------------------------------------------------------------------
# PROVIDER_TO_MODELS_DEV mapping
# ---------------------------------------------------------------------------

def test_all_28_providers_mapped():
    """Garante que todos os providers Bauer têm entrada no mapeamento."""
    expected = {
        "anthropic", "openai", "openai-api", "openrouter", "gemini", "groq",
        "mistral", "xai", "together", "deepseek", "github", "copilot",
        "opencode", "ollama", "cohere", "perplexity", "fireworks", "huggingface",
        "nvidia", "moonshot", "alibaba", "vertex", "azure", "lmstudio",
        "databricks", "sambanova", "cerebras", "custom",
    }
    assert expected == set(md.PROVIDER_TO_MODELS_DEV.keys())


# ---------------------------------------------------------------------------
# ModelInfo helpers
# ---------------------------------------------------------------------------

def test_model_info_format_cost(patched_catalog):
    info = md.get_model_info("anthropic", "claude-3-5-sonnet-20241022")
    cost_str = info.format_cost()
    assert "$3.00/M in" in cost_str
    assert "$15.00/M out" in cost_str


def test_model_info_format_capabilities(patched_catalog):
    info = md.get_model_info("anthropic", "claude-3-5-sonnet-20241022")
    caps = info.format_capabilities()
    assert "tools" in caps
    assert "vision" in caps


def test_model_info_no_cost(patched_catalog):
    # Haiku tem custo mas vamos testar formato geral
    info = md.get_model_info("anthropic", "claude-3-haiku-20240307")
    assert info.has_cost_data() is True
    assert info.supports_vision() is False


# ---------------------------------------------------------------------------
# Disk cache save/load round-trip
# ---------------------------------------------------------------------------

def test_disk_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(md, "_get_cache_path", lambda: tmp_path / "cache.json")
    md._save_disk_cache(FAKE_CATALOG)
    loaded = md._load_disk_cache()
    assert "anthropic" in loaded
    assert loaded["anthropic"]["name"] == "Anthropic"


# ---------------------------------------------------------------------------
# _is_free_model — fonte única de classificação gratuita
# ---------------------------------------------------------------------------

class TestIsFreeModel:
    def test_always_free_providers(self):
        assert md._is_free_model("ollama", "llama3") is True
        assert md._is_free_model("lmstudio", "qualquer-coisa") is True
        assert md._is_free_model("cerebras", "llama3.1-8b") is True

    def test_suffix_free_openrouter(self):
        assert md._is_free_model("openrouter", "meta-llama/llama-3.2-3b-instruct:free") is True
        assert md._is_free_model("openrouter", "openrouter/free") is True

    def test_suffix_free_together_and_opencode(self):
        assert md._is_free_model("together", "Meta-Llama-3.1-8B-Instruct-Turbo-Free") is True
        assert md._is_free_model("opencode", "mimo-v2-flash-free") is True

    def test_paid_models_not_free(self):
        assert md._is_free_model("openrouter", "openai/gpt-4o") is False
        assert md._is_free_model("anthropic", "claude-3-5-sonnet") is False
        assert md._is_free_model("groq", "llama-3.1-8b-instant") is False

    def test_cost_zero_is_not_free(self):
        # Regressão do bug do lyria: custo 0 por token != gratuito.
        # Modelos de áudio/imagem cobram por request/segundo → 402.
        # Sem sinal explícito (provider sempre-grátis ou sufixo), é pago.
        assert md._is_free_model("openrouter", "google/lyria-3-pro-preview") is False
        # provider desconhecido, mesmo que custo fosse 0, não é grátis
        assert md._is_free_model("randomvendor", "qualquer-modelo") is False


# ---------------------------------------------------------------------------
# _is_chat_capable — filtro de modalidade
# ---------------------------------------------------------------------------

class TestIsChatCapable:
    def test_text_output_is_chat(self):
        assert md._is_chat_capable(["text"]) is True
        assert md._is_chat_capable(["text", "audio"]) is True  # misto conta

    def test_media_only_not_chat(self):
        assert md._is_chat_capable(["image"]) is False
        assert md._is_chat_capable(["video"]) is False
        assert md._is_chat_capable(["audio"]) is False

    def test_unknown_modality_assumes_chat(self):
        # Sem metadado → não esconder (assume chat)
        assert md._is_chat_capable([]) is True
        assert md._is_chat_capable(None) is True
