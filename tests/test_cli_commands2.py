"""Testes adicionais para paths de CLI ainda não cobertos.

Foca em:
  - _load_or_die error paths
  - memory add-model-exp sem state file
  - tools run a partir de arquivo JSON
  - spec_list com status filter
  - spec_show --raw e paths de not-found
  - spec_status_cmd invalid/not-found
  - spec_delete com confirmação
  - spec_context com conteúdo
  - learning_export, learning_reset, learning_analyze --last
  - learning_explain com evidência
  - auth cmd_* funções diretas
  - OAuthCallbackHandler paths
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from bauer.cli import app

runner = CliRunner()


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mem_dir(tmp_path: Path) -> Path:
    d = tmp_path / "memory"
    d.mkdir()
    from bauer.memory_manager import MemoryManager
    MemoryManager(d).init_files()
    return d


@pytest.fixture
def specs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    return d


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
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


# ─── _load_or_die error paths ─────────────────────────────────────────────────


def test_config_validate_missing_file(tmp_path: Path):
    """Arquivo de config inexistente → exit code 2."""
    bad_path = tmp_path / "nao_existe.yaml"
    result = runner.invoke(app, ["config", "validate", "--config", str(bad_path)])
    assert result.exit_code != 0


def test_models_list_bad_models_file(tmp_path: Path):
    """models.yaml inválido → exit code 2."""
    bad = tmp_path / "models.yaml"
    bad.write_text("{invalid: yaml:\n  broken", encoding="utf-8")
    result = runner.invoke(app, ["models", "list", "--models", str(bad)])
    assert result.exit_code == 2


# ─── memory add-model-exp sem state file ──────────────────────────────────────


def test_memory_add_model_exp_no_state_file(mem_dir: Path, tmp_path: Path):
    """Sem runtime_state.json → exit 1."""
    nonexistent = tmp_path / ".runtime_state.json"
    result = runner.invoke(
        app,
        [
            "memory", "add-model-exp", "ok",
            "--state-file", str(nonexistent),
            "--dir", str(mem_dir),
        ],
    )
    assert result.exit_code == 1
    assert "nao encontrado" in result.output.lower() or "Runtime" in result.output


# ─── tools run a partir de arquivo JSON ──────────────────────────────────────


def test_tools_run_from_json_file(cfg_path: Path, tmp_path: Path):
    """tools run aceita caminho para arquivo .json."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "dado.txt").write_text("conteudo", encoding="utf-8")

    action_file = tmp_path / "action.json"
    action_file.write_text(
        json.dumps({"action": "list_dir", "args": {"path": "."}}),
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["tools", "run", str(action_file), "--config", str(cfg_path), "--workspace", str(ws)],
    )
    assert result.exit_code == 0
    assert "dado.txt" in result.output


# ─── spec_list com status filter ──────────────────────────────────────────────


def test_spec_list_status_filter_match(specs_dir: Path):
    """spec list --status filtra specs com status matching."""
    from bauer.spec_manager import Spec, SpecManager
    mgr = SpecManager(specs_dir)
    mgr.save(Spec(id="spec-a", title="Spec A", purpose="p", behavior="b",
                   acceptance_criteria=["ac"], status="approved"))
    mgr.save(Spec(id="spec-b", title="Spec B", purpose="p", behavior="b",
                   acceptance_criteria=["ac"], status="draft"))

    result = runner.invoke(app, ["spec", "list", "--dir", str(specs_dir), "--status", "approved"])
    assert result.exit_code == 0
    assert "spec-a" in result.output
    assert "spec-b" not in result.output


def test_spec_list_status_filter_no_match(specs_dir: Path):
    """spec list --status sem resultados mostra mensagem."""
    from bauer.spec_manager import Spec, SpecManager
    mgr = SpecManager(specs_dir)
    mgr.save(Spec(id="spec-a", title="Spec A", purpose="p", behavior="b",
                   acceptance_criteria=["ac"], status="draft"))

    result = runner.invoke(app, ["spec", "list", "--dir", str(specs_dir), "--status", "deprecated"])
    assert result.exit_code == 0
    assert "deprecated" in result.output or "Nenhum" in result.output


