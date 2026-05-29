"""Testes do preflight com Ollama offline (caminho garantido sem rede real)."""

from __future__ import annotations

from pathlib import Path

from bauer.config_loader import load_config
from bauer.model_registry import load_registry
from bauer.preflight import _resolve_context, run_doctor
from bauer.model_registry import ModelInfo


CFG = """
agent:
  name: Bauer Agent
  workspace: ./workspace

model:
  provider: ollama
  name: qwen2.5-coder:3b
  requested_context: 16384
  minimum_context: 8192
  auto_downgrade_context: true

ollama:
  host: http://127.0.0.1:1   # porta inválida de propósito (offline garantido)
  timeout_seconds: 1

runtime:
  profile: low
  ram_limit_mb: 4096
  safety_margin_mb: 1024

logging:
  level: error
  file: null
"""


MODELS = """
models:
  "qwen2.5-coder:3b":
    provider: ollama
    ram_base_mb: 2400
    ram_per_1k_ctx_mb: 35
    max_context_safe: 32768
    supports_tools: false
    ram_profile: low
"""


def test_doctor_with_ollama_offline(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(CFG, encoding="utf-8")
    models_path = tmp_path / "models.yaml"
    models_path.write_text(MODELS, encoding="utf-8")

    cfg = load_config(cfg_path)
    reg = load_registry(models_path)

    report = run_doctor(cfg, reg, state_file=tmp_path / ".runtime_state.json")
    assert report.state.ollama_alive is False
    assert report.state.status == "blocked"
    assert any("Ollama" in f for f in report.findings)


def test_resolve_context_reduces_to_modelfile():
    info = ModelInfo(
        provider="ollama",
        ram_base_mb=2400,
        ram_per_1k_ctx_mb=35,
        max_context_safe=32768,
    )
    applied, reason, notes = _resolve_context(
        requested=16384,
        minimum=8192,
        auto_downgrade=True,
        modelfile_num_ctx=8192,
        env_num_ctx=None,
        info=info,
        ram_available_mb=8000,
        safety_margin_mb=1024,
    )
    assert applied == 8192
    assert "modelfile_num_ctx" in reason


def test_resolve_context_reduces_to_ram_safe():
    info = ModelInfo(
        provider="ollama",
        ram_base_mb=2400,
        ram_per_1k_ctx_mb=35,
        max_context_safe=32768,
    )
    # RAM muito apertada
    applied, reason, _ = _resolve_context(
        requested=32768,
        minimum=2048,
        auto_downgrade=True,
        modelfile_num_ctx=32768,
        env_num_ctx=None,
        info=info,
        ram_available_mb=4096,
        safety_margin_mb=1024,
    )
    assert applied < 32768
    assert "ram_safe" in reason


def test_resolve_context_zero_when_no_fit():
    info = ModelInfo(
        provider="ollama",
        ram_base_mb=5200,
        ram_per_1k_ctx_mb=70,
        max_context_safe=32768,
    )
    applied, reason, notes = _resolve_context(
        requested=16384,
        minimum=8192,
        auto_downgrade=True,
        modelfile_num_ctx=None,
        env_num_ctx=None,
        info=info,
        ram_available_mb=4096,  # menor que ram_base
        safety_margin_mb=1024,
    )
    assert applied == 0
    assert "ram_safe" in reason
    assert any("não cabe" in n for n in notes)
