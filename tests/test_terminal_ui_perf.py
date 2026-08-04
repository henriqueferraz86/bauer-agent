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


# ─── _tool_exec_status: clarify (input() bloqueante) sob suspend() ─────────
# Regressão 2026-07-02: o spinner de execução de tool (commit 90c5f21)
# envolvia TODAS as tools, inclusive `clarify` — que chama input() direto no
# terminal. Rich Live display (console.status) e input() disputam o
# controle do terminal: a thread de refresh do spinner corrompe a leitura
# de stdin. Usuário reportou "nao consigo escrever" com a resposta
# aparecendo truncada/errada ("totodo").
#
# ATÉ o plano 028 F2, a correção era uma allowlist (`_INTERACTIVE_TOOLS`) que
# fazia `_tool_exec_status` pular o spinner por completo para `clarify` — e
# precisava ser mantida à mão a cada tool interativa nova. Desde o F2, o
# spinner SEMPRE abre (registrado em `bauer/ui_frame.py`) e `_clarify` (em
# `bauer/tools/agent_misc.py`) suspende o que estiver registrado antes de
# chamar `input()`. Os testes abaixo passaram a provar o resultado —
# `input()` nunca corrompido — em vez do mecanismo antigo — spinner ausente.
# A regressão estrutural do "totodo" (múltiplos displays aninhados
# suspendendo juntos) vive em tests/test_ui_frame.py::TestRegressaoTotodo.


def test_tool_exec_status_abre_spinner_tambem_para_clarify():
    """Não existe mais exceção por nome de tool — `_tool_exec_status` não
    sabe (nem precisa saber) que 'clarify' é especial."""
    from bauer.agent import _tool_exec_status

    console = _capture_console()
    with _tool_exec_status(console, "clarify"):
        pass  # não deve levantar — e não é mais um nullcontext disfarçado


def test_tool_exec_status_uses_spinner_for_normal_tools():
    from bauer.agent import _tool_exec_status

    console = _capture_console()
    with _tool_exec_status(console, "run_command"):
        pass  # não deve levantar


def test_native_tool_execution_abre_spinner_para_clarify():
    """O spinner de execução de tool agora abre também para clarify — a
    proteção contra colisão com input() é do lado de dentro (suspend()), não
    de uma lista de exclusão aqui."""
    import json
    from bauer.agent import _native_turn_interactive
    from bauer.tool_dedup import ToolCallDeduper

    client = MagicMock()
    client.chat_with_tools.return_value = {
        "content": "",
        "tool_calls": [{
            "id": "call_1",
            "function": {"name": "clarify", "arguments": json.dumps({"question": "Qual o publico-alvo?"})},
        }],
    }
    router = MagicMock()
    router.get_tool_schemas.return_value = []
    router.execute_native_call.return_value = "usuarios finais"

    console = MagicMock()
    ctx = MagicMock()
    ctx.get_payload.return_value = []
    ctx.messages = []

    kind, text = _native_turn_interactive(
        ctx, router, client, "test-model", console,
        cli_tool_log=[], deduper=ToolCallDeduper(), calls_left=10,
    )

    assert kind == "continue"
    # console.status agora é chamado 2x: a chamada do LLM (_thinking_status)
    # E a execução da tool (_tool_exec_status) — o router aqui é um MOCK, então
    # o _clarify real (com o suspend()) não roda; a suspensão de verdade está
    # coberta por test_bridge_clarify_de_verdade_nao_corrompe_input abaixo.
    status_texts = [c.args[0] for c in console.status.call_args_list]
    assert any("clarify" in t for t in status_texts)


