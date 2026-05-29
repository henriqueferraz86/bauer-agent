"""Testes para AgentRegistry.match() e orchestrator.list_saved_progress."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ─── AgentRegistry.match / auto_select ───────────────────────────────────────

class TestAgentRegistryMatch:
    def _make_registry(self, tmp_path: Path, agents: list[dict]):
        import yaml
        from bauer.agent_registry import AgentRegistry

        agents_file = tmp_path / "agents.yaml"
        agents_file.write_text(
            yaml.dump({"agents": agents}, allow_unicode=True),
            encoding="utf-8",
        )
        return AgentRegistry(agents_file)

    def test_match_returns_best_agent(self, tmp_path: Path):
        """Agente com maior overlap de palavras é retornado."""
        reg = self._make_registry(tmp_path, [
            {
                "name": "python-expert",
                "description": "Especialista em Python, scripts e automação",
                "system": "Você é um expert em Python.",
            },
            {
                "name": "devops",
                "description": "Docker, Kubernetes, CI/CD, infraestrutura",
                "system": "Você é um especialista em devops.",
            },
        ])
        result = reg.match("criar um script Python para automação de tarefas")
        assert result is not None
        assert result.name == "python-expert"

    def test_match_returns_devops_for_docker(self, tmp_path: Path):
        reg = self._make_registry(tmp_path, [
            {
                "name": "python-expert",
                "description": "Python scripts automação",
                "system": "Expert Python",
            },
            {
                "name": "devops",
                "description": "Docker Kubernetes CI/CD infraestrutura",
                "system": "Expert devops",
            },
        ])
        result = reg.match("configurar Docker container Kubernetes")
        assert result is not None
        assert result.name == "devops"

    def test_match_empty_registry_returns_none(self, tmp_path: Path):
        reg = self._make_registry(tmp_path, [])
        result = reg.match("qualquer tarefa")
        assert result is None

    def test_match_no_overlap_returns_none(self, tmp_path: Path):
        reg = self._make_registry(tmp_path, [
            {
                "name": "python-expert",
                "description": "Python automação scripts",
                "system": "Expert Python",
            }
        ])
        # Threshold alto + zero overlap
        result = reg.match("xyzzy foo bar baz qux", threshold=0.5)
        assert result is None

    def test_match_empty_task_returns_none(self, tmp_path: Path):
        reg = self._make_registry(tmp_path, [
            {"name": "test", "description": "test agent", "system": "test"}
        ])
        result = reg.match("")
        assert result is None

    def test_auto_select_wraps_match(self, tmp_path: Path):
        """auto_select() é alias de match()."""
        reg = self._make_registry(tmp_path, [
            {
                "name": "python-expert",
                "description": "Python scripts automação",
                "system": "Expert Python",
            }
        ])
        r1 = reg.match("Python script")
        r2 = reg.auto_select("Python script")
        if r1 is None:
            assert r2 is None
        else:
            assert r2 is not None
            assert r1.name == r2.name

    def test_match_threshold_filters_weak_matches(self, tmp_path: Path):
        """Matches abaixo do threshold são filtrados."""
        reg = self._make_registry(tmp_path, [
            {
                "name": "test-agent",
                "description": "Agente de teste genérico para demo",
                "system": "Agente simples"
            }
        ])
        # Threshold muito alto — deve retornar None
        result = reg.match("Python Docker Kubernetes microservicos", threshold=0.9)
        assert result is None

    def test_match_no_agents_yaml_returns_none(self, tmp_path: Path):
        from bauer.agent_registry import AgentRegistry
        reg = AgentRegistry(tmp_path / "nonexistent.yaml")
        result = reg.match("qualquer tarefa")
        assert result is None


# ─── Orchestrator list_saved_progress ────────────────────────────────────────

class TestOrchestratorListProgress:
    def _make_orch(self, tmp_path: Path):
        from bauer.orchestrator import AgentOrchestrator, OrchestratorConfig
        import hashlib

        orch = AgentOrchestrator(MagicMock(), MagicMock(), MagicMock(), OrchestratorConfig())

        def _patched_path(task: str) -> Path:
            h = hashlib.md5(task.encode("utf-8")).hexdigest()[:10]
            return tmp_path / ".orchestrate_progress" / h

        orch._progress_path = _patched_path  # type: ignore[method-assign]
        return orch

    def test_list_empty(self, tmp_path: Path):
        orch = self._make_orch(tmp_path)
        # Simula _base_dir vazia
        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(tmp_path)
            result = orch.list_saved_progress()
        assert result == []

    def test_list_with_saved_task(self, tmp_path: Path):
        orch = self._make_orch(tmp_path)
        task = "minha tarefa de teste"

        plan = [
            {"id": 1, "goal": "passo 1", "depends_on": []},
            {"id": 2, "goal": "passo 2", "depends_on": [1]},
        ]

        orch.save_plan(task, plan)

        from bauer.orchestrator import StepResult
        orch.save_progress(task, [StepResult(
            id=1, goal="passo 1", model_used="phi4-mini", response="ok", tool_log=[]
        )])

        with pytest.MonkeyPatch.context() as mp:
            # list_saved_progress usa Path(".orchestrate_progress")
            # que precisa ser relativo ao cwd
            # Pegamos o path real do progress e criamos symlink ou mudamos cwd
            progress_dir = orch._progress_path(task).parent
            mp.chdir(progress_dir.parent)
            entries = orch.list_saved_progress()

        assert len(entries) >= 1
        entry = entries[0]
        assert "task" in entry
        assert "steps_done" in entry
        assert "steps_total" in entry
        assert entry["steps_done"] == 1
        assert entry["steps_total"] == 2

    def test_orchestrate_cancel_command(self, tmp_path: Path):
        """bauer orchestrate cancel --all sem tarefas é ok."""
        from typer.testing import CliRunner
        from bauer.cli import app

        runner = CliRunner()
        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(tmp_path)
            result = runner.invoke(app, ["orchestrate", "cancel", "--all", "--force"])

        assert result.exit_code == 0
        assert "Nenhuma" in result.output or "cancelada" in result.output

    def test_orchestrate_list_command_empty(self, tmp_path: Path):
        """bauer orchestrate list sem tarefas é ok."""
        from typer.testing import CliRunner
        from bauer.cli import app

        runner = CliRunner()
        with pytest.MonkeyPatch.context() as mp:
            mp.chdir(tmp_path)
            result = runner.invoke(app, ["orchestrate", "list"])

        assert result.exit_code == 0
        assert "Nenhuma" in result.output or "tarefas" in result.output.lower()
