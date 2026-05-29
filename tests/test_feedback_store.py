"""Testes do FeedbackStore (Fase 7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.feedback_store import FeedbackStore


@pytest.fixture
def store(tmp_path: Path) -> FeedbackStore:
    mem = tmp_path / "memory"
    mem.mkdir()
    return FeedbackStore(memory_dir=mem)


def test_record_model_failure_creates_entry(store: FeedbackStore, tmp_path: Path):
    store.record_model_failure("qwen2.5:3b", 8192, "OOM error", machine_id="abc123")
    content = (tmp_path / "memory" / "FAILED_ATTEMPTS.md").read_text(encoding="utf-8")
    assert "qwen2.5:3b" in content
    assert "OOM error" in content
    assert "abc123" in content


def test_record_model_failure_without_machine_id(store: FeedbackStore, tmp_path: Path):
    store.record_model_failure("llama3:8b", 4096, "timeout")
    content = (tmp_path / "memory" / "FAILED_ATTEMPTS.md").read_text(encoding="utf-8")
    assert "llama3:8b" in content
    assert "timeout" in content
    assert "4096" in content


def test_record_model_success_creates_entry(store: FeedbackStore, tmp_path: Path):
    store.record_model_success("qwen2.5:3b", 16384, 5980, "ff97819")
    content = (tmp_path / "memory" / "MODEL_EXPERIENCE.md").read_text(encoding="utf-8")
    assert "qwen2.5:3b" in content
    assert "16384" in content
    assert "ok" in content
    assert "ff97819" in content


def test_record_model_success_with_lesson(store: FeedbackStore, tmp_path: Path):
    store.record_model_success("llama3:8b", 8192, 8000, "deadbeef", lesson="Funciona bem")
    content = (tmp_path / "memory" / "MODEL_EXPERIENCE.md").read_text(encoding="utf-8")
    assert "Funciona bem" in content


def test_record_preference_creates_entry(store: FeedbackStore, tmp_path: Path):
    store.record_preference("profile", "fast")
    content = (tmp_path / "memory" / "USER_PREFERENCES.md").read_text(encoding="utf-8")
    assert "profile" in content
    assert "fast" in content


def test_record_multiple_failures(store: FeedbackStore, tmp_path: Path):
    store.record_model_failure("m1", 4096, "erro 1")
    store.record_model_failure("m2", 8192, "erro 2")
    content = (tmp_path / "memory" / "FAILED_ATTEMPTS.md").read_text(encoding="utf-8")
    assert "erro 1" in content
    assert "erro 2" in content