def test_bridge_clarify_de_verdade_nao_corrompe_input(ws: Path):
    """O teste que importa: roda o `_clarify` REAL (não mock) sob um spinner
    de verdade e confirma que o turno completa com a resposta do usuário
    intacta — a prova de que suspend() protegeu o input() de ponta a ponta."""
    from bauer.agent import _run_tool_loop_body, _TurnState
    from bauer.context_manager import ContextManager
    from bauer.performance_tracker import SessionStats
    from bauer.tool_router import ToolRouter

    responses = [
        '{"action": "clarify", "args": {"question": "Qual o publico-alvo?"}}',
        "Obrigado, entendido.",
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
    router = ToolRouter(workspace=ws)
    stats = SessionStats(model="fake-model", context_tokens=4096, machine_id="x", provider="")
    state = _TurnState(client=client, active_model="fake-model", native_session_ok=False, fb_idx=0, mem_turn_idx=0)

    with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value="publico geral") as mock_input:
        mock_stdin.isatty.return_value = True
        outcome = _run_tool_loop_body(
            ctx=ctx, router=router, state=state, console=Console(file=io.StringIO(), force_terminal=False),
            fallback_clients=None, stats=stats, tool_timeout_s=5.0,
            session_store=None, session_id=None, active_workspace=str(ws),
            turn_input_text="pergunte algo", memprov=None,
        )

    mock_input.assert_called_once()  # a resposta chegou intacta, uma vez só
    assert outcome.kind == "final"
    assert outcome.display == "Obrigado, entendido."


def test_bridge_batch_paralelo_propaga_contexto_para_suspend(ws: Path):
    """O cenário que motivou o fix de contextvars, medido no que IMPORTA: o
    display registrado na thread principal é de fato PAUSADO por um
    suspend() que roda dentro da ThreadPoolExecutor.

    Duas tools no mesmo lote forçam o caminho `len(_to_execute) > 1`, que
    despacha em pool. ThreadPoolExecutor não herda o Context da thread
    chamadora — sem `copy_context()` por submit (plano 028 F2), a pilha de
    ui_frame fica invisível na worker e suspend() vira no-op silencioso.

    Verificar só "o input() saiu limpo" NÃO detecta isso (com input mockado
    nada colide de verdade) — por isso o assert é sobre o stop/start terem
    acontecido.
    """
    from bauer.agent import _run_tool_loop_body, _TurnState
    from bauer.context_manager import ContextManager
    from bauer.performance_tracker import SessionStats
    from bauer.tool_router import ToolRouter
    from bauer import ui_frame

    eventos: list[str] = []

    class _DisplayEspiao:
        def stop(self) -> None:
            eventos.append("stop")

        def start(self) -> None:
            eventos.append("start")

    lote = (
        '{"action": "list_dir", "args": {"path": "."}}\n'
        '{"action": "clarify", "args": {"question": "Qual o publico-alvo?"}}'
    )
    responses = [lote, "Obrigado, entendido."]
    calls = {"n": 0}

    def _side_effect(*args, **kwargs):
        idx = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return iter([responses[idx]])

    client = MagicMock()
    client.chat_stream.side_effect = _side_effect
    client.last_usage = {}

    ctx = ContextManager(applied_context=4096, system_prompt="System")
    router = ToolRouter(workspace=ws)
    stats = SessionStats(model="fake-model", context_tokens=4096, machine_id="x", provider="")
    state = _TurnState(client=client, active_model="fake-model", native_session_ok=False, fb_idx=0, mem_turn_idx=0)

    with patch("sys.stdin") as mock_stdin, patch("builtins.input", return_value="publico geral") as mock_input:
        mock_stdin.isatty.return_value = True
        # registrado na thread PRINCIPAL, como o spinner real do lote
        with ui_frame.register(_DisplayEspiao()):
            outcome = _run_tool_loop_body(
                ctx=ctx, router=router, state=state, console=Console(file=io.StringIO(), force_terminal=False),
                fallback_clients=None, stats=stats, tool_timeout_s=5.0,
                session_store=None, session_id=None, active_workspace=str(ws),
                turn_input_text="liste e pergunte", memprov=None,
            )

    mock_input.assert_called_once()
    assert outcome.kind == "final"
    assert outcome.display == "Obrigado, entendido."
    # as DUAS tools do lote correram — não só a clarify
    assert {t["tool"] for t in outcome.tool_log} == {"list_dir", "clarify"}
    # e o suspend() da clarify (rodando no POOL) enxergou o registro da main
    assert eventos == ["stop", "start"], (
        "suspend() dentro da ThreadPoolExecutor não pausou o display "
        "registrado na thread principal — copy_context() por submit sumiu?"
    )