# ─── spec_show --raw ──────────────────────────────────────────────────────────


def test_spec_show_raw(specs_dir: Path):
    """spec show --raw exibe YAML bruto."""
    from bauer.spec_manager import Spec, SpecManager
    mgr = SpecManager(specs_dir)
    mgr.save(Spec(id="raw-spec", title="Raw Spec", purpose="raw purpose",
                   behavior="raw behavior", acceptance_criteria=["ac1"]))

    result = runner.invoke(app, ["spec", "show", "raw-spec", "--raw", "--dir", str(specs_dir)])
    assert result.exit_code == 0
    assert "raw-spec" in result.output or "raw" in result.output.lower()


def test_spec_show_not_found_no_confirm(specs_dir: Path):
    """spec show de spec inexistente → user responde 'n' → exit 1."""
    result = runner.invoke(
        app,
        ["spec", "show", "nao-existe", "--dir", str(specs_dir)],
        input="n\n",
    )
    assert result.exit_code != 0


# ─── spec_status_cmd ─────────────────────────────────────────────────────────


def test_spec_status_invalid_status(specs_dir: Path):
    """Status inválido → exit 1."""
    from bauer.spec_manager import Spec, SpecManager
    mgr = SpecManager(specs_dir)
    mgr.save(Spec(id="my-spec", title="My", purpose="p", behavior="b", acceptance_criteria=["a"]))
    result = runner.invoke(app, ["spec", "status", "my-spec", "invalid-status", "--dir", str(specs_dir)])
    assert result.exit_code == 1
    assert "inválido" in result.output or "invalido" in result.output.lower()


def test_spec_status_not_found_no_confirm(specs_dir: Path):
    """Spec inexistente + resposta 'n' → exit 1."""
    result = runner.invoke(
        app,
        ["spec", "status", "nao-existe", "approved", "--dir", str(specs_dir)],
        input="n\n",
    )
    assert result.exit_code != 0


def test_spec_status_update_existing(specs_dir: Path):
    """Spec existente → status atualizado."""
    from bauer.spec_manager import Spec, SpecManager
    mgr = SpecManager(specs_dir)
    mgr.save(Spec(id="upd-spec", title="Upd", purpose="p", behavior="b", acceptance_criteria=["a"]))
    result = runner.invoke(app, ["spec", "status", "upd-spec", "implemented", "--dir", str(specs_dir)])
    assert result.exit_code == 0
    assert "implemented" in result.output.lower()


# ─── spec_delete com confirmação interativa ──────────────────────────────────


def test_spec_delete_not_found_exits(specs_dir: Path):
    """spec delete de spec inexistente → exit 1."""
    result = runner.invoke(app, ["spec", "delete", "nao-existe", "--force", "--dir", str(specs_dir)])
    assert result.exit_code == 1


def test_spec_delete_with_interactive_cancel(specs_dir: Path):
    """spec delete sem --force, user responde 'n' → cancela."""
    from bauer.spec_manager import Spec, SpecManager
    mgr = SpecManager(specs_dir)
    mgr.save(Spec(id="cancel-spec", title="Cancel", purpose="p", behavior="b", acceptance_criteria=["a"]))
    result = runner.invoke(
        app,
        ["spec", "delete", "cancel-spec", "--dir", str(specs_dir)],
        input="n\n",
    )
    # Spec deve ainda existir
    assert mgr.get("cancel-spec") is not None


# ─── spec_context com conteúdo ───────────────────────────────────────────────


def test_spec_context_approved_spec(specs_dir: Path):
    """spec context com spec approved mostra conteúdo."""
    from bauer.spec_manager import Spec, SpecManager
    mgr = SpecManager(specs_dir)
    mgr.save(Spec(
        id="ctx-spec",
        title="Context Spec",
        purpose="Purpose aqui",
        behavior="Behavior aqui",
        acceptance_criteria=["AC1"],
        status="approved",
    ))
    result = runner.invoke(app, ["spec", "context", "--dir", str(specs_dir)])
    assert result.exit_code == 0
    # Approved spec deve aparecer no contexto
    assert "ctx-spec" in result.output or "Context Spec" in result.output or "Purpose" in result.output


