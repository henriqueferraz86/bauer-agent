"""Testes do gradiente de qualidade da memória de decisões.

Antes, TODA decisão era gravada como neutral/0.5 (o gravador por-turno em
memory_context nunca passava score, e update_outcome nunca era chamado). Estes
testes fixam: (1) o score heurístico por-turno, (2) o feedback humano 👍/👎 via
update_latest_outcome.
"""

from __future__ import annotations

import time

import pytest

from bauer.decision_memory import DecisionMemory
from bauer.memory_context import (
    _heuristic_quality,
    _tool_entry_failed,
    sync_memory_after_turn,
)


class _SyncThread:
    """Fake Thread que roda o target na hora — torna o sync determinístico
    (o real usa daemon thread fire-and-forget, inerentemente racy no teste)."""

    def __init__(self, target=None, daemon=None, **kw):
        self._target = target

    def start(self):
        if self._target:
            self._target()


@pytest.fixture
def _sync_now(monkeypatch):
    monkeypatch.setattr("bauer.memory_context.threading.Thread", _SyncThread)


# ─── heurística de qualidade ─────────────────────────────────────────────────

class TestHeuristicQuality:
    def test_tool_falhou_marca_bad(self):
        log = [{"tool": "run_command", "result": "[Erro: comando nao encontrado]"}]
        assert _heuristic_quality("qualquer resposta", log) == ("bad", 0.30)

    def test_blocked_marca_bad(self):
        log = [{"tool": "run_command", "result": "[BLOCKED] DANGEROUS: rm -rf"}]
        assert _heuristic_quality("resp", log) == ("bad", 0.30)

    def test_app_factory_gate_marca_bad(self):
        log = [{"tool": "write_file", "result": "[App Factory] escrita bloqueada"}]
        assert _heuristic_quality("resp", log) == ("bad", 0.30)

    def test_resposta_substantiva_com_tools_ok_marca_good(self):
        log = [{"tool": "read_file", "result": "conteudo do arquivo lido com sucesso"}]
        outcome, score = _heuristic_quality("R" * 250, log)
        assert outcome == "good" and score == 0.65

    def test_resposta_curta_fica_neutral(self):
        log = [{"tool": "read_file", "result": "ok"}]
        assert _heuristic_quality("curta", log) == ("neutral", 0.50)

    def test_sem_tools_fica_neutral(self):
        assert _heuristic_quality("R" * 300, []) == ("neutral", 0.50)

    def test_falha_domina_mesmo_com_sucesso(self):
        log = [
            {"tool": "read_file", "result": "ok grande " * 30},
            {"tool": "run_command", "result": "[Erro: falhou]"},
        ]
        assert _heuristic_quality("R" * 300, log)[0] == "bad"

    def test_entry_failed_flag_explicita(self):
        assert _tool_entry_failed({"tool": "x", "failed": True, "result": "ok"}) is True
        assert _tool_entry_failed({"tool": "x", "result": "ok"}) is False


# ─── sync_memory_after_turn grava score não-trivial ──────────────────────────

class TestSyncGravaScore:
    def test_turno_com_falha_grava_score_baixo(self, tmp_path, _sync_now):
        sync_memory_after_turn(
            "faça algo", "R" * 100,
            [{"tool": "run_command", "result": "[Erro: x]"}],
            workspace=tmp_path, session_id="s1",
        )
        recs = DecisionMemory(db_path=tmp_path / "decisions.db")._load_all()
        assert recs, "decisão deveria ter sido gravada"
        assert recs[0].outcome == "bad" and recs[0].score == pytest.approx(0.30)

    def test_turno_bom_grava_score_alto(self, tmp_path, _sync_now):
        sync_memory_after_turn(
            "explique X", "R" * 300,
            [{"tool": "read_file", "result": "conteudo util lido"}],
            workspace=tmp_path, session_id="s2",
        )
        recs = DecisionMemory(db_path=tmp_path / "decisions.db")._load_all()
        assert recs
        assert recs[0].outcome == "good" and recs[0].score == pytest.approx(0.65)

    def test_nao_grava_mais_tudo_como_0_5(self, tmp_path, _sync_now):
        # Regressão do bug original: scores variam, não ficam presos em 0.5.
        sync_memory_after_turn("a", "R" * 300, [{"tool": "read_file", "result": "ok"}],
                               workspace=tmp_path, session_id="s3")
        sync_memory_after_turn("b", "c", [{"tool": "run_command", "result": "[Erro: y]"}],
                               workspace=tmp_path, session_id="s3")
        recs = DecisionMemory(db_path=tmp_path / "decisions.db")._load_all()
        scores = {round(r.score, 2) for r in recs}
        assert scores != {0.5}, "scores não deveriam estar todos presos em 0.5"


# ─── update_latest_outcome (feedback 👍/👎) ──────────────────────────────────

class TestUpdateLatestOutcome:
    def test_atualiza_decisao_mais_recente_da_sessao(self, tmp_path):
        dm = DecisionMemory(db_path=tmp_path / "decisions.db")
        dm.record("ctx1", "primeira", score=0.5, session_id="sess")
        time.sleep(0.01)
        dm.record("ctx2", "ultima", score=0.5, session_id="sess")
        ok = dm.update_latest_outcome("sess", "good", score=0.9)
        assert ok is True
        recs = {r.decision: r for r in dm._load_all()}
        assert recs["ultima"].score == pytest.approx(0.9)
        assert recs["ultima"].outcome == "good"
        assert recs["primeira"].score == pytest.approx(0.5)  # intacta

    def test_thumbsdown_baixa_score(self, tmp_path):
        dm = DecisionMemory(db_path=tmp_path / "decisions.db")
        dm.record("ctx", "resp", score=0.65, session_id="s")
        dm.update_latest_outcome("s", "bad", score=0.1)
        assert dm._load_all()[0].score == pytest.approx(0.1)

    def test_sem_session_id_nao_faz_nada(self, tmp_path):
        dm = DecisionMemory(db_path=tmp_path / "decisions.db")
        dm.record("ctx", "resp", score=0.5, session_id="s")
        assert dm.update_latest_outcome("", "good", score=0.9) is False

    def test_sessao_inexistente_retorna_false(self, tmp_path):
        dm = DecisionMemory(db_path=tmp_path / "decisions.db")
        dm.record("ctx", "resp", score=0.5, session_id="s")
        assert dm.update_latest_outcome("outra", "good", score=0.9) is False

    def test_so_a_sessao_certa_e_afetada(self, tmp_path):
        dm = DecisionMemory(db_path=tmp_path / "decisions.db")
        dm.record("c", "de-a", score=0.5, session_id="A")
        dm.record("c", "de-b", score=0.5, session_id="B")
        dm.update_latest_outcome("A", "good", score=0.9)
        recs = {r.decision: r for r in dm._load_all()}
        assert recs["de-a"].score == pytest.approx(0.9)
        assert recs["de-b"].score == pytest.approx(0.5)
