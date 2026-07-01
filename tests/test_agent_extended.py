"""Testes para bauer/agent.py — funções puras e lógica de turno."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.agent import (
    _build_system_prompt,
    _collect_response,
    _extract_text_from_pseudo_json,
    _run_orchestrator_inline,
    _try_parse_tool,
    run_one_turn,
    run_one_turn_with_fallback,
)
from bauer.openai_client import OpenAIClientError
from bauer.tool_router import ToolRouter


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


@pytest.fixture
def router(ws: Path) -> ToolRouter:
    return ToolRouter(workspace=ws)


def _client(chunks: list[str]) -> MagicMock:
    c = MagicMock()
    c.chat_stream.return_value = iter(chunks)
    return c


# ─── _extract_text_from_pseudo_json ──────────────────────────────────────────


def test_extract_text_from_pseudo_json_content_key():
    """Modelo respondeu com JSON {"action": "x", "args": {"content": "texto"}}."""
    r = _extract_text_from_pseudo_json('{"action": "resp", "args": {"content": "Olá mundo"}}')
    assert r == "Olá mundo"


def test_extract_text_from_pseudo_json_conteudo_key():
    r = _extract_text_from_pseudo_json('{"action": "resp", "args": {"conteudo": "Texto PT"}}')
    assert r == "Texto PT"


def test_extract_text_from_pseudo_json_text_key():
    r = _extract_text_from_pseudo_json('{"action": "resp", "args": {"text": "Texto"}}')
    assert r == "Texto"


def test_extract_text_from_pseudo_json_not_json():
    """String não-JSON retorna None."""
    r = _extract_text_from_pseudo_json("Texto normal")
    assert r is None


def test_extract_text_from_pseudo_json_empty_args():
    """JSON sem args de texto retorna None."""
    r = _extract_text_from_pseudo_json('{"action": "list_dir", "args": {"path": "."}}')
    assert r is None


def test_extract_text_from_pseudo_json_invalid_json():
    r = _extract_text_from_pseudo_json("{broken json")
    assert r is None


# ─── _try_parse_tool ─────────────────────────────────────────────────────────


def test_try_parse_tool_valid_action(router: ToolRouter):
    """Resposta JSON com tool conhecida."""
    result = _try_parse_tool('{"action": "list_dir", "args": {"path": "."}}', router)
    assert result is not None
    assert result["action"] == "list_dir"


def test_try_parse_tool_unknown_action(router: ToolRouter):
    """Tool desconhecida retorna None."""
    result = _try_parse_tool('{"action": "fly_to_moon", "args": {}}', router)
    assert result is None


def test_try_parse_tool_plain_text(router: ToolRouter):
    """Resposta em texto puro retorna None."""
    result = _try_parse_tool("Olá! Como posso ajudar?", router)
    assert result is None


def test_try_parse_tool_json_with_trailing_text(router: ToolRouter):
    """JSON válido no início seguido de texto extra."""
    payload = '{"action": "list_dir", "args": {"path": "."}} Aqui vai texto extra.'
    result = _try_parse_tool(payload, router)
    assert result is not None
    assert result["action"] == "list_dir"


def test_try_parse_tool_markdown_block(router: ToolRouter):
    """JSON em bloco markdown."""
    payload = '```json\n{"action": "list_dir", "args": {"path": "."}}\n```'
    result = _try_parse_tool(payload, router)
    assert result is not None
    assert result["action"] == "list_dir"


def test_try_parse_tool_not_dict(router: ToolRouter):
    """JSON que é lista retorna None."""
    result = _try_parse_tool("[1, 2, 3]", router)
    assert result is None


def test_try_parse_tool_prose_glued_before_json(router: ToolRouter):
    """Modelo narra antes de chamar a tool, sem quebra de linha (bug real reportado):
    'Vou verificar o diretório...{"action": "list_dir", "args": {"path": "."}}'
    Nem estratégia 1 (resposta inteira é JSON) nem estratégia 2 (JSON no início)
    cobrem esse caso — regressão da estratégia 3 (JSON embutido)."""
    payload = (
        'Vou verificar o diretório atual e também tentar localizar o executável '
        '(caso esteja no PATH).{"action": "list_dir", "args": {"path": "."}}'
    )
    result = _try_parse_tool(payload, router)
    assert result is not None
    assert result["action"] == "list_dir"
    assert result["args"] == {"path": "."}


def test_try_parse_tool_prose_before_json_unknown_action_is_none(router: ToolRouter):
    """JSON embutido mas com action desconhecida não deve ser tratado como tool call."""
    payload = 'Deixa eu pensar.{"action": "fly_to_moon", "args": {}}'
    result = _try_parse_tool(payload, router)
    assert result is None


def test_try_parse_tool_plain_text_with_braces_but_no_action(router: ToolRouter):
    """Texto com chaves soltas (ex.: exemplo de código) sem action válida não vira tool call."""
    payload = "Um dicionário em Python se parece com {'chave': 'valor'} — sem mais nada aqui."
    result = _try_parse_tool(payload, router)
    assert result is None


# ─── _collect_response ────────────────────────────────────────────────────────


def test_collect_response_joins_chunks():
    client = _client(["Hel", "lo ", "World"])
    result = _collect_response(client, "phi4-mini", [{"role": "user", "content": "oi"}])
    assert result == "Hello World"


def test_collect_response_empty():
    client = _client([])
    result = _collect_response(client, "phi4-mini", [])
    assert result == ""


# ─── _build_system_prompt ────────────────────────────────────────────────────


def test_build_system_prompt_contains_tools(router: ToolRouter):
    prompt = _build_system_prompt(router)
    assert "list_dir" in prompt
    assert "read_file" in prompt
    assert "write_file" in prompt


def test_build_system_prompt_is_string(router: ToolRouter):
    prompt = _build_system_prompt(router)
    assert isinstance(prompt, str)
    assert len(prompt) > 100


# ─── run_one_turn ─────────────────────────────────────────────────────────────


def test_run_one_turn_text_response(router: ToolRouter):
    """Resposta em texto puro — retorna imediatamente."""
    client = _client(["Olá! Como posso ajudar?"])
    from bauer.context_manager import ContextManager
    ctx = ContextManager(applied_context=4096, system_prompt="System")
    ctx.add_user("oi")

    response, tool_log = run_one_turn(ctx, router, client, "phi4-mini")
    assert response == "Olá! Como posso ajudar?"
    assert tool_log == []


# ─── run_one_turn_with_fallback ───────────────────────────────────────────────


def test_fallback_primary_429_cai_no_proximo(router: ToolRouter):
    """Primário dá 429 (retryável) → wrapper cai no fallback e responde."""
    from bauer.context_manager import ContextManager
    primary = MagicMock()
    primary.chat_stream.side_effect = OpenAIClientError("HTTP 429 do provider")
    fb = _client(["resposta do fallback"])
    ctx = ContextManager(applied_context=4096, system_prompt="System")
    ctx.add_user("oi")

    response, _ = run_one_turn_with_fallback(
        ctx, router, primary, "primary-model", [(fb, "fallback-model")],
    )
    assert response == "resposta do fallback"
    fb.chat_stream.assert_called()


def test_fallback_sem_lista_propaga_erro(router: ToolRouter):
    """Sem fallback configurado, o erro do primário propaga (comportamento antigo)."""
    from bauer.context_manager import ContextManager
    primary = MagicMock()
    primary.chat_stream.side_effect = OpenAIClientError("HTTP 429 do provider")
    ctx = ContextManager(applied_context=4096, system_prompt="System")
    ctx.add_user("oi")

    with pytest.raises(OpenAIClientError):
        run_one_turn_with_fallback(ctx, router, primary, "primary-model", [])


def test_fallback_erro_nao_retryavel_nao_cascateia(router: ToolRouter):
    """Erro não-retryável (ex.: 401 auth) NÃO deve queimar fallbacks — propaga direto."""
    from bauer.context_manager import ContextManager
    primary = MagicMock()
    primary.chat_stream.side_effect = OpenAIClientError("HTTP 401 API key invalida")
    fb = _client(["nao deveria chegar aqui"])
    ctx = ContextManager(applied_context=4096, system_prompt="System")
    ctx.add_user("oi")

    with pytest.raises(OpenAIClientError):
        run_one_turn_with_fallback(
            ctx, router, primary, "primary-model", [(fb, "fallback-model")],
        )
    fb.chat_stream.assert_not_called()


def test_fallback_restaura_ctx_entre_tentativas(router: ToolRouter):
    """ctx.messages volta ao estado pré-turno antes de tentar o fallback (sem lixo)."""
    from bauer.context_manager import ContextManager
    primary = MagicMock()
    primary.chat_stream.side_effect = OpenAIClientError("HTTP 500 server error")
    fb = _client(["ok"])
    ctx = ContextManager(applied_context=4096, system_prompt="System")
    ctx.add_user("oi")
    n_antes = len(ctx.messages)

    run_one_turn_with_fallback(ctx, router, primary, "m", [(fb, "m2")])
    # fallback adicionou 1 assistant; não deve haver acúmulo do turno que falhou
    assert len(ctx.messages) == n_antes + 1


def test_run_one_turn_tool_call_then_text(ws: Path, router: ToolRouter):
    """Tool call seguida de resposta em texto."""
    client = MagicMock()
    # Primeira chamada: JSON de tool; segunda: resposta final em texto
    (ws / "arquivo.txt").write_text("conteúdo", encoding="utf-8")
    responses = [
        '{"action": "list_dir", "args": {"path": "."}}',
        "Os arquivos foram listados com sucesso.",
    ]
    call_count = [0]

    def side_effect(model, messages):
        resp = responses[call_count[0]]
        call_count[0] += 1
        return iter([resp])

    client.chat_stream.side_effect = side_effect

    from bauer.context_manager import ContextManager
    ctx = ContextManager(applied_context=4096, system_prompt="System")
    ctx.add_user("liste os arquivos")

    response, tool_log = run_one_turn(ctx, router, client, "phi4-mini")
    assert response == "Os arquivos foram listados com sucesso."
    assert len(tool_log) == 1
    assert tool_log[0]["tool"] == "list_dir"


def test_run_one_turn_tool_error_continues(router: ToolRouter):
    """Erro de tool é capturado e adicionado ao contexto."""
    client = MagicMock()
    responses = [
        '{"action": "read_file", "args": {"path": "nao_existe.txt"}}',
        "Arquivo não encontrado, mas continuamos.",
    ]
    call_count = [0]

    def side_effect(model, messages):
        resp = responses[call_count[0]]
        call_count[0] += 1
        return iter([resp])

    client.chat_stream.side_effect = side_effect

    from bauer.context_manager import ContextManager
    ctx = ContextManager(applied_context=4096, system_prompt="System")
    ctx.add_user("leia o arquivo")

    response, tool_log = run_one_turn(ctx, router, client, "phi4-mini")
    assert "Arquivo" in response or response
    assert len(tool_log) == 1
    assert "Erro" in tool_log[0]["result"]


def test_run_one_turn_max_tool_turns_stops(router: ToolRouter):
    """Não entra em loop infinito de tool calls."""
    from bauer.agent import MAX_TOOL_TURNS
    from bauer.context_manager import ContextManager

    client = MagicMock()
    # Sempre retorna JSON de tool (force loop)
    client.chat_stream.return_value = iter(['{"action": "list_dir", "args": {"path": "."}}'])
    # Mas cada chamada retorna o mesmo JSON
    client.chat_stream.side_effect = lambda model, msgs: iter(
        ['{"action": "list_dir", "args": {"path": "."}}']
    )

    ctx = ContextManager(applied_context=4096, system_prompt="System")
    ctx.add_user("liste arquivos repetidamente")

    response, tool_log = run_one_turn(ctx, router, client, "phi4-mini")
    # Deve parar no MAX_TOOL_TURNS
    assert len(tool_log) <= MAX_TOOL_TURNS


# ─── _run_orchestrator_inline ────────────────────────────────────────────────


def _make_orchestrator(plan_steps=None, execute_results=None, synthesize_result="Síntese final"):
    orch = MagicMock()
    steps = plan_steps or [
        {"id": 1, "goal": "passo 1", "tools": False, "depends_on": [], "agent": ""},
    ]
    orch.plan.return_value = steps
    orch._topological_batches.return_value = [[s] for s in steps]

    from bauer.orchestrator import StepResult
    results = execute_results or [
        StepResult(id=1, goal="passo 1", model_used="phi4-mini", response="resultado 1"),
    ]
    orch.execute_parallel_steps.return_value = results
    orch.synthesize.return_value = synthesize_result
    return orch


def test_run_orchestrator_inline_success():
    from rich.console import Console
    console = Console(quiet=True)
    orch = _make_orchestrator(synthesize_result="Resposta orquestrada.")

    result = _run_orchestrator_inline("tarefa complexa", orch, console)
    assert result == "Resposta orquestrada."


def test_run_orchestrator_inline_plan_error():
    """Erro no planejamento retorna string vazia."""
    from rich.console import Console
    console = Console(quiet=True)

    orch = MagicMock()
    orch.plan.side_effect = RuntimeError("planejador offline")

    result = _run_orchestrator_inline("tarefa", orch, console)
    assert result == ""


def test_run_orchestrator_inline_empty_steps():
    """Sem passos retorna string vazia."""
    from rich.console import Console
    console = Console(quiet=True)

    orch = MagicMock()
    orch.plan.return_value = []
    orch._topological_batches.return_value = []

    result = _run_orchestrator_inline("tarefa", orch, console)
    assert result == ""


def test_run_orchestrator_inline_with_tool_log():
    """Passos com tool_log exibem ferramentas usadas."""
    from bauer.orchestrator import StepResult
    from rich.console import Console

    console = Console(quiet=True)
    orch = _make_orchestrator(
        execute_results=[
            StepResult(
                id=1, goal="g", model_used="m", response="r",
                tool_log=[{"tool": "list_dir", "result": "ok"}]
            )
        ],
        synthesize_result="ok",
    )
    result = _run_orchestrator_inline("tarefa", orch, console)
    assert result == "ok"


def test_run_orchestrator_inline_execute_error_continues():
    """Erro em execute continua para próxima onda."""
    from rich.console import Console
    from bauer.orchestrator import StepResult

    console = Console(quiet=True)
    orch = MagicMock()
    steps = [
        {"id": 1, "goal": "passo 1", "depends_on": [], "agent": ""},
        {"id": 2, "goal": "passo 2", "depends_on": [], "agent": ""},
    ]
    orch.plan.return_value = steps
    orch._topological_batches.return_value = [[steps[0]], [steps[1]]]
    orch.execute_parallel_steps.side_effect = [
        RuntimeError("falha passo 1"),
        [StepResult(id=2, goal="passo 2", model_used="m", response="ok2")],
    ]
    orch.synthesize.return_value = "sintese parcial"

    result = _run_orchestrator_inline("tarefa", orch, console)
    # Deve continuar e retornar sintese com o que conseguiu
    assert isinstance(result, str)


def test_run_orchestrator_inline_no_results_returns_empty():
    """Se nenhum passo produz resultado, retorna vazio."""
    from rich.console import Console
    console = Console(quiet=True)

    orch = MagicMock()
    orch.plan.return_value = [{"id": 1, "goal": "g", "depends_on": [], "agent": ""}]
    orch._topological_batches.return_value = [[{"id": 1, "goal": "g", "depends_on": [], "agent": ""}]]
    orch.execute_parallel_steps.side_effect = RuntimeError("falhou tudo")

    result = _run_orchestrator_inline("tarefa", orch, console)
    assert result == ""
