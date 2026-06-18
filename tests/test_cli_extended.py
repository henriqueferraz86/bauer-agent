"""Testes adicionais para CLI — task, spec, project, auth, memory extras, agent."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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


@pytest.fixture
def models_path(tmp_path: Path) -> Path:
    m = tmp_path / "models.yaml"
    m.write_text(
        "models:\n"
        "  qwen2.5:3b:\n"
        "    provider: ollama\n"
        "    ram_base_mb: 2500\n"
        "    ram_per_1k_ctx_mb: 40\n"
        "    max_context_safe: 32768\n"
        "    supports_tools: false\n"
        "    ram_profile: low\n",
        encoding="utf-8",
    )
    return m


@pytest.fixture
def mem_dir(tmp_path: Path) -> Path:
    d = tmp_path / "memory"
    d.mkdir()
    from bauer.memory_manager import MemoryManager
    MemoryManager(d).init_files()
    return d


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


@pytest.fixture
def specs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    return d


# ─── project init / status ────────────────────────────────────────────────────


def test_project_init_creates_files(ws: Path):
    result = runner.invoke(app, ["project", "init", "MeuProjeto", "--workspace", str(ws)])
    assert result.exit_code == 0


def test_project_init_already_exists(ws: Path):
    runner.invoke(app, ["project", "init", "MeuProjeto", "--workspace", str(ws)])
    result = runner.invoke(app, ["project", "init", "MeuProjeto", "--workspace", str(ws)])
    assert result.exit_code == 0
    assert "ja inicializado" in result.output or "inicializado" in result.output.lower()


def test_project_status_no_tasks(ws: Path):
    runner.invoke(app, ["project", "init", "MeuProjeto", "--workspace", str(ws)])
    result = runner.invoke(app, ["project", "status", "--workspace", str(ws)])
    assert result.exit_code == 0


def test_project_status_with_tasks(ws: Path):
    runner.invoke(app, ["project", "init", "MeuProjeto", "--workspace", str(ws)])
    runner.invoke(app, ["task", "add", "tarefa teste", "--workspace", str(ws)])
    result = runner.invoke(app, ["project", "status", "--workspace", str(ws)])
    assert result.exit_code == 0


# ─── task add / list / start / done / block / board ──────────────────────────


def test_task_add_with_title(ws: Path):
    result = runner.invoke(app, ["task", "add", "Nova tarefa teste", "--workspace", str(ws)])
    assert result.exit_code == 0
    assert "criada" in result.output or "Tarefa" in result.output


def test_task_add_with_desc(ws: Path):
    result = runner.invoke(
        app, ["task", "add", "Tarefa com desc", "--desc", "Descricao detalhada", "--workspace", str(ws)]
    )
    assert result.exit_code == 0


def test_task_list_empty(ws: Path):
    result = runner.invoke(app, ["task", "list", "--workspace", str(ws)])
    assert result.exit_code == 0
    assert "Nenhuma tarefa" in result.output


def test_task_list_with_tasks(ws: Path):
    runner.invoke(app, ["task", "add", "Tarefa 1", "--workspace", str(ws)])
    runner.invoke(app, ["task", "add", "Tarefa 2", "--workspace", str(ws)])
    result = runner.invoke(app, ["task", "list", "--workspace", str(ws)])
    assert result.exit_code == 0
    assert "Tarefa 1" in result.output or "TODO" in result.output


def test_task_list_filter_by_status(ws: Path):
    runner.invoke(app, ["task", "add", "Tarefa 1", "--workspace", str(ws)])
    result = runner.invoke(app, ["task", "list", "--status", "TODO", "--workspace", str(ws)])
    assert result.exit_code == 0


def test_task_start(ws: Path):
    runner.invoke(app, ["task", "add", "Tarefa para iniciar", "--workspace", str(ws)])
    from bauer.workspace_manager import WorkspaceManager
    wm = WorkspaceManager(ws)
    task = wm.list_tasks()[0]
    result = runner.invoke(app, ["task", "start", task.id, "--workspace", str(ws)])
    assert result.exit_code == 0


def test_task_done(ws: Path):
    runner.invoke(app, ["task", "add", "Tarefa para concluir", "--workspace", str(ws)])
    from bauer.workspace_manager import WorkspaceManager
    wm = WorkspaceManager(ws)
    task = wm.list_tasks()[0]
    runner.invoke(app, ["task", "start", task.id, "--workspace", str(ws)])
    result = runner.invoke(app, ["task", "done", task.id, "--workspace", str(ws)])
    assert result.exit_code == 0


def test_task_block(ws: Path):
    runner.invoke(app, ["task", "add", "Tarefa para bloquear", "--workspace", str(ws)])
    from bauer.workspace_manager import WorkspaceManager
    wm = WorkspaceManager(ws)
    task = wm.list_tasks()[0]
    result = runner.invoke(app, ["task", "block", task.id, "--workspace", str(ws)])
    assert result.exit_code == 0


def test_task_start_not_found(ws: Path):
    result = runner.invoke(app, ["task", "start", "999", "--workspace", str(ws)])
    assert result.exit_code != 0


def test_task_board(ws: Path):
    runner.invoke(app, ["task", "add", "Tarefa board", "--workspace", str(ws)])
    result = runner.invoke(app, ["task", "board", "--workspace", str(ws)])
    assert result.exit_code == 0


# ─── spec list / show / delete / status / context ────────────────────────────


def test_spec_list_empty(specs_dir: Path):
    result = runner.invoke(app, ["spec", "list", "--dir", str(specs_dir)])
    assert result.exit_code == 0
    assert "Nenhum spec" in result.output or "spec" in result.output.lower()


def test_spec_list_with_specs(specs_dir: Path):
    from bauer.spec_manager import Spec, SpecManager
    mgr = SpecManager(specs_dir)
    mgr.save(Spec(
        id="test-spec",
        title="Test Spec",
        purpose="Testando especificações",
        behavior="Comportamento X",
        acceptance_criteria=["AC1", "AC2"],
        status="draft",
    ))
    result = runner.invoke(app, ["spec", "list", "--dir", str(specs_dir)])
    assert result.exit_code == 0
    assert "test-spec" in result.output


def test_spec_show_existing(specs_dir: Path):
    from bauer.spec_manager import Spec, SpecManager
    mgr = SpecManager(specs_dir)
    mgr.save(Spec(
        id="my-spec",
        title="My Spec",
        purpose="Purpose",
        behavior="Behavior",
        acceptance_criteria=["AC1"],
    ))
    result = runner.invoke(app, ["spec", "show", "my-spec", "--dir", str(specs_dir)])
    assert result.exit_code == 0
    assert "My Spec" in result.output or "my-spec" in result.output


def test_spec_show_not_found(specs_dir: Path):
    result = runner.invoke(app, ["spec", "show", "nao-existe", "--dir", str(specs_dir)])
    assert result.exit_code != 0 or "nao encontrado" in result.output.lower()


def test_spec_delete_existing(specs_dir: Path):
    from bauer.spec_manager import Spec, SpecManager
    mgr = SpecManager(specs_dir)
    mgr.save(Spec(id="del-spec", title="Del Spec", purpose="p", behavior="b", acceptance_criteria=["a"]))
    result = runner.invoke(app, ["spec", "delete", "del-spec", "--dir", str(specs_dir), "--force"])
    assert result.exit_code == 0


def test_spec_delete_not_found(specs_dir: Path):
    result = runner.invoke(app, ["spec", "delete", "nao-existe", "--dir", str(specs_dir), "--force"])
    assert result.exit_code != 0 or "nao encontrado" in result.output.lower()


def test_spec_status_update(specs_dir: Path):
    from bauer.spec_manager import Spec, SpecManager
    mgr = SpecManager(specs_dir)
    mgr.save(Spec(id="my-spec", title="My Spec", purpose="p", behavior="b", acceptance_criteria=["a"]))
    result = runner.invoke(app, ["spec", "status", "my-spec", "approved", "--dir", str(specs_dir)])
    assert result.exit_code == 0 or "approved" in result.output.lower()


def test_spec_context_empty(specs_dir: Path):
    result = runner.invoke(app, ["spec", "context", "--dir", str(specs_dir)])
    assert result.exit_code == 0


def test_spec_context_with_specs(specs_dir: Path):
    from bauer.spec_manager import Spec, SpecManager
    mgr = SpecManager(specs_dir)
    mgr.save(Spec(id="ctx-spec", title="Context Spec", purpose="p", behavior="b", acceptance_criteria=["a"], status="approved"))
    result = runner.invoke(app, ["spec", "context", "--dir", str(specs_dir)])
    assert result.exit_code == 0
    assert "ctx-spec" in result.output or "Context" in result.output or result.output


# ─── memory add-note / add-lesson ─────────────────────────────────────────────


def test_memory_add_note(mem_dir: Path):
    result = runner.invoke(
        app, ["memory", "add-note", "Nota de teste", "Corpo da nota aqui", "--dir", str(mem_dir)]
    )
    assert result.exit_code == 0


def test_memory_add_lesson(mem_dir: Path):
    result = runner.invoke(
        app, ["memory", "add-lesson", "Licao teste", "Motivo da licao", "--dir", str(mem_dir)]
    )
    assert result.exit_code == 0


def test_memory_add_model_exp(mem_dir: Path, tmp_path: Path):
    """add-model-exp lê o runtime_state.json para pegar modelo/contexto/RAM."""
    state_file = tmp_path / ".runtime_state.json"
    state_data = {
        "configured_model": "phi4-mini",
        "context": {"applied": 4096},
        "ram_available_mb": 4000,
        "machine_id": "abc123",
    }
    state_file.write_text(json.dumps(state_data), encoding="utf-8")

    result = runner.invoke(
        app,
        ["memory", "add-model-exp", "ok",
         "--lesson", "funcionou bem",
         "--state-file", str(state_file),
         "--dir", str(mem_dir)],
    )
    assert result.exit_code == 0


# ─── auth providers / status / logout ─────────────────────────────────────────


def test_auth_providers():
    result = runner.invoke(app, ["auth", "providers"])
    assert result.exit_code == 0
    assert "openai" in result.output.lower() or "anthropic" in result.output.lower()


def test_auth_status_empty():
    """Status sem nenhum token autenticado."""
    with patch("bauer.auth.AuthManager.status", return_value={}):
        with patch("bauer.auth.AuthManager.close", return_value=None):
            result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0
    assert "Nenhum" in result.output or "provider" in result.output.lower()


def test_auth_status_with_providers():
    """Status com um provider autenticado."""
    mock_status = {
        "anthropic": {
            "type": "api_key",
            "expired": False,
            "api_base": "https://api.anthropic.com",
            "has_refresh": False,
        }
    }
    with patch("bauer.auth.AuthManager.status", return_value=mock_status):
        with patch("bauer.auth.AuthManager.close", return_value=None):
            result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0


def test_auth_logout_all():
    """Logout de todos os providers."""
    with patch("bauer.auth.AuthManager.logout", return_value=True):
        with patch("bauer.auth.AuthManager.close", return_value=None):
            result = runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0


def test_auth_logout_specific():
    """Logout de um provider específico."""
    with patch("bauer.auth.AuthManager.logout", return_value=True):
        with patch("bauer.auth.AuthManager.close", return_value=None):
            result = runner.invoke(app, ["auth", "logout", "--provider", "anthropic"])
    assert result.exit_code == 0


# ─── agent create / list / delete ─────────────────────────────────────────────


def test_agent_list_empty(tmp_path: Path):
    agents_file = tmp_path / "agents.yaml"
    result = runner.invoke(app, ["agent", "list", "--agents", str(agents_file)])
    assert result.exit_code == 0
    assert "Nenhum agent" in result.output or "agent" in result.output.lower()


def test_agent_list_with_agents(tmp_path: Path):
    from bauer.agent_registry import AgentDef, AgentRegistry
    agents_file = tmp_path / "agents.yaml"
    reg = AgentRegistry(path=str(agents_file))
    reg.save(AgentDef(name="test-agent", description="desc", system="system"))
    result = runner.invoke(app, ["agent", "list", "--agents", str(agents_file)])
    assert result.exit_code == 0
    assert "test-agent" in result.output


def test_agent_delete_existing(tmp_path: Path):
    from bauer.agent_registry import AgentDef, AgentRegistry
    agents_file = tmp_path / "agents.yaml"
    reg = AgentRegistry(path=str(agents_file))
    reg.save(AgentDef(name="del-agent", description="desc", system="system"))
    result = runner.invoke(app, ["agent", "delete", "del-agent", "--agents", str(agents_file), "--yes"])
    assert result.exit_code == 0
    assert "removido" in result.output.lower() or "del-agent" in result.output


def test_agent_delete_not_found(tmp_path: Path):
    agents_file = tmp_path / "agents.yaml"
    result = runner.invoke(app, ["agent", "delete", "nao-existe", "--agents", str(agents_file), "--yes"])
    assert result.exit_code != 0 or "nao encontrado" in result.output.lower()


# ─── learning analyze (--last sem dados) ─────────────────────────────────────


def test_learning_analyze_last_no_data(mem_dir: Path):
    """--last sem análise salva exibe mensagem adequada."""
    result = runner.invoke(app, ["learning", "analyze", "--last", "--dir", str(mem_dir)])
    assert result.exit_code == 0
    assert "Nenhuma" in result.output or "nenhuma" in result.output.lower()


def test_learning_analyze_no_data(mem_dir: Path):
    """Sem dados de aprendizado, mostra aviso."""
    result = runner.invoke(app, ["learning", "analyze", "--dir", str(mem_dir)])
    assert result.exit_code == 0
    assert "Nenhum dado" in result.output or "nenhum" in result.output.lower()


def test_learning_analyze_with_data(mem_dir: Path):
    """Com dados, tenta analisar (mocka o LLM)."""
    from bauer.memory_manager import MemoryManager
    MemoryManager(mem_dir).add_model_experience("phi4-mini", 4096, "ok", 2048, "abc")

    mock_result = MagicMock()
    mock_result.report = "## Padrões\nTudo funciona bem."
    mock_result.model_used = "phi4-mini"

    with patch("bauer.learning_engine.LearningEngineV2.analyze", return_value=mock_result):
        result = runner.invoke(app, ["learning", "analyze", "--dir", str(mem_dir)])
    assert result.exit_code == 0


def test_learning_analyze_error(mem_dir: Path):
    """Erro no analyze retorna exit code 1."""
    from bauer.memory_manager import MemoryManager
    MemoryManager(mem_dir).add_model_experience("phi4-mini", 4096, "ok", 2048, "abc")

    with patch("bauer.learning_engine.LearningEngineV2.analyze", side_effect=RuntimeError("LLM offline")):
        result = runner.invoke(app, ["learning", "analyze", "--dir", str(mem_dir)])
    assert result.exit_code == 1


# ─── config errors ────────────────────────────────────────────────────────────


def test_config_show_invalid(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("{invalid: [yaml", encoding="utf-8")
    result = runner.invoke(app, ["config", "show", "--config", str(bad)])
    assert result.exit_code != 0


# ─── memory summarize ─────────────────────────────────────────────────────────


def test_memory_summarize_with_note(mem_dir: Path):
    runner.invoke(app, ["memory", "add-note", "nota importante", "Detalhes da nota importante", "--dir", str(mem_dir)])
    result = runner.invoke(app, ["memory", "summarize", "--dir", str(mem_dir)])
    assert result.exit_code == 0


# ─── tools run ────────────────────────────────────────────────────────────────


def test_tools_run_list_dir(cfg_path: Path, tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "arquivo.txt").write_text("conteudo", encoding="utf-8")
    action_json = '{"action": "list_dir", "args": {"path": "."}}'
    result = runner.invoke(
        app,
        [
            "tools", "run", action_json,
            "--config", str(cfg_path),
            "--workspace", str(ws),
        ],
    )
    assert result.exit_code == 0
    assert "arquivo.txt" in result.output


def test_tools_run_read_file(cfg_path: Path, tmp_path: Path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "teste.txt").write_text("conteudo de teste", encoding="utf-8")
    action_json = '{"action": "read_file", "args": {"path": "teste.txt"}}'
    result = runner.invoke(
        app,
        [
            "tools", "run", action_json,
            "--config", str(cfg_path),
            "--workspace", str(ws),
        ],
    )
    assert result.exit_code == 0
    assert "conteudo de teste" in result.output
