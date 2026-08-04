"""`kanban_complete` grava em PROGRESS.md — em CÓDIGO, não por pedido ao modelo.

Antes disto, manter o PROGRESS.md atualizado era uma instrução em texto no
prompt da task do checkpoint ("Atualize PROGRESS.md ao concluir cada item" —
agent.py). O modelo podia esquecer, e nada detectava — PROGRESS.md e o kanban
divergiam em silêncio. Junto com `_ensure_kanban_seeded`
(test_planning_checkpoint.py), isto fecha o pedido de "deve ser interligado":
o kanban vira a fonte única e o PROGRESS.md um LOG gerado a partir dela.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer import app_factory as af


def _cfg_com_backend(valor: str):
    return type("Cfg", (), {"agent": type("Agent", (), {"task_backend": valor})()})()


@pytest.fixture
def router(tmp_path):
    from bauer.tool_router import ToolRouter
    return ToolRouter(workspace=tmp_path)


@pytest.fixture
def projeto_ativo(tmp_path):
    """Projeto App Factory ativo, com PROGRESS.md existente (como o scaffold
    da App Factory cria)."""
    proj = tmp_path / "app"
    af.init_project(proj, idea="x")
    af.set_active_project(tmp_path, proj)
    return proj


class TestNaoGravaForaDoContexto:
    def test_markdown_backend_nao_grava(self, router, projeto_ativo):
        """Sem sqlite, PROGRESS.md segue por conta do modelo — sem mudança."""
        progress = projeto_ativo / "docs" / "PROGRESS.md"
        antes = progress.read_text(encoding="utf-8")
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("markdown")):
            router._append_progress_md("Alguma task", "feito")
        assert progress.read_text(encoding="utf-8") == antes

    def test_sem_projeto_app_factory_ativo_nao_grava(self, tmp_path):
        """Nem todo workspace é um projeto App Factory — sem projeto ativo,
        'PROGRESS.md' não tem lar óbvio."""
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            router._append_progress_md("Task", "ok")  # não pode levantar
        assert not any(tmp_path.rglob("PROGRESS.md"))

    def test_projeto_ativo_sem_progress_md_nao_cria(self, router, tmp_path):
        """Não inventa a estrutura do projeto — só ESCREVE num PROGRESS.md
        que já existe."""
        proj = tmp_path / "app"
        af.init_project(proj, idea="x")
        (proj / "docs" / "PROGRESS.md").unlink()
        af.set_active_project(tmp_path, proj)
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            router._append_progress_md("Task", "ok")
        assert not (proj / "docs" / "PROGRESS.md").exists()

    def test_config_ilegivel_nao_quebra(self, router, projeto_ativo):
        with patch("bauer.config_loader.load_config", side_effect=RuntimeError("boom")):
            router._append_progress_md("Task", "ok")  # cai em markdown; não levanta


class TestGravaCorretamente:
    def test_grava_linha_com_titulo_e_resultado(self, router, projeto_ativo):
        progress = projeto_ativo / "docs" / "PROGRESS.md"
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            router._append_progress_md("Criar tela de login", "endpoint + UI prontos")
        texto = progress.read_text(encoding="utf-8")
        assert "Criar tela de login" in texto
        assert "endpoint + UI prontos" in texto
        assert "Concluído via kanban" in texto

    def test_sem_resultado_ainda_grava_o_titulo(self, router, projeto_ativo):
        progress = projeto_ativo / "docs" / "PROGRESS.md"
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            router._append_progress_md("Setup inicial", "")
        assert "Setup inicial" in progress.read_text(encoding="utf-8")

    def test_carimbo_de_data_hora_presente(self, router, projeto_ativo):
        import re
        progress = projeto_ativo / "docs" / "PROGRESS.md"
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            router._append_progress_md("Task", "ok")
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", progress.read_text(encoding="utf-8"))

    def test_encadeia_sem_linha_em_branco_extra(self, router, projeto_ativo):
        """Duas conclusões seguidas viram duas linhas de lista markdown
        encadeadas — não duas listas separadas por linha em branco.

        O scaffold do PROGRESS.md (bauer/data/app_factory/templates/PROGRESS.md)
        já tem placeholders "- [ ] ..." nas seções Feito/Em andamento/Próximo
        passo — filtrar por "- [" pegaria eles também. O marcador exclusivo das
        linhas que este código escreve é "Concluído via kanban".
        """
        progress = projeto_ativo / "docs" / "PROGRESS.md"
        antes = progress.read_text(encoding="utf-8")
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            router._append_progress_md("Primeira", "ok")
            router._append_progress_md("Segunda", "ok")
        depois = progress.read_text(encoding="utf-8")
        assert depois.count("Concluído via kanban") == 2
        # só o que foi ACRESCENTADO não pode ter linha em branco extra entre
        # as duas entradas novas — o conteúdo original do scaffold não é
        # tocado (pode ter suas próprias linhas em branco entre seções).
        acrescentado = depois[len(antes):]
        assert "\n\n" not in acrescentado

    def test_respeita_conteudo_existente_sem_quebra_final(self, router, projeto_ativo):
        """Arquivo que NÃO termina em \\n ganha uma quebra antes da nova
        linha — senão a entrada nova coласaria na última linha existente."""
        progress = projeto_ativo / "docs" / "PROGRESS.md"
        progress.write_text("# Progresso\ntexto sem quebra final", encoding="utf-8")
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            router._append_progress_md("Task", "ok")
        texto = progress.read_text(encoding="utf-8")
        assert "texto sem quebra final\n- [" in texto  # não grudou na linha anterior


class TestLigadoAoKanbanComplete:
    """Prova de ponta: completar um card de verdade grava em PROGRESS.md
    sozinho, sem o modelo precisar fazer nada."""

    def test_kanban_complete_grava_progress_md(self, tmp_path):
        from bauer.tool_router import ToolRouter

        proj = tmp_path / "app"
        af.init_project(proj, idea="x")
        af.set_active_project(tmp_path, proj)
        progress = proj / "docs" / "PROGRESS.md"

        router = ToolRouter(workspace=tmp_path)
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            criado = router._kanban_create({"title": "Fazer a coisa"})
            task_id = criado.split()[3]  # "[kanban] Tarefa criada: T0001 — ..."
            router._kanban_complete({"task_id": task_id, "result": "coisa feita"})

        texto = progress.read_text(encoding="utf-8")
        assert "Fazer a coisa" in texto
        assert "coisa feita" in texto
