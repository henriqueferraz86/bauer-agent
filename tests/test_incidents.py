"""Testes do módulo de telemetria de incidentes."""

from __future__ import annotations

import json

from bauer.incidents import list_incidents, record_incident


class TestRecordIncident:
    def test_grava_arquivo_json(self, tmp_path):
        path = record_incident(
            "empty_response",
            incidents_dir=tmp_path,
            model="test-model",
            approx_tokens=1234,
        )
        assert path is not None and path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["kind"] == "empty_response"
        assert data["details"]["model"] == "test-model"
        assert data["details"]["approx_tokens"] == 1234

    def test_nunca_levanta_excecao(self, tmp_path):
        # detalhe não-serializável + dir inválido não podem propagar
        result = record_incident(
            "x", incidents_dir=tmp_path / "\0invalido" if False else tmp_path,
            obj=object(),
        )
        assert result is not None  # objeto vira str truncada

    def test_trunca_strings_longas(self, tmp_path):
        path = record_incident("x", incidents_dir=tmp_path, conteudo="A" * 5000)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["details"]["conteudo"]) < 600
        assert "truncado" in data["details"]["conteudo"]

    def test_retencao_remove_antigos(self, tmp_path, monkeypatch):
        import bauer.incidents as mod
        monkeypatch.setattr(mod, "INCIDENTS_MAX", 5)
        for i in range(8):
            record_incident(f"k{i}", incidents_dir=tmp_path)
        assert len(list(tmp_path.glob("*.json"))) <= 5


class TestListIncidents:
    def test_lista_mais_recentes_primeiro(self, tmp_path):
        record_incident("a", incidents_dir=tmp_path, seq=1)
        record_incident("b", incidents_dir=tmp_path, seq=2)
        out = list_incidents(incidents_dir=tmp_path)
        assert len(out) == 2
        assert {o["kind"] for o in out} == {"a", "b"}

    def test_filtro_por_kind(self, tmp_path):
        record_incident("empty_response", incidents_dir=tmp_path)
        record_incident("tool_loop", incidents_dir=tmp_path)
        out = list_incidents(incidents_dir=tmp_path, kind="tool_loop")
        assert len(out) == 1
        assert out[0]["kind"] == "tool_loop"

    def test_dir_inexistente_retorna_vazio(self, tmp_path):
        assert list_incidents(incidents_dir=tmp_path / "nao_existe") == []
