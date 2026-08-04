"""`kanban_create`/`kanban_complete` projetam o board em `<projeto>/docs/TASKS.md`.

Metade que faltava do "deve ser interligado" (a outra metade,
`_ensure_kanban_seeded`, deriva o kanban A PARTIR do BACKLOG.md — ver
test_planning_checkpoint.py). Sem isto, o kanban só existia no board sqlite
(view em `<workspace>/TASKS.md`, via `WorkspaceManagerSqlite`) — o
`<projeto>/docs/TASKS.md` que o App Factory escreveu ficava congelado no
planejamento, e quem abria a pasta do projeto não via o board.
"""

from __future__ import annotations

from unittest.mock import patch

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
    proj = tmp_path / "app"
    af.init_project(proj, idea="x")
    af.set_active_project(tmp_path, proj)
    return proj


class TestNaoProjetaForaDoContexto:
    def test_markdown_backend_nao_mexe(self, router, projeto_ativo):
        tasks_md = projeto_ativo / "docs" / "TASKS.md"
        antes = tasks_md.read_text(encoding="utf-8")
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("markdown")):
            router._kanban_create({"title": "Card qualquer"})
        assert tasks_md.read_text(encoding="utf-8") == antes

    def test_sem_projeto_ativo_nao_cria_nada(self, tmp_path):
        """Sem projeto App Factory ativo, não há `docs/` — só a view de
        workspace (`WorkspaceManagerSqlite`, pré-existente) pode aparecer na
        raiz; a projeção nova (`_regenerate_project_tasks_md`) não inventa
        uma estrutura de projeto."""
        from bauer.tool_router import ToolRouter
        router = ToolRouter(workspace=tmp_path)
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            router._kanban_create({"title": "Card qualquer"})
        assert not (tmp_path / "docs").exists()

    def test_projeto_sem_tasks_md_nao_inventa(self, router, tmp_path):
        proj = tmp_path / "app"
        af.init_project(proj, idea="x")
        (proj / "docs" / "TASKS.md").unlink()
        af.set_active_project(tmp_path, proj)
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            router._kanban_create({"title": "Card qualquer"})
        assert not (proj / "docs" / "TASKS.md").exists()


class TestProjetaCorretamente:
    def test_kanban_create_aparece_em_proximas(self, router, projeto_ativo):
        tasks_md = projeto_ativo / "docs" / "TASKS.md"
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            router._kanban_create({"title": "Fazer a coisa"})
        texto = tasks_md.read_text(encoding="utf-8")
        assert "Fazer a coisa" in texto
        secao_proximas = texto.split("## Próximas")[1].split("## Concluídas")[0]
        assert "Fazer a coisa" in secao_proximas

    def test_kanban_complete_move_para_concluidas(self, router, projeto_ativo):
        tasks_md = projeto_ativo / "docs" / "TASKS.md"
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            criado = router._kanban_create({"title": "Fazer a coisa"})
            task_id = criado.split()[3]
            router._kanban_complete({"task_id": task_id, "result": "feito"})
        texto = tasks_md.read_text(encoding="utf-8")
        secao_concluidas = texto.split("## Concluídas")[1]
        assert "Fazer a coisa" in secao_concluidas
        secao_proximas = texto.split("## Próximas")[1].split("## Concluídas")[0]
        assert "Fazer a coisa" not in secao_proximas

    def test_tarefa_sem_tag_conta_como_do_projeto_ativo(self, router, projeto_ativo):
        """kanban_create ad-hoc (sem passar por _seed_kanban_from_backlog) não
        tagueia a descrição — ainda assim tem que aparecer no projeto ativo."""
        tasks_md = projeto_ativo / "docs" / "TASKS.md"
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            router._kanban_create({"title": "Ad-hoc sem tag"})
        assert "Ad-hoc sem tag" in tasks_md.read_text(encoding="utf-8")

    def test_tarefa_tagueada_para_outro_projeto_nao_aparece(self, router, projeto_ativo):
        """Workspace com mais de um projeto App Factory: a tag `[nome]` (a
        mesma que `_seed_kanban_from_backlog` grava) desambigua — sem isso,
        tarefas do projeto B vazariam para o TASKS.md do projeto A."""
        tasks_md = projeto_ativo / "docs" / "TASKS.md"
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            router._kanban_create({"title": "Do outro projeto", "description": "[outro-app] Fase 1"})
            router._kanban_create({"title": "Deste projeto", "description": f"[{projeto_ativo.name}] Fase 1"})
        texto = tasks_md.read_text(encoding="utf-8")
        assert "Deste projeto" in texto
        assert "Do outro projeto" not in texto