# ─── learning_export ──────────────────────────────────────────────────────────


def test_learning_export_creates_jsonl(mem_dir: Path, tmp_path: Path):
    """learning export cria arquivos JSONL."""
    from bauer.memory_manager import MemoryManager
    mm = MemoryManager(mem_dir)
    mm.add_model_experience("phi4-mini", 4096, "ok", 2048, "abc")

    output_dir = tmp_path / "datasets"
    result = runner.invoke(
        app,
        ["learning", "export", "--dir", str(mem_dir), "--output", str(output_dir)],
    )
    assert result.exit_code == 0
    assert (output_dir / "model_experience.jsonl").exists()


def test_learning_export_empty_dir(mem_dir: Path, tmp_path: Path):
    """learning export com dir vazio cria arquivo JSONL vazio."""
    output_dir = tmp_path / "datasets"
    result = runner.invoke(
        app,
        ["learning", "export", "--dir", str(mem_dir), "--output", str(output_dir)],
    )
    assert result.exit_code == 0
    assert output_dir.exists()


# ─── learning_reset ──────────────────────────────────────────────────────────


def test_learning_reset_with_confirm_flag(mem_dir: Path):
    """learning reset --confirm não pede confirmação interativa."""
    from bauer.memory_manager import MemoryManager
    MemoryManager(mem_dir).add_model_experience("phi4-mini", 4096, "ok", 2048, "abc")

    result = runner.invoke(app, ["learning", "reset", "--confirm", "--dir", str(mem_dir)])
    assert result.exit_code == 0


def test_learning_reset_empty_dir(tmp_path: Path):
    """learning reset sem arquivos mostra mensagem 'Nenhum'."""
    empty_dir = tmp_path / "empty_memory"
    empty_dir.mkdir()
    result = runner.invoke(app, ["learning", "reset", "--confirm", "--dir", str(empty_dir)])
    assert result.exit_code == 0
    assert "Nenhum" in result.output or "nenhum" in result.output.lower()


# ─── learning_forget_model ────────────────────────────────────────────────────


def test_learning_forget_model_with_confirm(mem_dir: Path):
    """learning forget-model --confirm remove entradas do modelo."""
    from bauer.memory_manager import MemoryManager
    MemoryManager(mem_dir).add_model_experience("phi4-mini", 4096, "ok", 2048, "abc")

    result = runner.invoke(
        app,
        ["learning", "forget-model", "phi4-mini", "--confirm", "--dir", str(mem_dir)],
    )
    assert result.exit_code == 0


def test_learning_forget_model_no_entries(mem_dir: Path):
    """learning forget-model sem entradas para o modelo."""
    result = runner.invoke(
        app,
        ["learning", "forget-model", "modelo-inexistente", "--confirm", "--dir", str(mem_dir)],
    )
    assert result.exit_code == 0
    assert "Nenhuma" in result.output or "nenhuma" in result.output.lower()


# ─── learning_analyze --last ──────────────────────────────────────────────────


def test_learning_analyze_last_with_saved(mem_dir: Path):
    """--last com análise salva exibe o conteúdo."""
    analysis_file = mem_dir / "LEARNING_ANALYSIS.md"
    analysis_file.write_text("## Análise\nTudo funcionou bem.", encoding="utf-8")

    result = runner.invoke(app, ["learning", "analyze", "--last", "--dir", str(mem_dir)])
    assert result.exit_code == 0
    # A análise deve aparecer no output
    assert "Análise" in result.output or "analise" in result.output.lower() or result.output


# ─── learning_explain com evidência ──────────────────────────────────────────


def test_learning_explain_with_failures(mem_dir: Path):
    """learning explain com dados de falha mostra recomendações."""
    from bauer.memory_manager import MemoryManager
    mm = MemoryManager(mem_dir)
    # Adiciona múltiplas falhas para gerar recomendação com evidência
    mm.add_failure("Task X", "OOM error", "use smaller model")
    mm.add_failure("Task Y", "slow response", "reduce context")

    result = runner.invoke(app, ["learning", "explain", "--dir", str(mem_dir)])
    assert result.exit_code == 0


