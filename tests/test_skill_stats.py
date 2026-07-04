"""Testes da telemetria de uso de skills (Nível 1 — só coleta, não age)."""

from __future__ import annotations

import json

from bauer import skill_stats as ss


class TestRecordUse:
    def test_grava_uso_e_desfecho(self, tmp_path):
        p = tmp_path / "s.json"
        ss.record_use("Sec", "good", path=p)
        d = ss.load_stats(path=p)
        assert d["Sec"]["uses"] == 1
        assert d["Sec"]["good"] == 1
        assert d["Sec"]["last_used"] > 0

    def test_acumula_multiplos(self, tmp_path):
        p = tmp_path / "s.json"
        for o in ("good", "good", "bad", "neutral"):
            ss.record_use("X", o, path=p)
        r = ss.load_stats(path=p)["X"]
        assert r["uses"] == 4 and r["good"] == 2 and r["bad"] == 1 and r["neutral"] == 1

    def test_outcome_invalido_vira_neutral(self, tmp_path):
        p = tmp_path / "s.json"
        ss.record_use("X", "explodiu", path=p)
        assert ss.load_stats(path=p)["X"]["neutral"] == 1

    def test_nome_vazio_ignorado(self, tmp_path):
        p = tmp_path / "s.json"
        ss.record_use("", "good", path=p)
        ss.record_use("   ", "good", path=p)
        assert ss.load_stats(path=p) == {}

    def test_skills_separadas(self, tmp_path):
        p = tmp_path / "s.json"
        ss.record_use("A", "good", path=p)
        ss.record_use("B", "bad", path=p)
        d = ss.load_stats(path=p)
        assert d["A"]["good"] == 1 and d["B"]["bad"] == 1


class TestRecordFeedback:
    def test_thumbs_up_e_down(self, tmp_path):
        p = tmp_path / "s.json"
        ss.record_feedback("X", True, path=p)
        ss.record_feedback("X", True, path=p)
        ss.record_feedback("X", False, path=p)
        r = ss.load_stats(path=p)["X"]
        assert r["thumbs_up"] == 2 and r["thumbs_down"] == 1

    def test_feedback_em_skill_sem_uso_previo(self, tmp_path):
        # feedback pode chegar antes de um record_use nesse arquivo — cria blank
        p = tmp_path / "s.json"
        ss.record_feedback("Nova", True, path=p)
        assert ss.load_stats(path=p)["Nova"]["thumbs_up"] == 1
        assert ss.load_stats(path=p)["Nova"]["uses"] == 0


class TestRobustez:
    def test_load_arquivo_inexistente_vazio(self, tmp_path):
        assert ss.load_stats(path=tmp_path / "nao_existe.json") == {}

    def test_load_json_corrompido_vazio(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text("{ nao eh json valido", encoding="utf-8")
        assert ss.load_stats(path=p) == {}

    def test_record_sobrevive_a_corrompido(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text("lixo", encoding="utf-8")
        ss.record_use("X", "good", path=p)  # não deve levantar
        assert ss.load_stats(path=p)["X"]["uses"] == 1

    def test_persistencia_em_disco(self, tmp_path):
        p = tmp_path / "s.json"
        ss.record_use("X", "good", path=p)
        # lê o JSON cru p/ garantir que gravou de verdade
        raw = json.loads(p.read_text(encoding="utf-8"))
        assert raw["X"]["uses"] == 1
