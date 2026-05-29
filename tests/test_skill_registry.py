"""Testes do SkillRegistry — detecção de skills por frequência."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.skill_registry import SkillRegistry


@pytest.fixture
def mem(tmp_path: Path) -> Path:
    d = tmp_path / "memory"
    d.mkdir()
    return d


@pytest.fixture
def registry(mem: Path) -> SkillRegistry:
    return SkillRegistry(memory_dir=mem)


# --- observe ----------------------------------------------------------------


def test_observe_returns_none_below_threshold(registry: SkillRegistry):
    result = registry.observe("run doctor check")
    assert result is None  # 1 < threshold (3)


def test_observe_returns_skill_at_threshold(registry: SkillRegistry):
    for _ in range(2):
        registry.observe("run doctor check")
    result = registry.observe("run doctor check")
    assert result == "diagnose_ollama"


def test_observe_returns_none_for_unknown_pattern(registry: SkillRegistry):
    result = registry.observe("isso nao tem padrao reconhecido aqui xyzxyz")
    assert result is None


def test_observe_increments_per_pattern(registry: SkillRegistry):
    registry.observe("run ls please")
    registry.observe("run ls please")
    result = registry.observe("run ls please")
    assert result == "list_files"


def test_observe_does_not_suggest_twice(registry: SkillRegistry, mem: Path):
    from bauer.memory_manager import MemoryManager
    MemoryManager(mem).init_files()

    for _ in range(3):
        registry.observe("run doctor check")

    # Segunda instância — já foi sugerido, não deve retornar de novo
    reg2 = SkillRegistry(memory_dir=mem)
    for _ in range(3):
        result = reg2.observe("run doctor check")
    # depois da sugestao, observações adicionais nao retornam o mesmo skill
    assert result is None


# --- _load_count ------------------------------------------------------------


def test_load_count_empty(registry: SkillRegistry):
    count = registry._load_count("diagnose_ollama")
    assert count == 0


def test_load_count_after_record(registry: SkillRegistry, mem: Path):
    from bauer.memory_manager import MemoryManager
    MemoryManager(mem).init_files()

    for _ in range(3):
        registry.observe("run doctor check")

    count = registry._load_count("diagnose_ollama")
    assert count >= 1


# --- _already_suggested ----------------------------------------------------


def test_already_suggested_false_initially(registry: SkillRegistry, mem: Path):
    from bauer.memory_manager import MemoryManager
    MemoryManager(mem).init_files()
    assert not registry._already_suggested("diagnose_ollama")


def test_already_suggested_true_after_record(registry: SkillRegistry, mem: Path):
    from bauer.memory_manager import MemoryManager
    MemoryManager(mem).init_files()

    for _ in range(3):
        registry.observe("run doctor check")

    assert registry._already_suggested("diagnose_ollama")
