"""Testes do onboarding — detecção de estado + welcome + tour."""
from __future__ import annotations

from pathlib import Path

from rich.console import Console

from bauer import onboarding as ob


# ── detect_state ─────────────────────────────────────────────────────────────

def test_detect_state_fresh(tmp_path):
    info = ob.detect_state(tmp_path / "nao-existe.yaml")
    assert info["state"] == "fresh"


def test_detect_state_ready_ollama(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model:\n  provider: ollama\n  name: qwen2.5-coder:3b\n", encoding="utf-8")
    info = ob.detect_state(cfg)
    assert info["state"] == "ready"
    assert info["provider"] == "ollama"


def test_detect_state_ready_opencode(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model:\n  provider: opencode\n  name: x\n", encoding="utf-8")
    assert ob.detect_state(cfg)["state"] == "ready"


def test_detect_state_almost_missing_key(tmp_path, monkeypatch):
    # chdir p/ tmp para que load_config não carregue o .env do repo (que tem chaves).
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model:\n  provider: groq\n  name: llama-3.3-70b-versatile\n", encoding="utf-8")
    info = ob.detect_state(cfg)
    assert info["state"] == "almost"
    assert "GROQ_API_KEY" in info["hint"]


def test_detect_state_ready_key_in_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-xxx")
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model:\n  provider: groq\n  name: x\n", encoding="utf-8")
    assert ob.detect_state(cfg)["state"] == "ready"


def test_detect_state_ready_key_in_dotenv(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model:\n  provider: anthropic\n  name: x\n", encoding="utf-8")
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-xxx\n", encoding="utf-8")
    assert ob.detect_state(cfg)["state"] == "ready"


def test_detect_state_invalid_config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("not: valid: yaml: {", encoding="utf-8")
    info = ob.detect_state(cfg)
    assert info["state"] in ("almost", "fresh")


# ── welcome_screen (não deve crashar em nenhum estado) ───────────────────────

def test_welcome_screen_fresh(tmp_path):
    ob.welcome_screen(Console(), config_path=tmp_path / "x.yaml")


def test_welcome_screen_ready(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model:\n  provider: ollama\n  name: q\n", encoding="utf-8")
    ob.welcome_screen(Console(), config_path=cfg)


def test_welcome_screen_almost(tmp_path, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("model:\n  provider: xai\n  name: grok\n", encoding="utf-8")
    ob.welcome_screen(Console(), config_path=cfg)


# ── guide_tour ───────────────────────────────────────────────────────────────

def test_guide_tour_non_interactive():
    ob.guide_tour(Console(), interactive=False)


def test_tour_has_content():
    assert len(ob._TOUR) >= 4
    assert all(len(item) == 2 for item in ob._TOUR)
