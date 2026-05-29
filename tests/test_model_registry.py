"""Testes do model_registry e da fórmula de contexto seguro (Decisão 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.model_registry import (
    ModelInfo,
    ModelRegistryError,
    contexto_seguro,
    load_registry,
)


SAMPLE = """
models:
  "qwen2.5-coder:3b":
    provider: ollama
    ram_base_mb: 2400
    ram_per_1k_ctx_mb: 35
    max_context_safe: 32768
    supports_tools: false
    ram_profile: low

  "qwen2.5-coder:7b":
    provider: ollama
    ram_base_mb: 5200
    ram_per_1k_ctx_mb: 70
    max_context_safe: 32768
    supports_tools: false
    ram_profile: medium
"""


def test_load_registry_ok(tmp_path: Path):
    p = tmp_path / "models.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    reg = load_registry(p)
    assert "qwen2.5-coder:3b" in reg.names()
    assert reg.get("qwen2.5-coder:7b").ram_base_mb == 5200


def test_missing_models_key(tmp_path: Path):
    p = tmp_path / "models.yaml"
    p.write_text("foo: bar", encoding="utf-8")
    with pytest.raises(ModelRegistryError):
        load_registry(p)


def test_contexto_seguro_modelo_nao_cabe():
    info = ModelInfo(
        provider="ollama",
        ram_base_mb=5200,
        ram_per_1k_ctx_mb=70,
        max_context_safe=32768,
    )
    # 4GB de RAM disponível, modelo 7B precisa 5.2GB → não cabe nem vazio
    assert contexto_seguro(info, ram_disponivel_mb=4096, folga_mb=1024) == 0


def test_contexto_seguro_limita_por_ram():
    info = ModelInfo(
        provider="ollama",
        ram_base_mb=2400,
        ram_per_1k_ctx_mb=35,
        max_context_safe=32768,
    )
    # 6GB disponíveis - 2.4GB base - 1GB folga = 2.6GB pra contexto
    # 2660 / 35 = ~76 ; 76 * 1024 = 77824 → cap em max_context_safe=32768
    res = contexto_seguro(info, ram_disponivel_mb=6144, folga_mb=1024)
    assert res == 32768


def test_contexto_seguro_limita_por_max_safe():
    info = ModelInfo(
        provider="ollama",
        ram_base_mb=2400,
        ram_per_1k_ctx_mb=35,
        max_context_safe=16384,
    )
    res = contexto_seguro(info, ram_disponivel_mb=32_000, folga_mb=1024)
    assert res == 16384


def test_contexto_seguro_meio_termo():
    info = ModelInfo(
        provider="ollama",
        ram_base_mb=2400,
        ram_per_1k_ctx_mb=35,
        max_context_safe=32768,
    )
    # 4GB disponíveis - 2.4GB - 1GB = 0.6GB ≈ 614MB / 35 = ~17.5 → ~17920
    # Arredondado pra múltiplo de 256
    res = contexto_seguro(info, ram_disponivel_mb=4096, folga_mb=1024)
    assert 0 < res < 32768
    assert res % 256 == 0
