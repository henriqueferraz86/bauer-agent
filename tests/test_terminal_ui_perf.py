"""Testes das correções de UX/performance do terminal (bauer agent).

Cobre:
  - AuthManager: httpx.Client lazy (criava SSL context ~260ms × 62 fallbacks
    no startup — 17s medidos antes do fix)
  - OllamaClient.is_alive: probe com timeout curto (não o timeout de chat)
  - _print_assistant_response: render Markdown + fallback texto puro
  - _thinking_status/_busy_spinner: nunca quebram o turno (best-effort);
    spinner cobre tanto a chamada ao LLM quanto a execução de tools (nativo
    e bridge) — sem isso um run_command demorado ficava sem nenhum
    indicador visível.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


# ─── AuthManager lazy http ────────────────────────────────────────────────────


def test_auth_manager_init_does_not_create_http_client(tmp_path):
    from bauer.auth import AuthManager

    auth = AuthManager(base_dir=tmp_path)
    assert auth._http_client is None  # nada de httpx.Client no __init__


def test_auth_manager_http_created_on_first_use_and_close_safe(tmp_path):
    from bauer.auth import AuthManager

    auth = AuthManager(base_dir=tmp_path)
    auth.close()  # fechar sem nunca ter usado não cria nem quebra
    assert auth._http_client is None

    client = auth._http  # primeiro acesso cria
    assert auth._http_client is client
    assert auth._http is client  # acessos seguintes reusam
    auth.close()


# ─── OllamaClient.is_alive: probe curto ──────────────────────────────────────


def test_is_alive_probe_uses_short_timeout():
    """Liveness não pode esperar o timeout de chat (30-300s): Ollama saudável
    responde /api/tags em ms; caído/via firewall segurava o startup."""
    from bauer.ollama_client import OllamaClient

    c = OllamaClient("http://localhost:11434", timeout_seconds=300)
    with patch("bauer.ollama_client.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        alive, reason = c.is_alive()

    assert alive is True
    assert mock_get.call_args.kwargs["timeout"] <= 2.0


def test_is_alive_probe_respects_smaller_configured_timeout():
    from bauer.ollama_client import OllamaClient

    c = OllamaClient("http://localhost:11434", timeout_seconds=1)
    with patch("bauer.ollama_client.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        c.is_alive()

    assert mock_get.call_args.kwargs["timeout"] == 1.0


# ─── _print_assistant_response ────────────────────────────────────────────────


def _capture_console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def test_print_assistant_response_renders_markdown():
    from bauer.agent import _print_assistant_response

    console = _capture_console()
    _print_assistant_response(console, "resposta com **negrito** e `codigo`")
    out = console.file.getvalue()

    assert "bauer" in out
    assert "negrito" in out
    assert "**" not in out  # markdown foi renderizado, não impresso cru


def test_print_assistant_response_includes_cost_line():
    from bauer.agent import _print_assistant_response

    console = _capture_console()
    _print_assistant_response(console, "ok", cost_line="[dim]custo x[/dim]")
    assert "custo x" in console.file.getvalue()


def test_print_assistant_response_falls_back_to_plain_text():
    from bauer.agent import _print_assistant_response

    console = _capture_console()
    with patch("rich.markdown.Markdown", side_effect=RuntimeError("boom")):
        _print_assistant_response(console, "texto simples")
    assert "texto simples" in console.file.getvalue()


# ─── _thinking_status ─────────────────────────────────────────────────────────


def test_thinking_status_yields_even_if_console_status_fails():
    from bauer.agent import _thinking_status

    console = MagicMock()
    console.status.side_effect = RuntimeError("live display já ativo")
    ran = False
    with _thinking_status(console, "modelo-x"):
        ran = True
    assert ran


def test_thinking_status_enters_and_exits_console_status():
    from bauer.agent import _thinking_status

    console = _capture_console()
    with _thinking_status(console, "modelo-x"):
        pass  # não deve levantar nem deixar live display pendurado
    # segundo uso confirma que o primeiro liberou o live display
    with _thinking_status(console, "modelo-x"):
        pass


# ─── _busy_spinner (genérico) + cobertura de execução de tools ──────────────
# Regressão: "enquanto esta rodando um comando some da parte debaixo o
# terminal ◆ BAUER..." — a bottom_toolbar do prompt_toolkit só existe
# durante o prompt() esperando input; um run_command demorado (docker
# build) ficava sem NENHUM indicador visível, parecendo travado. Fix:
# spinner também durante execução de tool (nativo e bridge), não só
# durante a geração do LLM.


def test_busy_spinner_yields_even_if_console_status_fails():
    from bauer.agent import _busy_spinner

    console = MagicMock()
    console.status.side_effect = RuntimeError("live display já ativo")
    ran = False
    with _busy_spinner(console, "[dim]executando algo…[/dim]"):
        ran = True
    assert ran


def test_busy_spinner_enters_and_exits_cleanly():
    from bauer.agent import _busy_spinner

    console = _capture_console()
    with _busy_spinner(console, "[dim]executando algo…[/dim]"):
        pass
    with _busy_spinner(console, "[dim]executando outra coisa…[/dim]"):
        pass


def test_native_tool_execution_shows_spinner_with_tool_name():
    """_native_turn_interactive envolve router.execute_native_call com
    _busy_spinner — antes só a chamada ao LLM tinha spinner, a execução da
    tool em si (o run_command demorado) ficava muda."""
    import json
    from bauer.agent import _native_turn_interactive
    from bauer.tool_dedup import ToolCallDeduper

    client = MagicMock()
    client.chat_with_tools.return_value = {
        "content": "",
        "tool_calls": [{
            "id": "call_1",
            "function": {"name": "run_command", "arguments": json.dumps({"command": "docker ps"})},
        }],
    }
    router = MagicMock()
    router.get_tool_schemas.return_value = []
    router.execute_native_call.return_value = "CONTAINER ID   IMAGE"

    console = MagicMock()
    ctx = MagicMock()
    ctx.get_payload.return_value = []
    ctx.messages = []

    kind, text = _native_turn_interactive(
        ctx, router, client, "test-model", console,
        cli_tool_log=[], deduper=ToolCallDeduper(), calls_left=10,
    )

    assert kind == "continue"
    status_texts = [c.args[0] for c in console.status.call_args_list]
    assert any("run_command" in t for t in status_texts)


def test_bridge_tool_execution_spinner_shows_single_action_name(ws: Path):
    """Um único tool call — o rótulo do spinner mostra o nome da action."""
    from bauer.agent import _run_tool_loop_body, _TurnState
    from bauer.context_manager import ContextManager
    from bauer.performance_tracker import SessionStats
    from bauer.tool_router import ToolRouter
    from rich.console import Console

    responses = [
        '{"action": "list_dir", "args": {"path": "."}}',
        "Feito.",
    ]
    calls = {"n": 0}

    def _side_effect(*args, **kwargs):
        idx = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return iter([responses[idx]])

    client = MagicMock()
    client.chat_stream.side_effect = _side_effect
    client.last_usage = {}

    ctx = ContextManager(applied_context=4096, system_prompt="System")
    real_console = Console(file=__import__("io").StringIO(), force_terminal=False, width=120)
    router = ToolRouter(workspace=ws)
    stats = SessionStats(model="fake-model", context_tokens=4096, machine_id="x", provider="")
    state = _TurnState(client=client, active_model="fake-model", native_session_ok=False, fb_idx=0, mem_turn_idx=0)

    with patch.object(real_console, "status", wraps=real_console.status) as mock_status:
        _run_tool_loop_body(
            ctx=ctx, router=router, state=state, console=real_console,
            fallback_clients=None, stats=stats, tool_timeout_s=5.0,
            session_store=None, session_id=None, active_workspace=str(ws),
            turn_input_text="liste os arquivos", memprov=None,
        )

    status_texts = [c.args[0] for c in mock_status.call_args_list]
    assert any("list_dir" in t for t in status_texts)
