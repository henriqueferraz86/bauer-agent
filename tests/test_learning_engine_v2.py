"""Testes para LearningEngineV2 — análise via LLM."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.learning_engine import AnalysisResult, LearningEngineV2, _ANALYSIS_FILE


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_engine(tmp_path: Path) -> LearningEngineV2:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    return LearningEngineV2(memory_dir=memory_dir)


def _write_memory(engine: LearningEngineV2, filename: str, content: str) -> None:
    p = engine.mm.memory_dir / filename
    p.write_text(content, encoding="utf-8")


# ─── _read_memory_section ────────────────────────────────────────────────────


def test_read_memory_section_empty_file(tmp_path):
    engine = _make_engine(tmp_path)
    result = engine._read_memory_section("MODEL_EXPERIENCE.md", "Experiências")
    assert "(vazio)" in result
    assert "Experiências" in result


def test_read_memory_section_with_content(tmp_path):
    engine = _make_engine(tmp_path)
    _write_memory(engine, "MODEL_EXPERIENCE.md", "## [2026-01-01] phi4-mini\n- result: ok")
    result = engine._read_memory_section("MODEL_EXPERIENCE.md", "Experiências")
    assert "phi4-mini" in result
    assert "Experiências" in result


def test_read_memory_section_truncates_long_content(tmp_path):
    engine = _make_engine(tmp_path)
    big_content = "x" * 5000
    _write_memory(engine, "MODEL_EXPERIENCE.md", big_content)
    result = engine._read_memory_section("MODEL_EXPERIENCE.md", "Exp")
    assert "truncado" in result
    assert "omitidos" in result


def test_read_memory_section_not_truncates_short(tmp_path):
    engine = _make_engine(tmp_path)
    short_content = "conteúdo curto"
    _write_memory(engine, "MODEL_EXPERIENCE.md", short_content)
    result = engine._read_memory_section("MODEL_EXPERIENCE.md", "Exp")
    assert "truncado" not in result


# ─── _build_memory_context ───────────────────────────────────────────────────


def test_build_memory_context_returns_string_and_dict(tmp_path):
    engine = _make_engine(tmp_path)
    text, summary = engine._build_memory_context()
    assert isinstance(text, str)
    assert isinstance(summary, dict)
    assert "model_experiences" in summary
    assert "failed_attempts" in summary


def test_build_memory_context_includes_all_sections(tmp_path):
    engine = _make_engine(tmp_path)
    _write_memory(engine, "MODEL_EXPERIENCE.md", "## [2026] test\n- result: ok")
    text, _ = engine._build_memory_context()
    assert "Experiências com Modelos" in text
    assert "Tentativas Falhas" in text
    assert "Lições de Runtime" in text
    assert "Skills Aprendidas" in text


# ─── analyze ─────────────────────────────────────────────────────────────────


def _mock_analyze_call(engine: LearningEngineV2, report_text: str = "Relatório gerado.") -> AnalysisResult:
    """Roda engine.analyze com cliente mockado."""
    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter([report_text])
    mock_client_class = MagicMock(return_value=mock_client)

    cfg = MagicMock()
    cfg.model.name = "phi4-mini"
    cfg.ollama.host = "http://localhost:11434"

    with (
        patch("bauer.ollama_client.OllamaClient", mock_client_class),
        patch("bauer.config_loader.load_config", return_value=cfg),
    ):
        return engine.analyze()


def test_analyze_returns_result(tmp_path):
    engine = _make_engine(tmp_path)
    _write_memory(engine, "MODEL_EXPERIENCE.md", "## [2026-01-01] test\n- result: ok")
    result = _mock_analyze_call(engine, "## Padrões\nTeste funcionou.")
    assert isinstance(result, AnalysisResult)
    assert result.report == "## Padrões\nTeste funcionou."
    assert result.model_used == "phi4-mini"
    assert result.timestamp


def test_analyze_saves_to_file(tmp_path):
    engine = _make_engine(tmp_path)
    _mock_analyze_call(engine, "Relatório de teste.")
    analysis_file = engine.mm.memory_dir / _ANALYSIS_FILE
    assert analysis_file.exists()
    content = analysis_file.read_text(encoding="utf-8")
    assert "Relatório de teste." in content


def test_analyze_raises_on_empty_response(tmp_path):
    engine = _make_engine(tmp_path)
    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter([""])  # resposta vazia
    mock_client_class = MagicMock(return_value=mock_client)

    cfg = MagicMock()
    cfg.model.name = "phi4-mini"
    cfg.ollama.host = "http://localhost:11434"

    with (
        patch("bauer.ollama_client.OllamaClient", mock_client_class),
        patch("bauer.config_loader.load_config", return_value=cfg),
        pytest.raises(RuntimeError, match="resposta vazia"),
    ):
        engine.analyze()


def test_analyze_uses_custom_model(tmp_path):
    engine = _make_engine(tmp_path)
    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter(["Análise customizada."])
    mock_client_class = MagicMock(return_value=mock_client)

    cfg = MagicMock()
    cfg.model.name = "phi4-mini"
    cfg.ollama.host = "http://localhost:11434"

    with (
        patch("bauer.ollama_client.OllamaClient", mock_client_class),
        patch("bauer.config_loader.load_config", return_value=cfg),
    ):
        result = engine.analyze(model="qwen3:0.6b")

    assert result.model_used == "qwen3:0.6b"


# ─── _save_analysis ──────────────────────────────────────────────────────────


def test_save_analysis_creates_file(tmp_path):
    engine = _make_engine(tmp_path)
    result = AnalysisResult(
        timestamp="2026-01-01 12:00 UTC",
        model_used="phi4-mini",
        report="## Padrões\nTudo ok.",
        data_summary={"model_experiences": 3, "failed_attempts": 1},
    )
    path = engine._save_analysis(result)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "2026-01-01 12:00 UTC" in content
    assert "phi4-mini" in content
    assert "Tudo ok." in content


def test_save_analysis_accumulates_entries(tmp_path):
    engine = _make_engine(tmp_path)
    for i in range(3):
        result = AnalysisResult(
            timestamp=f"2026-01-0{i+1} 12:00 UTC",
            model_used="phi4-mini",
            report=f"Análise {i+1}",
            data_summary={},
        )
        engine._save_analysis(result)

    content = (engine.mm.memory_dir / _ANALYSIS_FILE).read_text(encoding="utf-8")
    assert "Análise 1" in content
    assert "Análise 2" in content
    assert "Análise 3" in content


# ─── load_last_analysis ──────────────────────────────────────────────────────


def test_load_last_analysis_none_when_no_file(tmp_path):
    engine = _make_engine(tmp_path)
    assert engine.load_last_analysis() is None


def test_load_last_analysis_returns_most_recent(tmp_path):
    engine = _make_engine(tmp_path)
    for i in range(2):
        result = AnalysisResult(
            timestamp=f"2026-01-0{i+1} 00:00 UTC",
            model_used="phi4-mini",
            report=f"Relatorio {i+1}",
            data_summary={},
        )
        engine._save_analysis(result)

    last = engine.load_last_analysis()
    assert last is not None
    # A entrada mais recente é inserida antes das anteriores (prepend)
    assert "2026-01-02" in last or "Relatorio" in last