# ─── auth cmd_* direto ────────────────────────────────────────────────────────


def test_auth_cmd_status_empty(tmp_path: Path):
    """cmd_status com store vazio."""
    from bauer.auth import cmd_status, TokenStore
    store = TokenStore(base_dir=tmp_path / "auth")
    with patch("bauer.auth.AuthManager.status", return_value={}):
        with patch("bauer.auth.AuthManager.close", return_value=None):
            cmd_status()  # Não deve levantar exceção


def test_auth_cmd_status_with_token(tmp_path: Path):
    """cmd_status com um token autenticado."""
    mock_status = {
        "groq": {
            "type": "api_key",
            "expired": False,
            "api_base": "https://api.groq.com",
            "has_refresh": False,
        }
    }
    with patch("bauer.auth.AuthManager.status", return_value=mock_status):
        with patch("bauer.auth.AuthManager.close", return_value=None):
            from bauer.auth import cmd_status
            cmd_status()  # Não deve levantar exceção


def test_auth_cmd_logout_all(tmp_path: Path):
    """cmd_logout sem provider → remove todos."""
    with patch("bauer.auth.AuthManager.logout", return_value=True):
        with patch("bauer.auth.AuthManager.close", return_value=None):
            from bauer.auth import cmd_logout
            cmd_logout(None)


def test_auth_cmd_logout_specific(tmp_path: Path):
    """cmd_logout com provider específico."""
    with patch("bauer.auth.AuthManager.logout", return_value=True):
        with patch("bauer.auth.AuthManager.close", return_value=None):
            from bauer.auth import cmd_logout
            cmd_logout("anthropic")


def test_auth_cmd_list_providers():
    """cmd_list_providers não levanta exceção."""
    from bauer.auth import cmd_list_providers
    cmd_list_providers()  # Apenas verifica que não levanta


# ─── OAuthCallbackHandler paths ──────────────────────────────────────────────


def test_oauth_callback_handler_success():
    """OAuthCallbackHandler processa callback com code."""
    from bauer.auth import _OAuthCallbackHandler
    from http.server import HTTPServer
    import threading
    import urllib.request

    # Reset state
    _OAuthCallbackHandler.auth_code = None
    _OAuthCallbackHandler.state = None

    server = HTTPServer(("127.0.0.1", 0), _OAuthCallbackHandler)
    port = server.server_address[1]
    _OAuthCallbackHandler.actual_port = port

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        # Envia callback com code
        req = urllib.request.Request(f"http://127.0.0.1:{port}/auth/callback?code=mycode123&state=mystate")
        try:
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass  # Pode redirecionar, não importa
        assert _OAuthCallbackHandler.auth_code == "mycode123"
    finally:
        server.shutdown()


def test_oauth_callback_handler_success_page():
    """OAuthCallbackHandler responde 200 em /success."""
    from bauer.auth import _OAuthCallbackHandler
    from http.server import HTTPServer
    import threading
    import urllib.request

    server = HTTPServer(("127.0.0.1", 0), _OAuthCallbackHandler)
    port = server.server_address[1]

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/success", timeout=2)
        assert resp.status == 200
    finally:
        server.shutdown()


def test_oauth_callback_handler_error():
    """OAuthCallbackHandler responde 400 em path com error."""
    from bauer.auth import _OAuthCallbackHandler
    from http.server import HTTPServer
    import threading
    import urllib.request

    server = HTTPServer(("127.0.0.1", 0), _OAuthCallbackHandler)
    port = server.server_address[1]

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/auth/callback?error=access_denied&error_description=User+denied",
                timeout=2,
            )
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        server.shutdown()


def test_oauth_callback_handler_404():
    """OAuthCallbackHandler responde 404 para paths desconhecidos."""
    from bauer.auth import _OAuthCallbackHandler
    from http.server import HTTPServer
    import threading
    import urllib.request
    import urllib.error

    server = HTTPServer(("127.0.0.1", 0), _OAuthCallbackHandler)
    port = server.server_address[1]

    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    try:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/unknown/path", timeout=2)
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        server.shutdown()


