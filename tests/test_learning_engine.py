"""Testes do LearningEngine (Fase 7).

Verifica que:
- parsing esta correto
- recomendacoes tem motivo e evidencia
- reset cria backup e limpa entradas (preserva cabecalho)
- config nao e alterada
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bauer.learning_engine import (
    FailedAttempt,
    LearningEngine,
    ModelExp,
    generate_recommendations,
    parse_failed_attempts,
    parse_model_experience,
)
from bauer.memory_manager import MemoryManager


# --- fixtures ---------------------------------------------------------------


@pytest.fixture
def mem(tmp_path: Path) -> Path:
    d = tmp_path / "memory"
    d.mkdir()
    return d


@pytest.fixture
def engine(mem: Path) -> LearningEngine:
    mm = MemoryManager(mem)
    mm.init_files()
    return LearningEngine(mem)


# === parse_model_experience ==================================================


def test_parse_experience_empty():
    assert parse_model_experience("# header\n\n---\n") == []


def test_parse_experience_single():
    text = (
        "# MODEL_EXPERIENCE.md\n\n---\n\n"
        "## [2026-05-27 10:00 UTC] qwen2.5:3b — contexto 8192\n\n"
        "- machine_id: abc123\n"
        "- context_tokens: 8192\n"
        "- result: ok\n"
        "- ram_used_mb: 4096\n"
        "- lesson: Funciona bem\n"
    )
    exps = parse_model_experience(text)
    assert len(exps) == 1
    e = exps[0]
    assert "qwen2.5:3b" in e.title
    assert e.context_tokens == 8192
    assert e.result == "ok"
    assert e.ram_used_mb == 4096
    assert e.machine_id == "abc123"
    assert e.lesson == "Funciona bem"


def test_parse_experience_multiple():
    text = (
        "## [2026-05-27 10:00 UTC] modelA — contexto 4096\n"
        "- context_tokens: 4096\n- result: ok\n- ram_used_mb: 2048\n\n"
        "## [2026-05-27 11:00 UTC] modelB — contexto 8192\n"
        "- context_tokens: 8192\n- result: oom\n- ram_used_mb: 0\n"
    )
    exps = parse_model_experience(text)
    assert len(exps) == 2
    assert exps[0].result == "ok"
    assert exps[1].result == "oom"


def test_parse_experience_invalid_int():
    text = (
        "## [2026-05-27 10:00 UTC] modelA — contexto ?\n"
        "- context_tokens: NAO_NUMERO\n- ram_used_mb: NAO_NUMERO\n"
    )
    exps = parse_model_experience(text)
    assert len(exps) == 1
    assert exps[0].context_tokens == 0
    assert exps[0].ram_used_mb == 0


# === parse_failed_attempts ===================================================


def test_parse_failures_empty():
    assert parse_failed_attempts("# header\n\n---\n") == []


def test_parse_failures_single():
    text = (
        "## [2026-05-27 10:00 UTC] modelA — contexto 4096 falhou\n\n"
        "- error: OOM ao gerar resposta longa\n"
        "- machine_id: xyz\n"
    )
    failures = parse_failed_attempts(text)
    assert len(failures) == 1
    f = failures[0]
    assert "modelA" in f.title
    assert "OOM" in f.error
    assert f.machine_id == "xyz"


def test_parse_failures_multiple():
    text = (
        "## [T1] titulo1\n- error: erro1\n"
        "## [T2] titulo2\n- error: erro2\n- fix: corrigi assim\n"
    )
    failures = parse_failed_attempts(text)
    assert len(failures) == 2
    assert failures[1].fix == "corrigi assim"


# === generate_recommendations ================================================


def test_recs_no_data():
    recs = generate_recommendations([], [])
    assert len(recs) == 1
    assert "Nenhuma recomendacao" in recs[0].action
    assert recs[0].severity == "info"


def test_recs_oom_detected():
    exps = [ModelExp("T", "modelA — contexto 8192", 8192, "oom", 0, "abc")]
    recs = generate_recommendations(exps, [])
    oom_recs = [r for r in recs if r.severity == "warning" and "OOM" in r.action or "oom" in r.reason.lower()]
    assert any("oom" in r.reason.lower() or "OOM" in r.action for r in recs)
    oom_rec = next(r for r in recs if "Reduza" in r.action)
    assert "8192" in oom_rec.action
    assert len(oom_rec.evidence) >= 1


def test_recs_slow_detected():
    exps = [ModelExp("T", "modelA — contexto 4096", 4096, "slow", 0, "")]
    recs = generate_recommendations(exps, [])
    slow_recs = [r for r in recs if r.severity == "suggestion"]
    assert len(slow_recs) >= 1
    assert len(slow_recs[0].evidence) >= 1


def test_recs_successful_model():
    exps = [
        ModelExp("T1", "qwen2.5:3b — contexto 8192", 8192, "ok", 4096, "abc"),
        ModelExp("T2", "qwen2.5:3b — contexto 16384", 16384, "ok", 5980, "abc"),
    ]
    recs = generate_recommendations(exps, [])
    ok_recs = [r for r in recs if r.severity == "info" and "qwen2.5:3b" in r.action]
    assert len(ok_recs) >= 1
    assert "16384" in ok_recs[0].action
    assert len(ok_recs[0].evidence) >= 1


def test_recs_many_failures_warning():
    failures = [
        FailedAttempt(f"T{i}", f"titulo{i}", "erro") for i in range(4)
    ]
    recs = generate_recommendations([], failures)
    warning_recs = [r for r in recs if r.severity == "warning" and "falha" in r.reason.lower()]
    assert len(warning_recs) >= 1
    assert len(warning_recs[0].evidence) >= 1


def test_recs_machine_id_filter():
    exps = [
        ModelExp("T1", "modelA — contexto 8192", 8192, "oom", 0, "machine_A"),
        ModelExp("T2", "modelB — contexto 4096", 4096, "ok", 2048, "machine_B"),
    ]
    recs = generate_recommendations(exps, [], machine_id="machine_B")
    # OOM e de machine_A — nao deve aparecer para machine_B
    assert not any("Reduza" in r.action for r in recs)
    # OK de machine_B deve aparecer
    assert any("modelB" in r.action for r in recs)


def test_recs_all_have_reason():
    exps = [
        ModelExp("T", "m — contexto 8192", 8192, "oom", 0, ""),
        ModelExp("T2", "m — contexto 4096", 4096, "slow", 0, ""),
    ]
    failures = [FailedAttempt("T", "f1", "e"), FailedAttempt("T", "f2", "e"), FailedAttempt("T", "f3", "e")]
    recs = generate_recommendations(exps, failures)
    for rec in recs:
        assert rec.reason, f"Recomendacao sem motivo: {rec.action}"


# === LearningEngine ==========================================================


def test_engine_summary_empty(engine: LearningEngine):
    s = engine.summary()
    assert s["model_experiences"] == 0
    assert s["failed_attempts"] == 0


def test_engine_summary_counts(engine: LearningEngine):
    engine.mm.add_model_experience("m", 4096, "ok", 2048, "abc")
    engine.mm.add_model_experience("m", 8192, "ok", 4096, "abc")
    engine.mm.add_failure("titulo", "erro")
    s = engine.summary()
    assert s["model_experiences"] == 2
    assert s["failed_attempts"] == 1


def test_engine_recommend_no_data(engine: LearningEngine):
    recs = engine.recommend()
    assert len(recs) >= 1
    assert recs[0].severity == "info"


def test_engine_recommend_with_oom(engine: LearningEngine):
    engine.mm.add_model_experience("m", 8192, "oom", 0, "abc")
    recs = engine.recommend()
    assert any("Reduza" in r.action for r in recs)


# === reset ===================================================================


def test_reset_creates_backup(engine: LearningEngine, mem: Path):
    engine.mm.add_failure("titulo", "erro")
    engine.reset()
    assert (mem / "FAILED_ATTEMPTS.md.bak").exists()


def test_reset_backup_contains_entries(engine: LearningEngine, mem: Path):
    engine.mm.add_failure("titulo_teste", "meu erro")
    engine.reset()
    bak = (mem / "FAILED_ATTEMPTS.md.bak").read_text(encoding="utf-8")
    assert "titulo_teste" in bak
    assert "meu erro" in bak


def test_reset_clears_entries_from_file(engine: LearningEngine, mem: Path):
    engine.mm.add_failure("titulo_teste", "meu erro")
    engine.reset()
    content = (mem / "FAILED_ATTEMPTS.md").read_text(encoding="utf-8")
    assert "titulo_teste" not in content
    assert "meu erro" not in content


def test_reset_preserves_header(engine: LearningEngine, mem: Path):
    engine.mm.add_failure("titulo", "erro")
    engine.reset()
    content = (mem / "FAILED_ATTEMPTS.md").read_text(encoding="utf-8")
    assert "FAILED_ATTEMPTS" in content
    assert "---" in content


def test_reset_returns_affected_paths(engine: LearningEngine, mem: Path):
    engine.mm.add_failure("titulo", "erro")
    paths = engine.reset()
    names = [p.name for p in paths]
    assert "FAILED_ATTEMPTS.md" in names


def test_reset_no_files(tmp_path: Path):
    empty_dir = tmp_path / "empty_memory"
    empty_dir.mkdir()
    engine = LearningEngine(empty_dir)
    paths = engine.reset()
    assert paths == []


def test_reset_clears_model_experience(engine: LearningEngine, mem: Path):
    engine.mm.add_model_experience("m", 4096, "ok", 2048, "abc")
    engine.reset()
    content = (mem / "MODEL_EXPERIENCE.md").read_text(encoding="utf-8")
    # Entry removida, mas cabecalho preservado
    assert "## [" not in content
    assert "MODEL_EXPERIENCE" in content


def test_reset_and_summary_zero(engine: LearningEngine):
    engine.mm.add_failure("f", "e")
    engine.mm.add_model_experience("m", 4096, "ok", 2048, "abc")
    engine.reset()
    s = engine.summary()
    assert s["model_experiences"] == 0
    assert s["failed_attempts"] == 0
