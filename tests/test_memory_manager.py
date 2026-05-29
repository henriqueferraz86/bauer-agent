"""Testes do MemoryManager (Fase 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.memory_manager import MEMORY_FILES, MemoryManager


# --- init_files -------------------------------------------------------------


def test_init_creates_all_files(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    created = mm.init_files()
    assert len(created) == len(MEMORY_FILES)
    for name in MEMORY_FILES.values():
        assert (mm.memory_dir / name).exists()


def test_init_does_not_overwrite_existing(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    # Escreve algo no arquivo
    decisions = mm.memory_dir / "DECISIONS.md"
    decisions.write_text("conteudo custom", encoding="utf-8")
    # Segunda chamada nao deve sobrescrever
    mm.init_files()
    assert decisions.read_text(encoding="utf-8") == "conteudo custom"


def test_init_creates_directory(tmp_path: Path):
    mm = MemoryManager(tmp_path / "sub" / "memory")
    mm.init_files()
    assert mm.memory_dir.is_dir()


def test_init_files_have_headers(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    content = (mm.memory_dir / "DECISIONS.md").read_text(encoding="utf-8")
    assert "DECISIONS" in content
    assert "---" in content


# --- append_entry -----------------------------------------------------------


def test_append_entry_creates_entry(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    mm.append_entry("DECISIONS.md", "Minha decisao", {"campo": "valor"}, "corpo aqui")
    content = (mm.memory_dir / "DECISIONS.md").read_text(encoding="utf-8")
    assert "Minha decisao" in content
    assert "campo: valor" in content
    assert "corpo aqui" in content


def test_append_entry_has_timestamp(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    mm.append_entry("MEMORY.md", "Teste timestamp")
    content = (mm.memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    # Timestamp no formato ## [YYYY-MM-DD HH:MM UTC]
    assert "## [20" in content
    assert "UTC]" in content


def test_append_entry_preserves_existing(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    mm.append_entry("MEMORY.md", "primeira")
    mm.append_entry("MEMORY.md", "segunda")
    content = (mm.memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "primeira" in content
    assert "segunda" in content


def test_append_entry_without_fields(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    mm.append_entry("MEMORY.md", "sem campos", body="apenas corpo")
    content = (mm.memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "sem campos" in content
    assert "apenas corpo" in content


# --- add_decision -----------------------------------------------------------


def test_add_decision_writes_to_decisions(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    p = mm.add_decision("Usar qwen2.5-coder:3b", "Modelo conservador por padrao")
    assert p.name == "DECISIONS.md"
    content = p.read_text(encoding="utf-8")
    assert "Usar qwen2.5-coder:3b" in content
    assert "Modelo conservador por padrao" in content


def test_add_decision_with_context(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    mm.add_decision("Titulo", "Corpo", context="contexto aqui")
    content = (mm.memory_dir / "DECISIONS.md").read_text(encoding="utf-8")
    assert "context: contexto aqui" in content


# --- add_failure ------------------------------------------------------------


def test_add_failure_writes_error_and_fix(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    p = mm.add_failure("OOM com 7B", "Out of memory com 64K", fix="Reduziu para 16K")
    assert p.name == "FAILED_ATTEMPTS.md"
    content = p.read_text(encoding="utf-8")
    assert "OOM com 7B" in content
    assert "error: Out of memory com 64K" in content
    assert "fix: Reduziu para 16K" in content


def test_add_failure_without_fix(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    mm.add_failure("Problema", "Erro aconteceu")
    content = (mm.memory_dir / "FAILED_ATTEMPTS.md").read_text(encoding="utf-8")
    assert "error: Erro aconteceu" in content
    assert "fix:" not in content.split("error:")[-1].split("##")[0]


# --- add_model_experience ---------------------------------------------------


def test_add_model_experience_includes_machine_id(tmp_path: Path):
    """Decisao 5: machine_id obrigatorio em MODEL_EXPERIENCE.md."""
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    p = mm.add_model_experience(
        model="qwen2.5-coder:3b",
        context_tokens=16384,
        result="ok",
        ram_used_mb=7000,
        machine_id="abc123def456",
        lesson="Funciona bem",
    )
    assert p.name == "MODEL_EXPERIENCE.md"
    content = p.read_text(encoding="utf-8")
    assert "machine_id: abc123def456" in content
    assert "context_tokens: 16384" in content
    assert "result: ok" in content
    assert "ram_used_mb: 7000" in content
    assert "lesson: Funciona bem" in content


def test_add_model_experience_title_format(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    mm.add_model_experience("llama3.1:8b", 32768, "slow", 8000, "aabbcc112233")
    content = (mm.memory_dir / "MODEL_EXPERIENCE.md").read_text(encoding="utf-8")
    assert "llama3.1:8b" in content
    assert "32768" in content


# --- add_runtime_lesson -----------------------------------------------------


def test_add_runtime_lesson(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    p = mm.add_runtime_lesson(
        "Contexto reduzido de 64K para 16K",
        "RAM disponivel insuficiente para 64K",
        how_to_undo="Aumentar RAM ou editar config.yaml",
    )
    assert p.name == "RUNTIME_LESSONS.md"
    content = p.read_text(encoding="utf-8")
    assert "Contexto reduzido" in content
    assert "how_to_undo: Aumentar RAM" in content


# --- read_file / list_files -------------------------------------------------


def test_read_file_by_alias(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    mm.add_decision("Test", "Corpo")
    # Aceita alias 'DECISIONS' sem .md
    content = mm.read_file("DECISIONS")
    assert "Test" in content


def test_read_file_missing_returns_message(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    content = mm.read_file("DECISIONS.md")
    assert "nao encontrado" in content


def test_list_files_counts_entries(tmp_path: Path):
    mm = MemoryManager(tmp_path / "memory")
    mm.init_files()
    mm.add_decision("d1", "corpo")
    mm.add_decision("d2", "corpo")
    files = {name: (lines, entries) for name, lines, entries in mm.list_files()}
    assert files["DECISIONS.md"][1] == 2  # 2 entradas
    assert files["MEMORY.md"][1] == 0     # nenhuma entrada ainda


def test_list_files_missing_dir(tmp_path: Path):
    mm = MemoryManager(tmp_path / "nao_existe")
    result = mm.list_files()
    # Nao deve crashar — todos os arquivos tem 0 linhas e 0 entradas
    assert all(lines == 0 and entries == 0 for _, lines, entries in result)