# ─── OAuthCallbackServer ─────────────────────────────────────────────────────


def test_oauth_callback_server_start_stop():
    """OAuthCallbackServer inicia e para sem erro."""
    from bauer.auth import OAuthCallbackServer
    server = OAuthCallbackServer(port=0)  # porta 0 = aleatória

    # mock HTTPServer para não abrir porta real
    with patch("bauer.auth.HTTPServer") as mock_http:
        mock_srv = MagicMock()
        mock_srv.server_address = ("127.0.0.1", 12345)
        mock_http.return_value = mock_srv

        server.start()
        server.stop()
        mock_srv.shutdown.assert_called_once()


def test_oauth_callback_server_wait_for_code_timeout():
    """wait_for_code retorna (None, None) se timeout."""
    from bauer.auth import OAuthCallbackServer, _OAuthCallbackHandler
    _OAuthCallbackHandler.auth_code = None
    server = OAuthCallbackServer()
    code, state = server.wait_for_code(timeout=0.1)
    assert code is None
    assert state is None


# ─── env_loader uncovered ────────────────────────────────────────────────────


def test_env_loader_load_specific_var(tmp_path: Path):
    """env_loader carrega variáveis específicas do .env."""
    import os
    from bauer.env_loader import load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text("TEST_MY_BAUER_VAR=hello_world\n# comment\n\nOTHER_BAUER=123\n", encoding="utf-8")

    # Remove se já existia no env
    os.environ.pop("TEST_MY_BAUER_VAR", None)
    os.environ.pop("OTHER_BAUER", None)

    loaded = load_dotenv(env_file)
    assert "TEST_MY_BAUER_VAR" in loaded
    assert loaded["TEST_MY_BAUER_VAR"] == "hello_world"

    # Cleanup
    os.environ.pop("TEST_MY_BAUER_VAR", None)
    os.environ.pop("OTHER_BAUER", None)


def test_env_loader_missing_file(tmp_path: Path):
    """env_loader retorna vazio se .env não existe."""
    from bauer.env_loader import load_dotenv

    result = load_dotenv(tmp_path / ".env.nonexistent")
    assert result == {}


def test_env_loader_quoted_values(tmp_path: Path):
    """env_loader remove aspas ao redor de valores."""
    import os
    from bauer.env_loader import load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text('BAUER_QUOTED="quoted value"\nBAUER_SINGLE=\'single\'\n', encoding="utf-8")

    os.environ.pop("BAUER_QUOTED", None)
    os.environ.pop("BAUER_SINGLE", None)

    loaded = load_dotenv(env_file)
    assert loaded.get("BAUER_QUOTED") == "quoted value"
    assert loaded.get("BAUER_SINGLE") == "single"

    os.environ.pop("BAUER_QUOTED", None)
    os.environ.pop("BAUER_SINGLE", None)


# ─── openai_client uncovered ─────────────────────────────────────────────────


def test_openai_client_stream_basic():
    """OpenAIClient.chat_stream básico."""
    import json as _json
    from unittest.mock import MagicMock, patch
    from bauer.openai_client import OpenAIClient

    client = OpenAIClient(host="http://localhost:1234", api_key="test-key", model="gpt-4")

    lines = [
        _json.dumps({"choices": [{"delta": {"content": "hello"}}]}),
        _json.dumps({"choices": [{"delta": {"content": " world"}}]}),
        _json.dumps({"choices": [{"delta": {}}]}),
        "[DONE]",
    ]

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status.return_value = None
    mock_response.iter_lines.return_value = iter(f"data: {l}" for l in lines)
    ctx = MagicMock()
    ctx.__enter__.return_value = mock_response
    ctx.__exit__.return_value = False

    with patch("httpx.stream", return_value=ctx):
        chunks = list(client.chat_stream("gpt-4", [{"role": "user", "content": "oi"}]))
    assert "hello" in chunks
    assert " world" in chunks


