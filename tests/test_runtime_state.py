"""Testes do runtime_state.json."""

from __future__ import annotations

import json
from pathlib import Path

from bauer.runtime_state import ContextState, RuntimeState, read_state, write_state


def make_state() -> RuntimeState:
    return RuntimeState(
        configured_model="qwen2.5-coder:3b",
        configured_provider="ollama",
        active_model="qwen2.5-coder:3b",
        model_available=True,
        ollama_alive=True,
        ollama_host="http://localhost:11434",
        context=ContextState(
            requested=16384,
            modelfile_num_ctx=8192,
            env_OLLAMA_CONTEXT_LENGTH=None,
            applied=8192,
            empirical_probe=None,
            reason="limited_by=modelfile_num_ctx",
        ),
        tool_mode="bridge",
        profile="low",
        ram_available_mb=6000,
        ram_total_mb=8000,
        machine_id="abc123def456",
        status="ok_with_adjustments",
        notes=["Contexto reduzido de 16384 para 8192."],
    )


def test_write_and_read_roundtrip(tmp_path: Path):
    state = make_state()
    p = tmp_path / ".runtime_state.json"
    write_state(state, p)
    assert p.exists()

    data = read_state(p)
    assert data is not None
    assert data["configured_model"] == "qwen2.5-coder:3b"
    assert data["context"]["applied"] == 8192
    assert data["context"]["reason"] == "limited_by=modelfile_num_ctx"
    assert data["machine_id"] == "abc123def456"
    assert data["status"] == "ok_with_adjustments"
    # JSON é válido e gerado_at presente
    assert "generated_at" in data


def test_read_missing_returns_none(tmp_path: Path):
    assert read_state(tmp_path / "noexist.json") is None


def test_read_corrupt_returns_none(tmp_path: Path):
    p = tmp_path / ".runtime_state.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert read_state(p) is None


def test_machine_id_present_in_serialization(tmp_path: Path):
    state = make_state()
    p = tmp_path / ".runtime_state.json"
    write_state(state, p)
    raw = json.loads(p.read_text(encoding="utf-8"))
    # Decisão 5: aprendizado portável precisa de machine_id no estado.
    assert "machine_id" in raw
    assert len(raw["machine_id"]) == 12
