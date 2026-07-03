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