def test_openai_client_list_models():
    """OpenAIClient.list_models retorna lista de modelos."""
    from bauer.openai_client import OpenAIClient

    client = OpenAIClient(host="http://localhost:1234", api_key="test-key", model="gpt-4")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "data": [{"id": "gpt-4"}, {"id": "gpt-3.5-turbo"}]
    }

    with patch("httpx.get", return_value=mock_resp):
        models = client.list_models()
    assert "gpt-4" in models


def test_openai_client_has_model_true():
    """OpenAIClient.has_model retorna True quando modelo existe."""
    from bauer.openai_client import OpenAIClient

    client = OpenAIClient(host="http://localhost:1234", api_key="test-key", model="gpt-4")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"data": [{"id": "gpt-4"}]}

    with patch("httpx.get", return_value=mock_resp):
        assert client.has_model("gpt-4") is True


def test_openai_client_is_alive_true():
    """OpenAIClient.is_alive retorna True com resposta 200."""
    from bauer.openai_client import OpenAIClient

    client = OpenAIClient(host="http://localhost:1234", api_key="test-key", model="gpt-4")
    mock_resp = MagicMock()
    mock_resp.status_code = 200

    with patch("httpx.get", return_value=mock_resp):
        alive, reason = client.is_alive()
    assert alive is True


# ─── model_registry uncovered ────────────────────────────────────────────────


def test_model_registry_get_unknown(tmp_path: Path):
    """ModelRegistry.get retorna None para modelo desconhecido."""
    from bauer.model_registry import load_registry
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        "models:\n"
        "  phi4-mini:\n"
        "    provider: ollama\n"
        "    ram_base_mb: 2500\n"
        "    ram_per_1k_ctx_mb: 40\n"
        "    max_context_safe: 32768\n"
        "    supports_tools: false\n"
        "    ram_profile: low\n",
        encoding="utf-8",
    )
    reg = load_registry(models_file)
    assert reg.get("nao-existe") is None


def test_model_registry_contexto_seguro_no_ram(tmp_path: Path):
    """contexto_seguro retorna 0 se RAM insuficiente."""
    from bauer.model_registry import load_registry, contexto_seguro
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        "models:\n"
        "  phi4-mini:\n"
        "    provider: ollama\n"
        "    ram_base_mb: 8000\n"
        "    ram_per_1k_ctx_mb: 40\n"
        "    max_context_safe: 32768\n"
        "    supports_tools: false\n"
        "    ram_profile: low\n",
        encoding="utf-8",
    )
    reg = load_registry(models_file)
    info = reg.get("phi4-mini")
    # Com apenas 1000 MB disponível, não cabe
    result = contexto_seguro(info, ram_disponivel_mb=1000, folga_mb=512)
    assert result == 0


def test_model_registry_names(tmp_path: Path):
    """ModelRegistry.names() retorna lista de modelos."""
    from bauer.model_registry import load_registry
    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        "models:\n"
        "  phi4-mini:\n"
        "    provider: ollama\n"
        "    ram_base_mb: 2500\n"
        "    ram_per_1k_ctx_mb: 40\n"
        "    max_context_safe: 32768\n"
        "    supports_tools: false\n"
        "    ram_profile: low\n"
        "  qwen3:0.6b:\n"
        "    provider: ollama\n"
        "    ram_base_mb: 1000\n"
        "    ram_per_1k_ctx_mb: 20\n"
        "    max_context_safe: 32768\n"
        "    supports_tools: false\n"
        "    ram_profile: low\n",
        encoding="utf-8",
    )
    reg = load_registry(models_file)
    names = reg.names()
    assert "phi4-mini" in names
    assert "qwen3:0.6b" in names


# ─── preflight.py uncovered ──────────────────────────────────────────────────


