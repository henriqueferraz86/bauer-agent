"""Testes de segurança para combinações inseguras de configuração.

Garante que:
- O default de serve.host seja 127.0.0.1 (bind local).
- bauer doctor emite aviso quando host externo + api_key vazio.
- Não há aviso quando host externo + api_key preenchido.
- Não há aviso quando host local mesmo sem api_key.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bauer.config_loader import load_config, ServeSection
from bauer.model_registry import load_registry
from bauer.preflight import run_doctor


_BASE_CFG = {
    "agent": {"name": "test", "workspace": "./workspace"},
    "model": {
        "provider": "ollama",
        "name": "qwen:test",
        "requested_context": 4096,
        "minimum_context": 2048,
        "auto_downgrade_context": True,
    },
    "ollama": {"host": "http://127.0.0.1:1", "timeout_seconds": 1},
    "runtime": {"profile": "low", "ram_limit_mb": 4096, "safety_margin_mb": 1024},
    "logging": {"level": "error", "file": None},
}

_BASE_MODELS = """
models:
  "qwen:test":
    provider: ollama
    ram_base_mb: 2000
    ram_per_1k_ctx_mb: 30
    max_context_safe: 16384
    supports_tools: false
    ram_profile: low
"""


def _cfg_with_serve(tmp_path: Path, host: str, api_key: str = "") -> Path:
    cfg = dict(_BASE_CFG)
    cfg["serve"] = {"host": host, "api_key": api_key}
    p = tmp_path / "config.yaml"
    p.write_text(yaml.dump(cfg), encoding="utf-8")
    return p


def _models_path(tmp_path: Path) -> Path:
    p = tmp_path / "models.yaml"
    p.write_text(_BASE_MODELS, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Default de host seguro
# ---------------------------------------------------------------------------

def test_serve_host_default_is_local():
    """Novo ServeSection deve usar 127.0.0.1 por default."""
    s = ServeSection()
    assert s.host == "127.0.0.1", (
        f"Default de serve.host deve ser '127.0.0.1', mas é '{s.host}'. "
        "Instalaçao nova ficaria exposta na rede sem api_key."
    )


# ---------------------------------------------------------------------------
# bauer doctor detecta combinação insegura
# ---------------------------------------------------------------------------

def test_doctor_warns_open_host_no_key(tmp_path: Path):
    """Doctor deve avisar quando host externo e api_key vazio."""
    cfg_path = _cfg_with_serve(tmp_path, host="0.0.0.0", api_key="")
    models_path = _models_path(tmp_path)

    cfg = load_config(cfg_path)
    reg = load_registry(models_path)
    report = run_doctor(cfg, reg, tmp_path / "state.json")

    security_warnings = [f for f in report.findings if "AVISO DE SEGURANÇA" in f]
    assert security_warnings, (
        "Doctor deve emitir aviso de segurança para host=0.0.0.0 com api_key vazio. "
        f"Findings: {report.findings}"
    )
    assert "serve.api_key" in security_warnings[0]


def test_doctor_no_warn_open_host_with_key(tmp_path: Path):
    """Doctor nao deve avisar quando host externo mas api_key preenchido."""
    cfg_path = _cfg_with_serve(tmp_path, host="0.0.0.0", api_key="secret-token-123")
    models_path = _models_path(tmp_path)

    cfg = load_config(cfg_path)
    reg = load_registry(models_path)
    report = run_doctor(cfg, reg, tmp_path / "state.json")

    security_warnings = [f for f in report.findings if "AVISO DE SEGURANÇA" in f]
    assert not security_warnings, (
        f"Doctor nao deve avisar quando api_key está preenchida. Findings: {report.findings}"
    )


def test_doctor_no_warn_local_host_no_key(tmp_path: Path):
    """Doctor nao deve avisar para host local mesmo sem api_key."""
    for local_host in ("127.0.0.1", "localhost"):
        cfg_path = _cfg_with_serve(tmp_path, host=local_host, api_key="")
        models_path = _models_path(tmp_path)

        cfg = load_config(cfg_path)
        reg = load_registry(models_path)
        report = run_doctor(cfg, reg, tmp_path / "state.json")

        security_warnings = [f for f in report.findings if "AVISO DE SEGURANÇA" in f]
        assert not security_warnings, (
            f"Doctor nao deve avisar para host local '{local_host}'. "
            f"Findings: {report.findings}"
        )
