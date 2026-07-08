"""Testes do loop do agente com Tool Bridge (Fase 6)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.agent import MAX_TOOL_TURNS, _build_system_prompt, _try_parse_tool
from bauer.tool_router import ToolRouter


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello", encoding="utf-8")
    return workspace


@pytest.fixture
def router(ws: Path) -> ToolRouter:
    return ToolRouter(workspace=ws)


# --- _try_parse_tool --------------------------------------------------------


def test_parse_tool_valid_action(router: ToolRouter):
    response = '{"action": "list_dir", "args": {"path": "."}}'
    result = _try_parse_tool(response, router)
    assert result is not None
    assert result["action"] == "list_dir"


def test_parse_tool_returns_none_for_text(router: ToolRouter):
    result = _try_parse_tool("Olá! Como posso ajudar?", router)
    assert result is None


def test_parse_tool_returns_none_for_invalid_json(router: ToolRouter):
    result = _try_parse_tool("isso nao e json", router)
    assert result is None


def test_parse_tool_handles_markdown_block(router: ToolRouter):
    response = '```json\n{"action": "read_file", "args": {"path": "hello.txt"}}\n```'
    result = _try_parse_tool(response, router)
    assert result is not None
    assert result["action"] == "read_file"


def test_parse_tool_returns_none_for_partial_json(router: ToolRouter):
    result = _try_parse_tool('{"sem_action": true}', router)
    assert result is None


def test_parse_tool_ignores_unknown_action(router: ToolRouter):
    """Ação inventada pelo modelo ('responda', 'current_time', etc.) é ignorada."""
    result = _try_parse_tool('{"action": "responda"}', router)
    assert result is None


def test_parse_tool_ignores_unknown_action_with_trailing_text(router: ToolRouter):
    """JSON com action inválida + texto extra → tudo tratado como texto."""
    response = '{"action": "current_time"}\n\nSao 14h30.'
    result = _try_parse_tool(response, router)
    assert result is None


def test_parse_tool_extracts_json_from_mixed_response(router: ToolRouter):
    """JSON de tool válida + texto extra → extrai só a tool action."""
    response = '{"action": "list_dir", "args": {"path": "."}}\n\nVou listar os arquivos.'
    result = _try_parse_tool(response, router)
    assert result is not None
    assert result["action"] == "list_dir"


# --- _build_system_prompt ---------------------------------------------------


def test_system_prompt_lists_tools(router: ToolRouter):
    prompt = _build_system_prompt(router)
    assert "list_dir" in prompt
    assert "read_file" in prompt
    assert "write_file" in prompt
    assert "search_text" in prompt


def test_system_prompt_has_json_format(router: ToolRouter):
    prompt = _build_system_prompt(router)
    assert '"action"' in prompt
    assert '"args"' in prompt


def test_system_prompt_mentions_portugues(router: ToolRouter):
    prompt = _build_system_prompt(router)
    assert "portugues" in prompt.lower() or "português" in prompt.lower()


# --- run_agent_session (integração) -----------------------------------------


# --- comandos slash de workspace --------------------------------------------


def test_task_command_uses_active_workspace(tmp_path: Path):
    from bauer.agent import _handle_task_cmd
    from rich.console import Console

    active_ws = tmp_path / "company-a" / "workspace"
    global_ws = tmp_path / "workspace"
    console = Console(record=True, width=120)

    _handle_task_cmd("/task add Custom workspace task", console, active_ws)

    assert (active_ws / "TASKS.md").exists()
    assert "Custom workspace task" in (active_ws / "TASKS.md").read_text(encoding="utf-8")
    assert not (global_ws / "TASKS.md").exists()


def test_project_command_uses_active_workspace(tmp_path: Path):
    from bauer.agent import _handle_project_cmd
    from bauer.workspace_manager import WorkspaceManager
    from rich.console import Console

    active_ws = tmp_path / "company-a" / "workspace"
    WorkspaceManager(active_ws).init_project("Active Project", "From active workspace")
    console = Console(record=True, width=120)

    _handle_project_cmd(console, active_ws)

    output = console.export_text()
    assert "Active Project" in output
    assert "From active workspace" in output


def test_run_agent_session_task_command_uses_router_workspace(tmp_path: Path):
    from bauer.agent import run_agent_session
    from rich.console import Console

    active_ws = tmp_path / "company-a" / "workspace"
    router = ToolRouter(workspace=active_ws)
    client = _make_client()
    console = Console()

    with patch("builtins.input", side_effect=["/task add Via chat", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    assert (active_ws / "TASKS.md").exists()
    assert "Via chat" in (active_ws / "TASKS.md").read_text(encoding="utf-8")
    client.chat_stream.assert_not_called()


# ─── config.tools.max_tool_turns (MAX_TOOL_TURNS configurável) ─────────────
# Regressão: "Limite de 150 tool calls atingido neste turno" era hardcoded,
# sem NENHUM jeito de aumentar via config.yaml.


def test_tools_section_max_tool_turns_default_150():
    from bauer.config_loader import ToolsSection

    assert ToolsSection().max_tool_turns == 150


def test_resolve_max_tool_turns_reads_config():
    from bauer.agent import _resolve_max_tool_turns
    from bauer.config_loader import BauerConfig, ModelSection, ToolsSection

    cfg = BauerConfig(
        model=ModelSection(provider="ollama", name="x"),
        tools=ToolsSection(max_tool_turns=9999),
    )
    with patch("bauer.config_loader.load_config", return_value=cfg):
        assert _resolve_max_tool_turns() == 9999


def test_resolve_max_tool_turns_defaults_150_on_config_load_failure():
    from bauer.agent import _resolve_max_tool_turns

    with patch("bauer.config_loader.load_config", side_effect=FileNotFoundError("no config")):
        assert _resolve_max_tool_turns() == 150


def test_run_agent_session_applies_configured_max_tool_turns(ws: Path, router: ToolRouter):
    """run_agent_session muta o global MAX_TOOL_TURNS a partir da config antes
    de entrar no loop — cobre todos os call sites internos que leem o nome do
    módulo (_run_tool_loop_body, _native_turn_interactive etc)."""
    import bauer.agent as agent_mod
    from bauer.config_loader import BauerConfig, ModelSection, ToolsSection
    from rich.console import Console

    cfg = BauerConfig(
        model=ModelSection(provider="ollama", name="x"),
        tools=ToolsSection(max_tool_turns=9999),
    )
    client = _make_client()
    console = Console()

    try:
        with patch("bauer.config_loader.load_config", return_value=cfg), \
             patch("builtins.input", side_effect=[EOFError]):
            agent_mod.run_agent_session(client, "test-model", 4096, console, router)
        assert agent_mod.MAX_TOOL_TURNS == 9999
    finally:
        # Nunca deixa o global mutado vazar pra outros testes do mesmo processo.
        agent_mod.MAX_TOOL_TURNS = 150


def test_run_agent_session_falls_back_to_150_without_config(ws: Path, router: ToolRouter):
    import bauer.agent as agent_mod
    from rich.console import Console

    client = _make_client()
    console = Console()

    try:
        agent_mod.MAX_TOOL_TURNS = 9999  # simula valor deixado por outro teste/sessão
        with patch("bauer.config_loader.load_config", side_effect=FileNotFoundError), \
             patch("builtins.input", side_effect=[EOFError]):
            agent_mod.run_agent_session(client, "test-model", 4096, console, router)
        assert agent_mod.MAX_TOOL_TURNS == 150
    finally:
        agent_mod.MAX_TOOL_TURNS = 150


def test_dispatch_command_dry_run_uses_active_workspace(tmp_path: Path):
    from bauer.agent import _handle_dispatch_cmd
    from bauer.workspace_manager import WorkspaceManager
    from rich.console import Console

    active_ws = tmp_path / "company-a" / "workspace"
    global_ws = tmp_path / "workspace"
    wm = WorkspaceManager(active_ws)
    wm.init_project("Active Project")
    wm.add_task("Queued task", status="READY", metadata={"dispatch": "true"})
    console = Console(record=True, width=120)

    _handle_dispatch_cmd("/dispatch once --dry-run", console, active_ws)

    output = console.export_text()
    assert "dry: T0001" in output
    assert wm.get_task("001").status == "READY"
    assert not (global_ws / "TASKS.md").exists()


def test_run_agent_session_dispatch_status_uses_router_workspace(tmp_path: Path):
    from bauer.agent import run_agent_session
    from bauer.workspace_manager import WorkspaceManager
    from rich.console import Console

    active_ws = tmp_path / "company-a" / "workspace"
    WorkspaceManager(active_ws).init_project("Active Project")
    router = ToolRouter(workspace=active_ws)
    client = _make_client()
    console = Console()

    with patch("builtins.input", side_effect=["/dispatch status", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    client.chat_stream.assert_not_called()


def _make_client(*responses: str) -> MagicMock:
    """Cria mock de OllamaClient que retorna respostas em sequência."""
    client = MagicMock()
    response_iter = iter(responses)

    def chat_stream_side_effect(*args, **kwargs):
        return iter([next(response_iter)])

    client.chat_stream.side_effect = chat_stream_side_effect
    return client


def test_agent_text_response_no_tool(ws: Path, router: ToolRouter):
    """Resposta de texto puro — sem tool call."""
    from bauer.agent import run_agent_session
    from rich.console import Console

    client = _make_client("Olá! Como posso ajudar?")
    console = Console()

    with patch("builtins.input", side_effect=["oi", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    assert client.chat_stream.call_count == 1


def test_agent_listen_command_sends_transcript_to_model(ws: Path, router: ToolRouter):
    """Comando /listen captura voz e usa a transcricao como turno do usuario."""
    from bauer.agent import run_agent_session
    from rich.console import Console

    client = _make_client("Resposta por texto.")
    console = Console()

    with patch("bauer.agent._capture_listen_input", return_value="resuma o projeto"):
        with patch("builtins.input", side_effect=["/listen", EOFError]):
            run_agent_session(client, "test-model", 4096, console, router)

    assert client.chat_stream.call_count == 1
    first_call_payload = client.chat_stream.call_args_list[0][0][1]
    contents = [m["content"] for m in first_call_payload]
    assert "resuma o projeto" in contents


def test_agent_listen_ignores_punctuation_only_transcript(ws: Path, router: ToolRouter):
    """Transcricao espuria como '.' nao deve virar turno do modelo."""
    from bauer.agent import run_agent_session
    from rich.console import Console

    client = _make_client("nao deve chamar")
    console = Console()

    with patch("bauer.agent._capture_listen_input", return_value=None):
        with patch("builtins.input", side_effect=["/listen", "/exit"]):
            run_agent_session(client, "test-model", 4096, console, router)

    client.chat_stream.assert_not_called()


def test_agent_listen_loop_keeps_listening_until_stop(ws: Path, router: ToolRouter):
    """Modo /listen loop volta a escutar depois da resposta ate comando de parada."""
    from bauer.agent import run_agent_session
    from rich.console import Console

    client = _make_client("Primeira resposta.")
    console = Console()

    with patch("bauer.agent._capture_listen_input", side_effect=["primeira pergunta", "parar"]):
        with patch("builtins.input", side_effect=["/listen loop", EOFError]):
            run_agent_session(client, "test-model", 4096, console, router)

    assert client.chat_stream.call_count == 1
    first_payload = client.chat_stream.call_args_list[0][0][1]
    contents = [m["content"] for m in first_payload]
    assert "primeira pergunta" in contents


def test_capture_listen_input_handles_keyboard_interrupt():
    """Ctrl+C durante audio deve cancelar o /listen e devolver o prompt."""
    from bauer.agent import _capture_listen_input
    from rich.console import Console

    with patch("bauer.audio_capture.capture_voice_input", side_effect=KeyboardInterrupt):
        assert _capture_listen_input(Console()) is None


def test_capture_listen_input_rejects_noise_transcript():
    from bauer.agent import _capture_listen_input
    from rich.console import Console

    with patch("bauer.audio_capture.capture_voice_input", return_value="."):
        assert _capture_listen_input(Console()) is None


def test_agent_single_tool_call(ws: Path, router: ToolRouter):
    """Modelo chama uma tool e depois responde com texto."""
    from bauer.agent import run_agent_session
    from rich.console import Console

    tool_action = '{"action": "list_dir", "args": {"path": "."}}'
    final_response = "Os arquivos são: hello.txt"
    client = _make_client(tool_action, final_response)
    console = Console()

    with patch("builtins.input", side_effect=["liste os arquivos", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    assert client.chat_stream.call_count == 2


def test_agent_tool_result_fed_back(ws: Path, router: ToolRouter):
    """Resultado da tool é adicionado ao contexto antes da próxima chamada."""
    from bauer.agent import run_agent_session
    from rich.console import Console

    tool_action = '{"action": "list_dir", "args": {"path": "."}}'
    final_response = "Vi os arquivos: hello.txt"
    client = _make_client(tool_action, final_response)
    console = Console()

    with patch("builtins.input", side_effect=["liste", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    # Segunda chamada deve incluir o resultado da tool no payload
    second_call_payload = client.chat_stream.call_args_list[1][0][1]
    contents = [m["content"] for m in second_call_payload]
    assert any("Resultado de list_dir" in c for c in contents)


def test_agent_max_tool_turns_protection(ws: Path, router: ToolRouter):
    """Agente para após MAX_TOOL_TURNS tool calls consecutivos.

    Contexto GRANDE (131072) de propósito: com 4096, a compressão de contexto
    dispara a cada ~13 rodadas e o fallback dela ("auxiliary primeiro,
    principal depois") consome respostas do MOCK como sumarizador — antes da
    hermeticidade do conftest.py isso silenciosamente fazia ~10 chamadas de
    LLM REAIS por execução deste teste. O teste é sobre o teto de tool calls,
    não sobre compressão — contexto grande isola o que se quer medir.
    """
    from bauer.agent import run_agent_session
    from rich.console import Console

    # Modelo fica chamando list_dir infinitamente
    tool_action = '{"action": "list_dir", "args": {"path": "."}}'
    # Resposta final após MAX+1 chamadas
    responses = [tool_action] * (MAX_TOOL_TURNS + 1)
    client = _make_client(*responses)
    console = Console()

    with patch("builtins.input", side_effect=["faça algo", EOFError]):
        run_agent_session(client, "test-model", 131072, console, router)

    # Não deve ter mais de MAX_TOOL_TURNS + 1 chamadas (MAX tool calls + 1 final)
    assert client.chat_stream.call_count <= MAX_TOOL_TURNS + 1


def test_agent_tool_error_does_not_crash(ws: Path, router: ToolRouter):
    """Erro na tool é capturado e enviado ao modelo — não crasha o agente."""
    from bauer.agent import run_agent_session
    from rich.console import Console

    # Tenta ler arquivo inexistente
    tool_action = '{"action": "read_file", "args": {"path": "nao_existe.txt"}}'
    final_response = "Arquivo nao encontrado, desculpe."
    client = _make_client(tool_action, final_response)
    console = Console()

    with patch("builtins.input", side_effect=["leia nao_existe.txt", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    # Deve ter feito 2 chamadas (tool + resposta) sem cravar
    assert client.chat_stream.call_count == 2


def test_agent_exit_command(ws: Path, router: ToolRouter):
    """Comando /exit encerra o loop sem chamar o modelo."""
    from bauer.agent import run_agent_session
    from rich.console import Console

    client = _make_client()
    console = Console()

    with patch("builtins.input", side_effect=["/exit"]):
        run_agent_session(client, "test-model", 4096, console, router)

    client.chat_stream.assert_not_called()


def test_agent_clear_command(ws: Path, router: ToolRouter):
    """Comando /clear limpa o contexto sem chamar o modelo."""
    from bauer.agent import run_agent_session
    from rich.console import Console

    client = _make_client("Olá!")
    console = Console()

    with patch("builtins.input", side_effect=["/clear", "oi", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    assert client.chat_stream.call_count == 1


def test_agent_model_command(ws: Path, router: ToolRouter, capsys):
    """Comando /model abre seletor de modelo sem chamar o LLM."""
    from bauer.agent import run_agent_session
    from rich.console import Console

    client = _make_client()
    console = Console()

    # Mocka o seletor interativo para não consumir stdin no CI
    with patch("bauer.model_switcher.run_model_switcher"), \
         patch("builtins.input", side_effect=["/model", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    client.chat_stream.assert_not_called()


def test_agent_modelo_alias(ws: Path, router: ToolRouter):
    """/modelo e alias de /model — nao chama o LLM."""
    from bauer.agent import run_agent_session
    from rich.console import Console

    client = _make_client()
    console = Console()

    with patch("bauer.model_switcher.run_model_switcher"), \
         patch("builtins.input", side_effect=["/modelo", EOFError]):
        run_agent_session(client, "test-model", 4096, console, router)

    client.chat_stream.assert_not_called()
