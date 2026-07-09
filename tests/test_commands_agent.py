"""Testes de inicialização para bauer/commands/agent_cmd.py.

Cobre: import correto pós-P4, erros de config ausente, flags de CLI,
       subcomandos registrados e listagem de agentes.
NÃO cobre o loop interativo do agente (requer input real do terminal).
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("typer")
from typer.testing import CliRunner

from bauer.cli import app

runner = CliRunner()


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    c = tmp_path / "config.yaml"
    c.write_text(
        "agent:\n  name: Test\n  workspace: ./workspace\n"
        "model:\n  provider: ollama\n  name: qwen2.5:3b\n"
        "  requested_context: 8192\n  minimum_context: 4096\n"
        "  auto_downgrade_context: true\n"
        "ollama:\n  host: http://localhost:11434\n  timeout_seconds: 10\n  api_key: ''\n"
        "openai:\n  host: http://localhost:1234\n  timeout_seconds: 30\n  api_key: ''\n"
        "runtime:\n  profile: low\n  ram_limit_mb: 4096\n  safety_margin_mb: 512\n"
        "logging:\n  level: info\n  file: null\n"
        "tools:\n  shell_enabled: false\n  safe_mode: true\n"
        "  timeout_seconds: 30\n  max_output_kb: 50\n"
        "serve:\n  host: 0.0.0.0\n  port: 8000\n  api_key: ''\n  workers: 1\n",
        encoding="utf-8",
    )
    return c


# ─── Import / atributos esperados pós-refactor P4 ─────────────────────────────


def test_agent_module_importable():
    """Garantia de import pós-refactor P4: agent_app deve estar acessível."""
    from bauer.commands import agent_cmd
    assert hasattr(agent_cmd, "agent_app")


# ─── CLI help / subcomandos registrados ───────────────────────────────────────


def test_agent_help_works():
    """bauer agent --help deve sair com código 0 e mostrar o nome do grupo."""
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0
    assert "agent" in result.output.lower()


def test_agent_subcommands_registered():
    """Subcomandos create, list, run, delete devem aparecer no --help."""
    result = runner.invoke(app, ["agent", "--help"])
    assert result.exit_code == 0
    output_lower = result.output.lower()
    # Verifica os subcomandos confirmados em agent_cmd.py (linhas 556, 577, 612, 913)
    assert "create" in output_lower
    assert "list" in output_lower
    assert "run" in output_lower
    assert "delete" in output_lower


# ─── agent list (sem agentes criados) ─────────────────────────────────────────


def test_agent_list_empty(tmp_path: Path):
    """bauer agent list sem agents.yaml deve mostrar mensagem de lista vazia sem traceback."""
    agents_file = tmp_path / "agents.yaml"  # não existe — registry vazio
    result = runner.invoke(app, ["agent", "list", "--agents", str(agents_file)])
    assert result.exception is None
    assert result.exit_code == 0
    assert "Traceback" not in result.output


# ─── agent missing config ─────────────────────────────────────────────────────


def test_agent_missing_config_exits_cleanly(tmp_path: Path, monkeypatch):
    """bauer agent sem config.yaml deve sair com erro claro, não traceback."""
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "empty"))
    result = runner.invoke(app, [
        "agent",
        "--config", str(tmp_path / "nao_existe.yaml"),
    ])
    assert result.exit_code != 0
    # Não deve expor traceback Python ao usuário
    assert "Traceback" not in result.output


# ─── agent run-one --agent (especialista) ─────────────────────────────────────
# Regressão: delegate_task passava a tarefa direto pro CLI sem nenhum system
# prompt, mesmo quando um agent_name apontava pra um especialista LOCAL — a
# "especialização" não fazia diferença nenhuma na resposta.


def test_run_one_without_agent_uses_bare_user_message(monkeypatch, tmp_path: Path):
    """Sem --agent: comportamento de sempre — só a mensagem do usuário, sem system."""
    captured = {}

    class _FakeClient:
        default_model = "fake-model"

        def chat_stream(self, model, messages):
            captured["model"] = model
            captured["messages"] = messages
            return iter(["ok"])

    import bauer.commands.agent_cmd as agent_cmd_mod
    monkeypatch.setattr(agent_cmd_mod, "_build_client", lambda cfg: _FakeClient())
    monkeypatch.setattr(agent_cmd_mod, "_load_or_die", lambda config, models: (_FakeCfg(), None))

    result = runner.invoke(app, ["agent", "run-one", "faca algo"])
    assert result.exit_code == 0
    assert captured["messages"] == [{"role": "user", "content": "faca algo"}]


def test_run_one_with_agent_prepends_system_prompt(monkeypatch, tmp_path: Path):
    """Com --agent: system prompt do especialista vai como primeira mensagem."""
    import yaml as _yaml

    agents_file = tmp_path / "agents.yaml"
    agents_file.write_text(
        _yaml.dump({"agents": [{
            "name": "devops-specialist",
            "description": "DevOps",
            "system": "Voce e um especialista DevOps.",
        }]}, allow_unicode=True),
        encoding="utf-8",
    )

    captured = {}

    class _FakeClient:
        default_model = "fake-model"

        def chat_stream(self, model, messages):
            captured["model"] = model
            captured["messages"] = messages
            return iter(["ok"])

    import bauer.commands.agent_cmd as agent_cmd_mod
    monkeypatch.setattr(agent_cmd_mod, "_build_client", lambda cfg: _FakeClient())
    monkeypatch.setattr(agent_cmd_mod, "_load_or_die", lambda config, models: (_FakeCfg(), None))

    result = runner.invoke(app, [
        "agent", "run-one", "configure um pipeline",
        "--agent", "devops-specialist", "--agents", str(agents_file),
    ])
    assert result.exit_code == 0
    assert captured["messages"][0] == {"role": "system", "content": "Voce e um especialista DevOps."}
    assert captured["messages"][1] == {"role": "user", "content": "configure um pipeline"}


def test_run_one_unknown_agent_falls_back_to_bare_message(monkeypatch, tmp_path: Path):
    """--agent apontando pra um nome que não existe no registry não deve quebrar
    — degrada pro comportamento sem especialização."""
    agents_file = tmp_path / "agents.yaml"  # vazio/inexistente

    captured = {}

    class _FakeClient:
        default_model = "fake-model"

        def chat_stream(self, model, messages):
            captured["messages"] = messages
            return iter(["ok"])

    import bauer.commands.agent_cmd as agent_cmd_mod
    monkeypatch.setattr(agent_cmd_mod, "_build_client", lambda cfg: _FakeClient())
    monkeypatch.setattr(agent_cmd_mod, "_load_or_die", lambda config, models: (_FakeCfg(), None))

    result = runner.invoke(app, [
        "agent", "run-one", "tarefa",
        "--agent", "nao-existe", "--agents", str(agents_file),
    ])
    assert result.exit_code == 0
    assert captured["messages"] == [{"role": "user", "content": "tarefa"}]


class _FakeCfg:
    class model:
        name = "fake-model"


# ─── _resolve_cwd_project (detecção/adoção da pasta atual como workspace) ─────


class TestResolveCwdProject:
    """A cola de CLI: detecção automática + adoção com uma confirmação."""

    def _isolate(self, monkeypatch, tmp_path):
        reg = tmp_path / "projects.json"
        monkeypatch.setattr("bauer.projects_registry._DEFAULT_REGISTRY", reg)
        return reg

    def test_registered_dir_auto_used_without_prompt(self, monkeypatch, tmp_path):
        import bauer.commands.agent_cmd as m
        from bauer import projects_registry as pr

        self._isolate(monkeypatch, tmp_path)
        proj = tmp_path / "meu-projeto"
        proj.mkdir()
        pr.add_project(proj)

        # confirm NUNCA deve ser chamado para pasta já registrada
        def _boom(*a, **k):
            raise AssertionError("não deveria pedir confirmação")
        monkeypatch.setattr(m.typer, "confirm", _boom)

        got = m._resolve_cwd_project(interactive=True, cwd=proj)
        assert got is not None
        assert got.resolve() == proj.resolve()

    def test_new_folder_adopted_on_yes(self, monkeypatch, tmp_path):
        import bauer.commands.agent_cmd as m
        from bauer import projects_registry as pr

        self._isolate(monkeypatch, tmp_path)
        proj = tmp_path / "novo-vazio"
        proj.mkdir()  # pasta VAZIA — o fluxo do usuário

        monkeypatch.setattr(m.typer, "confirm", lambda *a, **k: True)
        got = m._resolve_cwd_project(interactive=True, cwd=proj)

        assert got is not None and got.resolve() == proj.resolve()
        # adoção registrou o projeto (aparece na tela Projetos)
        assert pr.find_project_for_cwd(proj) is not None

    def test_new_folder_declined_keeps_default(self, monkeypatch, tmp_path):
        import bauer.commands.agent_cmd as m
        from bauer import projects_registry as pr

        self._isolate(monkeypatch, tmp_path)
        proj = tmp_path / "recusado"
        proj.mkdir()

        monkeypatch.setattr(m.typer, "confirm", lambda *a, **k: False)
        got = m._resolve_cwd_project(interactive=True, cwd=proj)

        assert got is None
        assert pr.find_project_for_cwd(proj) is None  # não registrou

    def test_non_interactive_never_adopts(self, monkeypatch, tmp_path):
        import bauer.commands.agent_cmd as m
        from bauer import projects_registry as pr

        self._isolate(monkeypatch, tmp_path)
        proj = tmp_path / "sem-tty"
        proj.mkdir()

        def _boom(*a, **k):
            raise AssertionError("não deveria perguntar sem TTY")
        monkeypatch.setattr(m.typer, "confirm", _boom)

        got = m._resolve_cwd_project(interactive=False, cwd=proj)
        assert got is None
        assert pr.find_project_for_cwd(proj) is None

    def test_sensitive_dir_never_adopts(self, monkeypatch, tmp_path):
        import bauer.commands.agent_cmd as m

        self._isolate(monkeypatch, tmp_path)

        def _boom(*a, **k):
            raise AssertionError("não deveria perguntar em pasta sensível")
        monkeypatch.setattr(m.typer, "confirm", _boom)

        got = m._resolve_cwd_project(interactive=True, cwd=Path.home())
        assert got is None
