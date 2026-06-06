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


# --- record_turn_usage (Wave 1) ---------------------------------------------


def test_record_turn_usage_accumulates_tokens():
    """Each call adds to usage_total per canonical key."""
    s = SessionStats(model="gpt-4o-mini", context_tokens=128_000, provider="openai")
    s.record_turn_usage({"prompt_tokens": 100, "completion_tokens": 30})
    s.record_turn_usage({"prompt_tokens": 200, "completion_tokens": 50})
    assert s.usage_total["prompt_tokens"] == 300
    assert s.usage_total["completion_tokens"] == 80
    assert s.usage_total["total_tokens"] == 380


def test_record_turn_usage_adds_cost_for_known_provider():
    """1k prompt + 1k completion on gpt-4o-mini = $0.00015 + $0.0006 = ~$0.00075"""
    s = SessionStats(model="gpt-4o-mini", context_tokens=128_000, provider="openai")
    s.record_turn_usage({"prompt_tokens": 1000, "completion_tokens": 1000})
    assert 0.0001 < s.cost_usd_total < 0.001


def test_record_turn_usage_returns_normalised_dict():
    """The return value lets callers display per-turn numbers immediately."""
    s = SessionStats(model="claude-3-5-sonnet-latest", context_tokens=200_000,
                     provider="anthropic")
    turn = s.record_turn_usage({"input_tokens": 500, "output_tokens": 200})
    # Anthropic shape gets normalised to canonical prompt/completion keys.
    assert turn["prompt_tokens"] == 500
    assert turn["completion_tokens"] == 200


def test_record_turn_usage_handles_empty():
    """Provider that didn't return usage → no-op, no error."""
    s = SessionStats(model="m", context_tokens=4096)
    out = s.record_turn_usage({})
    assert s.usage_total["total_tokens"] == 0
    assert s.cost_usd_total == 0.0
    assert out["total_tokens"] == 0


def test_record_turn_usage_no_cost_without_provider():
    """Costing is skipped if provider is empty string (e.g. Ollama local)."""
    s = SessionStats(model="qwen2.5-coder:3b", context_tokens=16384, provider="")
    s.record_turn_usage({"prompt_tokens": 1000, "completion_tokens": 500})
    assert s.cost_usd_total == 0.0  # explicit zero, no fallback pricing
    # Tokens still tracked even without cost.
    assert s.usage_total["prompt_tokens"] == 1000


def test_record_turn_usage_anthropic_cache_pricing():
    """Cache read tokens at 10% of input price reduce session cost."""
    s = SessionStats(model="claude-3-5-sonnet-latest", context_tokens=200_000,
                     provider="anthropic")
    # 100k prompt, all from cache → very cheap
    s.record_turn_usage({
        "input_tokens": 100_000,
        "output_tokens": 0,
        "cache_read_input_tokens": 100_000,
    })
    # 100k × $3 × 0.10 / 1M = $0.030
    expected = 100_000 * 3.0 * 0.10 / 1_000_000
    assert abs(s.cost_usd_total - expected) < 1e-6
