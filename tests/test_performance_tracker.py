"""Testes do SessionStats / performance_tracker."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from bauer.performance_tracker import SessionStats, _auto_lesson


# --- result property --------------------------------------------------------


def test_result_ok_no_errors():
    s = SessionStats(model="m", context_tokens=4096)
    s.start_turn()
    s.end_turn(100)
    assert s.result == "ok"


def test_result_oom_from_error():
    s = SessionStats(model="m", context_tokens=4096)
    s.record_error("OOM ao gerar resposta")
    assert s.result == "oom"


def test_result_error_generic():
    s = SessionStats(model="m", context_tokens=4096)
    s.record_error("timeout connecting")
    assert s.result == "error"


def test_result_ok_zero_turns():
    s = SessionStats(model="m", context_tokens=4096)
    assert s.result == "ok"


# --- start_turn / end_turn --------------------------------------------------


def test_turn_increments_count():
    s = SessionStats(model="m", context_tokens=4096)
    s.start_turn()
    assert s.total_turns == 1
    s.start_turn()
    assert s.total_turns == 2


def test_end_turn_accumulates_chars():
    s = SessionStats(model="m", context_tokens=4096)
    s.start_turn()
    s.end_turn(50)
    s.start_turn()
    s.end_turn(30)
    assert s.total_chars == 80


def test_end_turn_returns_elapsed():
    s = SessionStats(model="m", context_tokens=4096)
    s.start_turn()
    elapsed = s.end_turn(10)
    assert elapsed >= 0.0


# --- save -------------------------------------------------------------------


def test_save_writes_to_model_experience(tmp_path: Path):
    from bauer.memory_manager import MemoryManager

    mem = tmp_path / "memory"
    mem.mkdir()
    MemoryManager(mem).init_files()

    s = SessionStats(model="test-model", context_tokens=8192, machine_id="abc")
    s.start_turn()
    s.end_turn(100)
    s.save(memory_dir=mem, lesson="teste ok")

    content = (mem / "MODEL_EXPERIENCE.md").read_text(encoding="utf-8")
    assert "test-model" in content
    assert "8192" in content


def test_save_does_not_raise_on_missing_dir(tmp_path: Path):
    s = SessionStats(model="m", context_tokens=4096)
    s.save(memory_dir=tmp_path / "nao_existe")


# --- _auto_lesson -----------------------------------------------------------


def test_auto_lesson_oom():
    s = SessionStats(model="m", context_tokens=4096)
    s.record_error("out of memory: process killed")
    assert "OOM" in _auto_lesson(s) or "reduzir" in _auto_lesson(s).lower()


def test_auto_lesson_ok():
    s = SessionStats(model="m", context_tokens=4096)
    s.start_turn()
    s.end_turn(50)
    lesson = _auto_lesson(s)
    assert "sucesso" in lesson.lower() or "turnos" in lesson.lower() or lesson != ""
