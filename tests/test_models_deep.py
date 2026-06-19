"""Tests for G27 — models.dev deep integration.

Covers: auto-refresh daemon, catalog_models() filtering, CLI models catalog,
and Telegram /model keyboard metadata enrichment.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from bauer.models_dev import (
    catalog_models,
    fetch_models_dev,
    start_background_refresh,
    stop_background_refresh,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_CATALOG: Dict[str, Any] = {
    "openai": {
        "models": {
            "gpt-4o": {
                "description": "GPT-4o",
                "limit": {"context": 128000, "output": 4096},
                "pricing": {"input": 5.0, "output": 15.0},
                "supports_tools": True,
                "supports_vision": True,
                "supports_reasoning": False,
            },
            "gpt-3.5-turbo": {
                "description": "GPT-3.5",
                "limit": {"context": 16385, "output": 4096},
                "pricing": {"input": 0.5, "output": 1.5},
                "supports_tools": True,
                "supports_vision": False,
            },
        }
    },
    "anthropic": {
        "models": {
            "claude-3-5-sonnet": {
                "description": "Claude 3.5 Sonnet",
                "limit": {"context": 200000, "output": 8192},
                "pricing": {"input": 3.0, "output": 15.0},
                "supports_tools": True,
                "supports_vision": True,
                "supports_reasoning": False,
            },
            "claude-3-opus": {
                "description": "Claude 3 Opus",
                "limit": {"context": 200000, "output": 4096},
                "pricing": {"input": 15.0, "output": 75.0},
                "supports_tools": True,
                "supports_vision": True,
            },
        }
    },
    "groq": {
        "models": {
            "llama-3.1-8b": {
                "description": "Llama 3.1 8B",
                "limit": {"context": 131072, "output": 8192},
                "pricing": {"input": 0.05, "output": 0.08},
                "supports_tools": True,
                "supports_vision": False,
            }
        }
    },
}


# ---------------------------------------------------------------------------
# Auto-refresh daemon
# ---------------------------------------------------------------------------


class TestBackgroundRefreshDaemon:
    def setup_method(self):
        stop_background_refresh()
        # Allow thread to stop
        time.sleep(0.05)

    def teardown_method(self):
        stop_background_refresh()

    def test_start_creates_daemon_thread(self):
        from bauer import models_dev as md

        start_background_refresh(interval_sec=9999)
        assert md._refresh_thread is not None
        assert md._refresh_thread.is_alive()
        assert md._refresh_thread.daemon is True

    def test_start_is_idempotent(self):
        from bauer import models_dev as md

        start_background_refresh(interval_sec=9999)
        t1 = md._refresh_thread
        start_background_refresh(interval_sec=9999)
        t2 = md._refresh_thread
        assert t1 is t2

    def test_stop_sets_flag(self):
        from bauer import models_dev as md

        start_background_refresh(interval_sec=9999)
        stop_background_refresh()
        assert md._refresh_stop is True

    def test_thread_name(self):
        from bauer import models_dev as md

        start_background_refresh(interval_sec=9999)
        assert md._refresh_thread.name == "models_dev_refresh"

    def test_restart_after_stop(self):
        from bauer import models_dev as md

        start_background_refresh(interval_sec=9999)
        first_thread = md._refresh_thread
        stop_background_refresh()
        time.sleep(0.05)
        md._refresh_thread = None  # simulate dead thread
        start_background_refresh(interval_sec=9999)
        assert md._refresh_thread is not None

    def test_daemon_does_not_block_process_exit(self):
        """Thread must be daemon=True so process can exit cleanly."""
        from bauer import models_dev as md

        start_background_refresh(interval_sec=9999)
        assert md._refresh_thread.daemon is True


# ---------------------------------------------------------------------------
# catalog_models() — filter logic
# ---------------------------------------------------------------------------


class TestCatalogModels:
    def _mock_fetch(self):
        return patch("bauer.models_dev.fetch_models_dev", return_value=_SAMPLE_CATALOG)

    def test_returns_list(self):
        with self._mock_fetch():
            results = catalog_models()
        assert isinstance(results, list)

    def test_all_providers_without_filter(self):
        with self._mock_fetch():
            results = catalog_models()
        providers = {r["provider"] for r in results}
        assert "openai" in providers
        assert "anthropic" in providers
        assert "groq" in providers

    def test_filter_by_provider(self):
        with self._mock_fetch():
            results = catalog_models(provider="openai")
        assert all(r["provider"] == "openai" for r in results)
        assert len(results) == 2

    def test_filter_by_provider_case_insensitive(self):
        with self._mock_fetch():
            results = catalog_models(provider="OpenAI")
        assert all(r["provider"] == "openai" for r in results)

    def test_filter_by_nonexistent_provider_empty(self):
        with self._mock_fetch():
            results = catalog_models(provider="nonexistent_xyz")
        assert results == []

    def test_filter_by_capability_tools(self):
        with self._mock_fetch():
            results = catalog_models(capability="tools")
        # All models in sample support tools
        assert len(results) > 0
        for r in results:
            assert "tools" in r["capabilities"]

    def test_filter_by_capability_vision(self):
        with self._mock_fetch():
            results = catalog_models(capability="vision")
        # Only gpt-4o, claude-3-5-sonnet, claude-3-opus support vision
        for r in results:
            assert "vision" in r["capabilities"]
        model_ids = {r["id"] for r in results}
        assert "gpt-4o" in model_ids
        assert "gpt-3.5-turbo" not in model_ids

    def test_filter_by_capability_case_insensitive(self):
        with self._mock_fetch():
            results_lower = catalog_models(capability="vision")
            results_upper = catalog_models(capability="Vision")
        assert len(results_lower) == len(results_upper)

    def test_filter_by_nonexistent_capability_empty(self):
        with self._mock_fetch():
            results = catalog_models(capability="telepathy")
        assert results == []

    def test_filter_by_max_cost_per_m(self):
        with self._mock_fetch():
            # Only llama-3.1-8b ($0.05) and gpt-3.5-turbo ($0.5) should pass at $1/M
            results = catalog_models(max_cost_per_m=1.0)
        cost_filtered = {r["id"] for r in results}
        assert "llama-3.1-8b" in cost_filtered
        assert "gpt-3.5-turbo" in cost_filtered
        assert "gpt-4o" not in cost_filtered
        assert "claude-3-opus" not in cost_filtered

    def test_combine_provider_and_capability_filter(self):
        with self._mock_fetch():
            results = catalog_models(provider="openai", capability="vision")
        assert all(r["provider"] == "openai" for r in results)
        assert all("vision" in r["capabilities"] for r in results)
        model_ids = {r["id"] for r in results}
        assert "gpt-4o" in model_ids
        assert "gpt-3.5-turbo" not in model_ids

    def test_result_has_required_keys(self):
        with self._mock_fetch():
            results = catalog_models(provider="openai")
        for r in results:
            assert "id" in r
            assert "provider" in r
            assert "context_window" in r
            assert "cost_in" in r
            assert "cost_out" in r
            assert "capabilities" in r
            assert "description" in r

    def test_context_window_extracted(self):
        with self._mock_fetch():
            results = catalog_models(provider="openai")
        gpt4o = next(r for r in results if r["id"] == "gpt-4o")
        assert gpt4o["context_window"] == 128000

    def test_cost_extracted(self):
        with self._mock_fetch():
            results = catalog_models(provider="openai")
        gpt4o = next(r for r in results if r["id"] == "gpt-4o")
        assert gpt4o["cost_in"] == 5.0
        assert gpt4o["cost_out"] == 15.0

    def test_capabilities_list_for_tools_vision(self):
        with self._mock_fetch():
            results = catalog_models(provider="openai")
        gpt4o = next(r for r in results if r["id"] == "gpt-4o")
        assert "tools" in gpt4o["capabilities"]
        assert "vision" in gpt4o["capabilities"]

    def test_sorted_by_provider_then_id(self):
        with self._mock_fetch():
            results = catalog_models()
        providers_seq = [r["provider"] for r in results]
        assert providers_seq == sorted(providers_seq) or all(
            providers_seq[i] <= providers_seq[i + 1] for i in range(len(providers_seq) - 1)
        )

    def test_empty_catalog_returns_empty(self):
        with patch("bauer.models_dev.fetch_models_dev", return_value={}):
            results = catalog_models()
        assert results == []

    def test_malformed_model_entry_skipped(self):
        catalog = {
            "openai": {
                "models": {
                    "good-model": {"limit": {"context": 4096}, "pricing": {}},
                    "bad-model": "not-a-dict",
                }
            }
        }
        with patch("bauer.models_dev.fetch_models_dev", return_value=catalog):
            results = catalog_models()
        ids = {r["id"] for r in results}
        assert "good-model" in ids
        assert "bad-model" not in ids

    def test_model_with_no_context_window(self):
        catalog = {"openai": {"models": {"m": {"pricing": {"input": 1.0}}}}}
        with patch("bauer.models_dev.fetch_models_dev", return_value=catalog):
            results = catalog_models()
        assert results[0]["context_window"] is None

    def test_model_with_no_cost(self):
        catalog = {"openai": {"models": {"m": {"limit": {"context": 4096}}}}}
        with patch("bauer.models_dev.fetch_models_dev", return_value=catalog):
            results = catalog_models()
        assert results[0]["cost_in"] is None


# ---------------------------------------------------------------------------
# CLI — bauer models catalog
# ---------------------------------------------------------------------------


class TestBauerModelsCatalogCli:
    def _invoke(self, *args):
        typer = pytest.importorskip("typer")
        from typer.testing import CliRunner
        from bauer.cli import app

        runner = CliRunner(mix_stderr=False)
        return runner.invoke(app, ["models", "catalog", *args], catch_exceptions=False)

    def test_command_exists(self):
        pytest.importorskip("typer")
        from typer.testing import CliRunner
        from bauer.cli import app

        runner = CliRunner(mix_stderr=False)
        result = runner.invoke(app, ["models", "catalog", "--help"])
        assert result.exit_code == 0
        assert "catalog" in result.output.lower() or "provider" in result.output.lower()

    def test_provider_filter_flag(self):
        pytest.importorskip("typer")
        with patch("bauer.models_dev.fetch_models_dev", return_value=_SAMPLE_CATALOG):
            result = self._invoke("--provider", "openai")
        assert result.exit_code == 0
        assert "openai" in result.output.lower() or "gpt" in result.output.lower()

    def test_capability_filter_flag(self):
        pytest.importorskip("typer")
        with patch("bauer.models_dev.fetch_models_dev", return_value=_SAMPLE_CATALOG):
            result = self._invoke("--capability", "vision")
        assert result.exit_code == 0

    def test_no_results_message(self):
        pytest.importorskip("typer")
        with patch("bauer.models_dev.fetch_models_dev", return_value={}):
            result = self._invoke()
        assert result.exit_code == 0
        assert "nenhum" in result.output.lower() or "found" in result.output.lower() or "filtro" in result.output.lower()

    def test_limit_flag(self):
        pytest.importorskip("typer")
        with patch("bauer.models_dev.fetch_models_dev", return_value=_SAMPLE_CATALOG):
            result = self._invoke("--limit", "1")
        assert result.exit_code == 0

    def test_output_contains_context_column(self):
        pytest.importorskip("typer")
        with patch("bauer.models_dev.fetch_models_dev", return_value=_SAMPLE_CATALOG):
            result = self._invoke("--provider", "openai")
        assert result.exit_code == 0
        assert "ctx" in result.output.lower()

    def test_output_contains_cost_column(self):
        pytest.importorskip("typer")
        with patch("bauer.models_dev.fetch_models_dev", return_value=_SAMPLE_CATALOG):
            result = self._invoke("--provider", "openai")
        assert result.exit_code == 0
        assert "$" in result.output or "in/M" in result.output or "cost" in result.output.lower()


# ---------------------------------------------------------------------------
# Telegram /model keyboard metadata
# ---------------------------------------------------------------------------


class TestTelegramModelKeyboardMetadata:
    def _make_bridge(self, tmp_path):
        from bauer.telegram_bridge import TelegramBridge

        cfg = MagicMock()
        cfg.token = "FAKE_TOKEN"
        cfg.allowed_users = []
        cfg.model_allowlist = []
        backend = MagicMock()
        backend._models_for_provider.return_value = ["gpt-4o", "gpt-3.5-turbo"]
        backend._active_model.return_value = ("openai", "gpt-4o")
        bridge = TelegramBridge.__new__(TelegramBridge)
        bridge._cfg = cfg
        bridge._token = "FAKE"
        bridge._model_allowlist = []
        bridge._picker_state = {}
        bridge.backend = backend
        return bridge

    def test_model_keyboard_calls_catalog_models(self, tmp_path):
        bridge = self._make_bridge(tmp_path)
        bridge._picker_state["42"] = {
            "providers": ["openai"],
            "models": {},
        }
        bridge._active_pair = MagicMock(return_value=("openai", "gpt-4o"))

        with patch("bauer.models_dev.fetch_models_dev", return_value=_SAMPLE_CATALOG):
            text, keyboard = bridge._model_keyboard("42", 0, 0)

        # Should produce a keyboard dict with inline_keyboard
        assert "inline_keyboard" in keyboard

    def test_context_window_badge_shown(self, tmp_path):
        bridge = self._make_bridge(tmp_path)
        bridge._picker_state["42"] = {
            "providers": ["openai"],
            "models": {},
        }
        bridge._active_pair = MagicMock(return_value=("", ""))

        with patch("bauer.models_dev.fetch_models_dev", return_value=_SAMPLE_CATALOG):
            text, keyboard = bridge._model_keyboard("42", 0, 0)

        # Flatten all button labels
        labels = [
            btn["text"]
            for row in keyboard["inline_keyboard"]
            for btn in row
            if "mp:m:" in btn.get("callback_data", "")
        ]
        # gpt-4o has 128k context → should show [128k]
        gpt4o_labels = [l for l in labels if "gpt-4o" in l]
        assert gpt4o_labels, "gpt-4o button should exist"
        assert "128k" in gpt4o_labels[0]

    def test_cost_badge_shown(self, tmp_path):
        bridge = self._make_bridge(tmp_path)
        bridge._picker_state["42"] = {
            "providers": ["openai"],
            "models": {},
        }
        bridge._active_pair = MagicMock(return_value=("", ""))

        with patch("bauer.models_dev.fetch_models_dev", return_value=_SAMPLE_CATALOG):
            text, keyboard = bridge._model_keyboard("42", 0, 0)

        labels = [
            btn["text"]
            for row in keyboard["inline_keyboard"]
            for btn in row
            if "mp:m:" in btn.get("callback_data", "")
        ]
        # gpt-4o has $5.00/M input cost
        gpt4o_label = next((l for l in labels if "gpt-4o" in l), "")
        assert "$" in gpt4o_label, f"Expected $ in gpt-4o label, got: {gpt4o_label!r}"

    def test_model_without_metadata_still_shows(self, tmp_path):
        """Models not in models_dev catalog still appear without badges."""
        bridge = self._make_bridge(tmp_path)
        bridge.backend._models_for_provider.return_value = ["unknown-model-xyz"]
        bridge._picker_state["42"] = {
            "providers": ["openai"],
            "models": {},
        }
        bridge._active_pair = MagicMock(return_value=("", ""))

        with patch("bauer.models_dev.fetch_models_dev", return_value=_SAMPLE_CATALOG):
            text, keyboard = bridge._model_keyboard("42", 0, 0)

        labels = [
            btn["text"]
            for row in keyboard["inline_keyboard"]
            for btn in row
            if "mp:m:" in btn.get("callback_data", "")
        ]
        assert any("unknown-model-xyz" in l for l in labels)

    def test_catalog_exception_doesnt_break_keyboard(self, tmp_path):
        """If catalog_models raises, keyboard still renders."""
        bridge = self._make_bridge(tmp_path)
        bridge._picker_state["42"] = {
            "providers": ["openai"],
            "models": {},
        }
        bridge._active_pair = MagicMock(return_value=("", ""))

        with patch("bauer.models_dev.fetch_models_dev", side_effect=RuntimeError("network error")):
            text, keyboard = bridge._model_keyboard("42", 0, 0)

        assert "inline_keyboard" in keyboard


# ---------------------------------------------------------------------------
# catalog_models with per-token costs (normalization)
# ---------------------------------------------------------------------------


class TestCatalogModelsCostNormalization:
    def test_per_token_cost_normalized_for_filter(self):
        """If pricing uses per-token values (< 0.01), cost filter should still work."""
        catalog = {
            "cheap": {
                "models": {
                    "nano": {
                        "limit": {"context": 8192},
                        "pricing": {"input": 0.000001, "output": 0.000002},
                    }
                }
            }
        }
        with patch("bauer.models_dev.fetch_models_dev", return_value=catalog):
            # 0.000001 * 1e6 = 1.0 USD/M — filter at $0.50/M should exclude
            results_low = catalog_models(max_cost_per_m=0.50)
            # filter at $2/M should include
            results_high = catalog_models(max_cost_per_m=2.0)

        assert len(results_low) == 0
        assert len(results_high) == 1
