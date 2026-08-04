"""Testes do checkpoint de planejamento da App Factory (gate → /loop).

Cobre: parsing do BACKLOG.md, seed do kanban, detecção do cruzamento de gate
(PLANNING→IMPLEMENTATION) e as 3 opções (Revisar/Desenvolver/Continuar), com
degradê para não-interativo.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from bauer import app_factory as af
from bauer.agent import (
    _af_active_gate,
    _ensure_kanban_seeded,
    _maybe_planning_checkpoint,
    _parse_backlog_tasks,
    _resolve_planning_checkpoint,
    _seed_kanban_from_backlog,
)


# ─── _parse_backlog_tasks ────────────────────────────────────────────────────

class TestParseBacklog:
    def test_extrai_itens_de_topo_com_fase(self):
        text = (
            "# BACKLOG\n"
            "## Fase 1 — Fundacao\n"
            "- [ ] Criar estrutura do projeto\n"
            "  - Prioridade: alta\n"
            "  - Criterio: pronto\n"
            "- [ ] Criar login\n"
            "  - Prioridade: media\n"
            "## Fase 2 — Slice\n"
            "- [ ] Fluxo ponta a ponta\n"
            "  - Prioridade: baixa\n"
        )
        tasks = _parse_backlog_tasks(text)
        titles = [t["title"] for t in tasks]
        assert titles == ["Criar estrutura do projeto", "Criar login", "Fluxo ponta a ponta"]
        assert tasks[0]["priority"] == "high"
        assert tasks[1]["priority"] == "medium"
        assert tasks[2]["priority"] == "low"
        assert tasks[0]["phase"] == "Fase 1 — Fundacao"
        assert tasks[2]["phase"] == "Fase 2 — Slice"

    def test_ignora_sub_bullets_e_nao_checkbox(self):
        text = (
            "- [ ] Task real\n"
            "  - [ ] subtask aninhada (ignorada)\n"
            "- item sem checkbox (ignorado)\n"
            "  - Prioridade: alta\n"
        )
        tasks = _parse_backlog_tasks(text)
        assert [t["title"] for t in tasks] == ["Task real"]

    def test_checkbox_marcado_tambem_conta(self):
        tasks = _parse_backlog_tasks("- [x] Ja feito\n- [ ] A fazer\n")
        assert [t["title"] for t in tasks] == ["Ja feito", "A fazer"]

    def test_cap_limita_quantidade(self):
        text = "\n".join(f"- [ ] Task {i}" for i in range(100))
        tasks = _parse_backlog_tasks(text, cap=10)
        assert len(tasks) == 10

    def test_vazio(self):
        assert _parse_backlog_tasks("# so titulo\nsem tasks\n") == []

    def test_fallback_para_heading_de_story_sem_checkbox(self):
        """Visto na prática (financeos-pme): o modelo às vezes descreve o
        backlog como '### Story N.M: Título' em vez do checkbox do template
        (data/app_factory/templates/BACKLOG.md). Sem fallback, um BACKLOG.md
        real e completo semeava ZERO cards."""
        text = (
            "## Fase 1 — Fundação\n\n"
            "### Story 1.1: Criar estrutura do projeto\n"
            "- **Prioridade:** alta\n"
            "- **Descrição:** blá\n\n"
            "### Story 1.2: Criar ambiente local\n"
            "- **Prioridade:** média\n"
        )
        tasks = _parse_backlog_tasks(text)
        titles = [t["title"] for t in tasks]
        assert titles == [
            "Story 1.1: Criar estrutura do projeto",
            "Story 1.2: Criar ambiente local",
        ]
        assert tasks[0]["priority"] == "high"
        assert tasks[1]["priority"] == "medium"
        assert tasks[0]["phase"] == "Fase 1 — Fundação"

    def test_checkbox_tem_prioridade_sobre_o_fallback(self):
        """Se o doc tem AO MENOS UM checkbox reconhecível, o fallback de
        heading nunca entra em jogo — evita reinterpretar um BACKLOG.md que
        segue o template mas também tem headings '###' por outro motivo."""
        text = "- [ ] Task do template\n### Não é um item de backlog\n"
        tasks = _parse_backlog_tasks(text)
        assert [t["title"] for t in tasks] == ["Task do template"]


# ─── _seed_kanban_from_backlog ───────────────────────────────────────────────

class TestSeedKanban:
    def _router(self, ws):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=ws)

    def test_seed_cria_cards_reais(self, tmp_path):
        proj = tmp_path / "meu-app"
        af.init_project(proj, idea="x")
        (proj / "docs" / "BACKLOG.md").write_text(
            "## Fase 1\n- [ ] Task A\n  - Prioridade: alta\n- [ ] Task B\n",
            encoding="utf-8",
        )
        router = self._router(tmp_path)
        console = MagicMock()
        n = _seed_kanban_from_backlog(router, proj, console)
        assert n == 2
        # cards aparecem no MESMO store que o ledger do loop le
        from bauer.workspace_manager import WorkspaceManager
        titles = [t.title for t in WorkspaceManager(tmp_path).list_tasks()]
        assert "Task A" in titles and "Task B" in titles

    def test_seed_sem_backlog_retorna_zero(self, tmp_path):
        proj = tmp_path / "app"
        proj.mkdir()
        router = self._router(tmp_path)
        assert _seed_kanban_from_backlog(router, proj, MagicMock()) == 0

    def test_seed_backlog_sem_itens_retorna_zero(self, tmp_path):
        proj = tmp_path / "app"
        af.init_project(proj, idea="x")
        (proj / "docs" / "BACKLOG.md").write_text("# vazio\nsem checkbox\n", encoding="utf-8")
        assert _seed_kanban_from_backlog(self._router(tmp_path), proj, MagicMock()) == 0


# ─── _af_active_gate ─────────────────────────────────────────────────────────

class TestActiveGate:
    def test_sem_projeto_ativo(self, tmp_path):
        assert _af_active_gate(tmp_path) == (None, None)

    def test_projeto_em_discovery(self, tmp_path):
        proj = tmp_path / "app"
        af.init_project(proj, idea="x")
        af.set_active_project(tmp_path, proj)
        _proj, gate = _af_active_gate(tmp_path)
        assert gate == int(af.Gate.DISCOVERY)

    def test_projeto_em_implementation(self, tmp_path):
        proj = tmp_path / "app"
        af.init_project(proj, idea="x")
        for d in af.PLANNING_DOCS:
            (proj / "docs" / d).write_text("conteudo real " * 30, encoding="utf-8")
        af.set_active_project(tmp_path, proj)
        _proj, gate = _af_active_gate(tmp_path)
        assert gate >= int(af.Gate.IMPLEMENTATION)


# ─── _maybe_planning_checkpoint ──────────────────────────────────────────────

class TestCheckpoint:
    def _completed_project(self, tmp_path):
        proj = tmp_path / "bauerinvest"
        af.init_project(proj, idea="Plataforma de investimentos")
        for d in af.PLANNING_DOCS:
            (proj / "docs" / d).write_text("conteudo real preenchido " * 30, encoding="utf-8")
        af.set_active_project(tmp_path, proj)
        return proj

    def test_desabilitado_retorna_none(self, tmp_path):
        self._completed_project(tmp_path)
        assert _maybe_planning_checkpoint(
            MagicMock(), MagicMock(), tmp_path, gate_before=1, enabled=False
        ) is None

    def test_nao_interativo_degrada(self, tmp_path):
        self._completed_project(tmp_path)
        with patch("sys.stdin.isatty", return_value=False):
            assert _maybe_planning_checkpoint(
                MagicMock(), MagicMock(), tmp_path, gate_before=1, enabled=True
            ) is None

    def test_sem_cruzamento_nao_dispara(self, tmp_path):
        # ja estava em IMPLEMENTATION antes → nao e cruzamento
        self._completed_project(tmp_path)
        with patch("sys.stdin.isatty", return_value=True):
            assert _maybe_planning_checkpoint(
                MagicMock(), MagicMock(), tmp_path,
                gate_before=int(af.Gate.IMPLEMENTATION), enabled=True,
            ) is None

    def test_opcao_continuar(self, tmp_path):
        self._completed_project(tmp_path)
        with patch("sys.stdin.isatty", return_value=True), \
             patch("rich.prompt.Prompt.ask", return_value="C"):
            result = _maybe_planning_checkpoint(
                MagicMock(), MagicMock(), tmp_path, gate_before=1, enabled=True
            )
        assert result == ("continuar", "")

    def test_opcao_revisar(self, tmp_path):
        self._completed_project(tmp_path)
        with patch("sys.stdin.isatty", return_value=True), \
             patch("rich.prompt.Prompt.ask", return_value="R"):
            result = _maybe_planning_checkpoint(
                MagicMock(), MagicMock(), tmp_path, gate_before=0, enabled=True
            )
        assert result == ("revisar", "")

    def test_opcao_desenvolver_sem_seed(self, tmp_path):
        self._completed_project(tmp_path)
        with patch("sys.stdin.isatty", return_value=True), \
             patch("rich.prompt.Prompt.ask", return_value="D"), \
             patch("rich.prompt.Confirm.ask", return_value=False):
            action, task = _maybe_planning_checkpoint(
                MagicMock(), MagicMock(), tmp_path, gate_before=1, enabled=True
            )
        assert action == "develop"
        assert "bauerinvest" in task
        assert "verify_app" in task

    def test_opcao_desenvolver_com_seed_kanban(self, tmp_path):
        from bauer.tool_router import ToolRouter
        proj = self._completed_project(tmp_path)
        (proj / "docs" / "BACKLOG.md").write_text(
            "## Fase 1\n- [ ] Setup\n- [ ] Login\n", encoding="utf-8"
        )
        router = ToolRouter(workspace=tmp_path)
        with patch("sys.stdin.isatty", return_value=True), \
             patch("rich.prompt.Prompt.ask", return_value="D"), \
             patch("rich.prompt.Confirm.ask", return_value=True):
            action, task = _maybe_planning_checkpoint(
                MagicMock(), router, tmp_path, gate_before=1, enabled=True
            )
        assert action == "develop"
        assert "kanban" in task.lower()  # instrucao de trabalhar pelos cards
        from bauer.workspace_manager import WorkspaceManager
        titles = [t.title for t in WorkspaceManager(tmp_path).list_tasks()]
        assert "Setup" in titles and "Login" in titles


# ─── _resolve_planning_checkpoint ────────────────────────────────────────────

class TestResolveToggle:
    def test_default_true(self):
        from bauer.config_loader import AgentSection
        assert AgentSection().planning_checkpoint is True

    def test_degrada_para_true_se_config_falha(self):
        with patch("bauer.config_loader.load_config", side_effect=Exception("boom")):
            assert _resolve_planning_checkpoint() is True


# ─── _ensure_kanban_seeded — kanban = fonte única quando task_backend=sqlite ─
#
# Motivação (relatado em uso real): "por que o kanban está vazio?" — um
# projeto App Factory já tinha cruzado para IMPLEMENTATION, mas o kanban
# nunca foi semeado porque a oferta antiga só disparava NA SESSÃO em que o
# cruzamento acontecia (Confirm.ask no instante exato). Sessão seguinte, ou
# /loop (sem TTY): nunca mais oferecido. `_ensure_kanban_seeded` existe para
# não depender de "estar presente no momento certo" — só olha o ESTADO atual
# (gate + board vazio) e semeia se fizer sentido, idempotente.

def _cfg_com_backend(valor: str):
    """Objeto mínimo com `.agent.task_backend` — o que `resolve_task_backend`
    lê de `load_config()`."""
    return type("Cfg", (), {"agent": type("Agent", (), {"task_backend": valor})()})()


class TestEnsureKanbanSeeded:
    def _router(self, ws):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=ws)

    def _projeto_pronto(self, tmp_path, *, backlog="## Fase 1\n- [ ] Setup\n- [ ] Login\n"):
        proj = tmp_path / "app"
        af.init_project(proj, idea="x")
        for d in af.PLANNING_DOCS:
            (proj / "docs" / d).write_text("conteudo real " * 30, encoding="utf-8")
        if backlog is not None:
            (proj / "docs" / "BACKLOG.md").write_text(backlog, encoding="utf-8")
        return proj

    def test_markdown_backend_e_no_op(self, tmp_path):
        """Sem sqlite, o fluxo antigo (Confirm interativo) segue mandando —
        esta função nem entra em ação."""
        proj = self._projeto_pronto(tmp_path)
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("markdown")):
            n = _ensure_kanban_seeded(self._router(tmp_path), proj, MagicMock())
        assert n == 0
        from bauer.workspace_manager_sqlite import WorkspaceManagerSqlite
        assert WorkspaceManagerSqlite(tmp_path).list_tasks() == []

    def test_gate_abaixo_de_implementation_e_no_op(self, tmp_path):
        proj = tmp_path / "app"
        af.init_project(proj, idea="x")  # só DISCOVERY — docs vazios
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            n = _ensure_kanban_seeded(self._router(tmp_path), proj, MagicMock())
        assert n == 0

    @staticmethod
    def _board_tasks(workspace):
        """Lê o MESMO board que `_ensure_kanban_seeded` escreve —
        `get_workspace_manager` resolve por `board_for_workspace` (hash do
        caminho do workspace), não pela variável `BAUER_KANBAN_BOARD` que
        isola os outros testes da suíte. Um `WorkspaceManagerSqlite(ws)` sem
        `board=` explícito cai nessa outra convenção e leria um board vazio —
        foi o que a primeira versão deste teste fez, e falhou por isso."""
        from bauer.workspace_manager_factory import get_workspace_manager
        return get_workspace_manager(workspace, backend="sqlite").list_tasks()

    def test_semeia_quando_sqlite_implementation_e_board_vazio(self, tmp_path):
        proj = self._projeto_pronto(tmp_path)
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            n = _ensure_kanban_seeded(self._router(tmp_path), proj, MagicMock())
        assert n == 2
        titles = [t.title for t in self._board_tasks(tmp_path)]
        assert "Setup" in titles and "Login" in titles

    def test_nao_duplica_quando_board_ja_tem_cards(self, tmp_path):
        """Idempotência: rodar de novo (ex.: todo boot de sessão) não recria."""
        proj = self._projeto_pronto(tmp_path)
        cfg = _cfg_com_backend("sqlite")
        with patch("bauer.config_loader.load_config", return_value=cfg):
            primeira = _ensure_kanban_seeded(self._router(tmp_path), proj, MagicMock())
            segunda = _ensure_kanban_seeded(self._router(tmp_path), proj, MagicMock())
        assert primeira == 2
        assert segunda == 0
        assert len(self._board_tasks(tmp_path)) == 2  # não dobrou

    def test_backlog_sem_itens_retorna_zero(self, tmp_path):
        proj = self._projeto_pronto(tmp_path, backlog="# vazio\nsem checkbox\n")
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            n = _ensure_kanban_seeded(self._router(tmp_path), proj, MagicMock())
        assert n == 0

    def test_config_ilegivel_nao_quebra(self, tmp_path):
        proj = self._projeto_pronto(tmp_path)
        with patch("bauer.config_loader.load_config", side_effect=RuntimeError("boom")):
            n = _ensure_kanban_seeded(self._router(tmp_path), proj, MagicMock())
        assert n == 0  # cai no default markdown — não levanta

    def test_workspace_sem_projeto_nao_quebra(self, tmp_path):
        with patch("bauer.config_loader.load_config", return_value=_cfg_com_backend("sqlite")):
            n = _ensure_kanban_seeded(self._router(tmp_path), None, MagicMock())
        assert n == 0


class TestSeedNoBootDaSessao:
    """A metade que resolve o caso real: sessão NOVA, projeto que já cruzou o
    gate numa sessão ANTERIOR. Teste estrutural — `run_agent_session` é grande
    demais para montar um mock completo do turno; verificamos que o boot
    CHAMA `_ensure_kanban_seeded` logo após resolver o checkpoint, cobrindo o
    caso que o `Confirm.ask` do cruzamento ao vivo não cobre."""

    def test_boot_chama_ensure_kanban_seeded(self):
        import inspect
        from bauer import agent

        fonte = inspect.getsource(agent.run_agent_session)
        i_checkpoint = fonte.index("_resolve_planning_checkpoint()")
        i_seed = fonte.index("_ensure_kanban_seeded(router, _af_proj_boot")
        assert i_seed > i_checkpoint, (
            "o seed no boot precisa vir DEPOIS de saber se o checkpoint está "
            "habilitado — chamar antes ignoraria agent.planning_checkpoint=false"
        )