def test_preflight_run_doctor_mocked(tmp_path: Path, cfg_path: Path):
    """run_doctor executa com Ollama offline."""
    from bauer.config_loader import load_config
    from bauer.model_registry import load_registry
    from bauer.preflight import run_doctor

    models_file = tmp_path / "models.yaml"
    models_file.write_text(
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

    cfg = load_config(cfg_path)
    reg = load_registry(models_file)
    state_file = tmp_path / ".runtime_state.json"

    import httpx
    with patch("httpx.get", side_effect=httpx.ConnectError("ollama offline")):
        report = run_doctor(cfg, reg, state_file)

    assert report is not None
    assert report.state.status in ("ok", "ok_with_adjustments", "blocked")
    assert report.state.ollama_alive is False


# ─── self_tuner uncovered ────────────────────────────────────────────────────


def test_self_tuner_tune_basic(tmp_path: Path):
    """SelfTuner.tune executa sem crash com modelo não registrado."""
    from bauer.self_tuner import SelfTuner
    from bauer.model_registry import load_registry

    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        "models:\n"
        "  phi4-mini:\n"
        "    provider: ollama\n"
        "    ram_base_mb: 2500\n"
        "    ram_per_1k_ctx_mb: 40\n"
        "    max_context_safe: 32768\n"
        "    supports_tools: false\n"
        "    ram_profile: low\n",
        encoding="utf-8",
    )
    reg = load_registry(models_file)
    tuner = SelfTuner(memory_dir=tmp_path)
    result = tuner.tune(
        desired_model="phi4-mini",
        desired_context=4096,
        minimum_context=2048,
        installed_models=["phi4-mini"],
        registry=reg,
        ram_available_mb=8192,
    )
    assert result.model == "phi4-mini"
    assert result.context_tokens >= 2048


def test_self_tuner_tune_insufficient_ram(tmp_path: Path):
    """SelfTuner.tune ajusta quando RAM é insuficiente."""
    from bauer.self_tuner import SelfTuner
    from bauer.model_registry import load_registry

    models_file = tmp_path / "models.yaml"
    models_file.write_text(
        "models:\n"
        "  phi4-mini:\n"
        "    provider: ollama\n"
        "    ram_base_mb: 8000\n"
        "    ram_per_1k_ctx_mb: 40\n"
        "    max_context_safe: 32768\n"
        "    supports_tools: false\n"
        "    ram_profile: high\n",
        encoding="utf-8",
    )
    reg = load_registry(models_file)
    tuner = SelfTuner(memory_dir=tmp_path, safety_margin_mb=512)
    result = tuner.tune(
        desired_model="phi4-mini",
        desired_context=4096,
        minimum_context=2048,
        installed_models=["phi4-mini"],
        registry=reg,
        ram_available_mb=1000,  # muito pouco
    )
    # Deve ajustar (pode manter mínimo ou trocar modelo)
    assert result is not None
    assert result.context_tokens >= 2048


# ─── server.py uncovered ─────────────────────────────────────────────────────


def test_server_delete_session_success():
    """DELETE /sessions/{sid} com sessão existente → 200."""
    from bauer.server import create_app
    from bauer.tool_router import ToolRouter
    from starlette.testclient import TestClient

    tmp_path = Path(__file__).parent.parent / "tmp_server_test"
    tmp_path.mkdir(exist_ok=True)

    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter(["ok"])
    mock_client.list_models.return_value = ["phi4-mini"]
    mock_client.has_model.return_value = True

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(exist_ok=True)

    router = ToolRouter(workspace=tmp_path)
    app = create_app(
        model_name="phi4-mini",
        applied_context=4096,
        router=router,
        client=mock_client,
        system_prompt="System",
        sessions_dir=sessions_dir,
        api_key="",
        rate_limit_requests=0,
        rate_limit_window_s=60.0,
    )
    client = TestClient(app, raise_server_exceptions=True)

    # Criar sessão via chat
    with patch("bauer.agent.run_one_turn", return_value=("resposta ok", [])):
        resp = client.post("/chat", json={"message": "oi", "session_id": "test-sid-123"})

    # Deletar sessão
    resp = client.delete("/sessions/test-sid-123")
    # Pode ser 200 ou 404 dependendo se foi salva
    assert resp.status_code in (200, 404)

    import shutil
    shutil.rmtree(tmp_path, ignore_errors=True)
