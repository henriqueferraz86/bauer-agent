"""Testes adicionais para AgentOrchestrator — cobre linhas 154-586."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.orchestrator import (
    AgentOrchestrator,
    OrchestratorConfig,
    StepResult,
)


# ─── Factories ───────────────────────────────────────────────────────────────


def _client(reply: str = "resultado mock") -> MagicMock:
    c = MagicMock()
    c.chat_stream.return_value = iter([reply])
    return c


def _make_orch(
    reply: str = "resultado mock",
    parallel: bool = False,
    max_retries: int = 0,
    workspace: Path | None = None,
) -> AgentOrchestrator:
    from bauer.model_router import ModelRouter, Route
    from bauer.tool_router import ToolRouter

    client = _client(reply)
    ws = workspace or Path(".")
    tool_router = ToolRouter(workspace=ws)
    model_router = MagicMock(spec=ModelRouter)
    model_router.select_model.return_value = (
        "phi4-mini",
        Route(kind="direct", label="Direct", model="phi4-mini"),
    )
    cfg = OrchestratorConfig(parallel_steps=parallel, max_retries=max_retries)
    return AgentOrchestrator(
        client=client,
        tool_router=tool_router,
        model_router=model_router,
        config=cfg,
        planner_client=client,
    )


def _step(
    id: int = 1,
    goal: str = "tarefa teste",
    tools: bool = False,
    depends_on: list | None = None,
    agent: str = "",
) -> dict:
    return {
        "id": id,
        "goal": goal,
        "tools": tools,
        "depends_on": depends_on or [],
        "agent": agent,
    }


# ─── _call_model com console ─────────────────────────────────────────────────


def test_call_model_with_console():
    orch = _make_orch("hello")
    console = MagicMock()
    orch.console = console
    result = orch._call_model("phi4-mini", [{"role": "user", "content": "oi"}], stream_prefix="[p1]")
    assert result == "hello"
    console.print.assert_called()


def test_call_model_without_prefix_no_console_print():
    orch = _make_orch("hello")
    console = MagicMock()
    orch.console = console
    # stream_prefix vazio — nao deve chamar console.print mid-stream
    orch._call_model("phi4-mini", [{"role": "user", "content": "oi"}], stream_prefix="")
    # console.print nao e chamado para chunks sem prefixo
    console.print.assert_not_called()


def test_call_ollama_with_console():
    orch = _make_orch("resultado")
    console = MagicMock()
    orch.console = console
    result = orch._call_ollama("phi4-mini", [{"role": "user", "content": "oi"}], stream_prefix="[p1]")
    assert result == "resultado"
    console.print.assert_called()


# ─── _extract_json ────────────────────────────────────────────────────────────


def test_extract_json_plain_json():
    orch = _make_orch()
    data = {"objective": "teste", "steps": []}
    result = orch._extract_json(json.dumps(data))
    assert result == data


def test_extract_json_markdown_block():
    orch = _make_orch()
    json_str = json.dumps({"objective": "teste", "steps": []})
    text = f"```json\n{json_str}\n```"
    result = orch._extract_json(text)
    assert result is not None
    assert result["objective"] == "teste"


def test_extract_json_brace_extraction():
    orch = _make_orch()
    text = 'Aqui esta o plano: {"id": 1, "goal": "algo"} fim.'
    result = orch._extract_json(text)
    assert result is not None
    assert result["id"] == 1


def test_extract_json_invalid_returns_none():
    orch = _make_orch()
    result = orch._extract_json("isso nao e json nenhum")
    assert result is None


# ─── plan com agents e specs ─────────────────────────────────────────────────


def test_plan_with_agents_section():
    orch = _make_orch()
    plan_response = json.dumps({
        "objective": "fazer algo",
        "steps": [
            {"id": 1, "goal": "passo 1", "tools": False, "depends_on": [], "agent": ""},
        ],
    })
    orch._planner_client.chat_stream.return_value = iter([plan_response])

    mock_agent = MagicMock()
    mock_agent.name = "python"
    mock_agent.description = "Agente Python"

    steps = orch.plan("tarefa teste", agents=[mock_agent])
    assert len(steps) == 1


def test_plan_with_specs_section():
    orch = _make_orch()
    plan_response = json.dumps({
        "objective": "fazer algo",
        "steps": [
            {"id": 1, "goal": "passo 1", "tools": False, "depends_on": [], "agent": ""},
        ],
    })
    orch._planner_client.chat_stream.return_value = iter([plan_response])

    mock_spec = MagicMock()
    mock_spec.to_context.return_value = "SPEC-001: Especificacao teste"

    steps = orch.plan("tarefa teste", specs=[mock_spec])
    assert len(steps) >= 1


def test_plan_fallback_on_bad_response():
    """Quando o modelo retorna JSON inválido, cria passo fallback."""
    orch = _make_orch()
    orch._planner_client.chat_stream.return_value = iter(["isso nao e json"])
    steps = orch.plan("tarefa impossivel")
    assert len(steps) == 1
    assert "tarefa impossivel" in steps[0]["goal"]


# ─── _load_agent_system ──────────────────────────────────────────────────────


def test_load_agent_system_empty_name():
    orch = _make_orch()
    assert orch._load_agent_system("") == ""


def test_load_agent_system_nonexistent():
    orch = _make_orch()
    result = orch._load_agent_system("nao-existe")
    assert result == ""


def test_load_agent_system_found(tmp_path: Path):
    from bauer.agent_registry import AgentDef, AgentRegistry
    reg = AgentRegistry(path=str(tmp_path / "agents.yaml"))
    reg.save(AgentDef(
        name="meu-agent",
        description="desc",
        system="Voce e um especialista.",
    ))
    orch = _make_orch()
    orch.config.agents_file = str(tmp_path / "agents.yaml")
    result = orch._load_agent_system("meu-agent")
    assert result == "Voce e um especialista."


# ─── execute_step ─────────────────────────────────────────────────────────────


def test_execute_step_without_tools():
    orch = _make_orch("resposta direta")
    step = _step(id=1, goal="escreva algo", tools=False)
    result = orch.execute_step(step, [])
    assert isinstance(result, StepResult)
    assert result.id == 1
    assert result.goal == "escreva algo"
    assert result.response == "resposta direta"
    assert result.tool_log == []


def test_execute_step_with_previous_results():
    orch = _make_orch("resposta com contexto")
    prev = StepResult(id=1, goal="passo anterior", model_used="phi4-mini", response="resultado anterior")
    step = _step(id=2, goal="use o resultado anterior", tools=False)
    result = orch.execute_step(step, [prev])
    assert result.id == 2


def test_execute_step_with_console_stream():
    orch = _make_orch("resposta streaming")
    orch.console = MagicMock()
    step = _step(id=3, goal="tarefa com streaming", tools=False)
    result = orch.execute_step(step, [])
    assert result.response == "resposta streaming"


def test_execute_step_with_agent_system(tmp_path: Path):
    from bauer.agent_registry import AgentDef, AgentRegistry
    reg = AgentRegistry(path=str(tmp_path / "agents.yaml"))
    reg.save(AgentDef(name="especialista", description="d", system="Sou especialista."))

    orch = _make_orch("resposta especializada")
    orch.config.agents_file = str(tmp_path / "agents.yaml")
    step = _step(id=1, goal="tarefa especializada", tools=False, agent="especialista")
    result = orch.execute_step(step, [])
    assert result.response == "resposta especializada"


def test_execute_step_with_tools(tmp_path: Path):
    """execute_step com tools=True usa run_one_turn."""
    orch = _make_orch()
    # Mocka run_one_turn para nao precisar de LLM real
    with patch("bauer.orchestrator.run_one_turn") as mock_run:
        mock_run.return_value = ("resultado com tools", [{"tool": "list_dir", "result": "ok"}])
        step = _step(id=1, goal="liste arquivos", tools=True)
        result = orch.execute_step(step, [])
        assert result.response == "resultado com tools"
        assert len(result.tool_log) == 1
        assert result.tool_log[0]["tool"] == "list_dir"


def test_execute_step_with_tools_and_agent(tmp_path: Path):
    """execute_step com tools=True E agent especializado."""
    from bauer.agent_registry import AgentDef, AgentRegistry
    reg = AgentRegistry(path=str(tmp_path / "agents.yaml"))
    reg.save(AgentDef(name="dev", description="d", system="Especialista em codigo."))

    orch = _make_orch()
    orch.config.agents_file = str(tmp_path / "agents.yaml")

    with patch("bauer.orchestrator.run_one_turn") as mock_run:
        mock_run.return_value = ("codigo gerado", [])
        step = _step(id=1, goal="escreva codigo", tools=True, agent="dev")
        result = orch.execute_step(step, [])
        assert result.response == "codigo gerado"


# ─── _execute_step_with_retry ─────────────────────────────────────────────────


def test_retry_succeeds_on_first_attempt():
    orch = _make_orch("ok")
    step = _step()
    result = orch._execute_step_with_retry(step, [])
    assert result.response == "ok"


def test_retry_after_failure():
    """Primeira tentativa falha, segunda funciona."""
    orch = _make_orch()
    orch.config.max_retries = 2
    orch.config.retry_delay_s = 0.0  # sem delay nos testes

    call_count = [0]

    def fake_execute(step, previous):
        call_count[0] += 1
        if call_count[0] < 2:
            raise RuntimeError("falha temporaria")
        return StepResult(id=1, goal="g", model_used="m", response="recuperado")

    orch.execute_step = fake_execute
    result = orch._execute_step_with_retry(step=_step(), previous_results=[])
    assert result.response == "recuperado"
    assert call_count[0] == 2


def test_retry_exhausted_returns_error_result():
    """Quando todas as tentativas falham, retorna StepResult de erro."""
    orch = _make_orch()
    orch.config.max_retries = 1
    orch.config.retry_delay_s = 0.0

    def always_fail(step, previous):
        raise RuntimeError("sempre falha")

    orch.execute_step = always_fail
    result = orch._execute_step_with_retry(step=_step(id=5), previous_results=[])
    assert result.id == 5
    assert "falhou" in result.response.lower() or "erro" in result.model_used


# ─── execute_parallel_steps ──────────────────────────────────────────────────


def test_execute_parallel_sequential_mode():
    orch = _make_orch("ok")
    batch = [_step(1), _step(2)]
    results = orch.execute_parallel_steps(batch, [])
    assert len(results) == 2
    assert {r.id for r in results} == {1, 2}


def test_execute_parallel_true_mode():
    """Modo paralelo com ThreadPoolExecutor."""
    orch = _make_orch()
    orch.config.parallel_steps = True

    def fake_step(step, previous):
        return StepResult(id=step["id"], goal=step["goal"], model_used="m", response="ok")

    orch._execute_step_with_retry = fake_step
    batch = [_step(1), _step(2), _step(3)]
    results = orch.execute_parallel_steps(batch, [])
    assert len(results) == 3
    ids = {r.id for r in results}
    assert ids == {1, 2, 3}


def test_execute_parallel_single_step_skips_threadpool():
    orch = _make_orch("ok")
    orch.config.parallel_steps = True  # mesmo com paralelo, batch de 1 é sequencial
    batch = [_step(1)]
    results = orch.execute_parallel_steps(batch, [])
    assert len(results) == 1


# ─── Persistência ─────────────────────────────────────────────────────────────


def test_save_and_load_plan(tmp_path: Path):
    orch = _make_orch()
    # Sobrescreve _progress_path para usar tmp_path
    original = orch._progress_path

    def patched(task):
        h = original(task).name
        return tmp_path / h

    orch._progress_path = patched

    steps = [_step(1, "buscar dados"), _step(2, "analisar")]
    orch.save_plan("minha tarefa", steps)
    loaded = orch.load_plan("minha tarefa")
    assert loaded is not None
    assert len(loaded) == 2
    assert loaded[0]["goal"] == "buscar dados"


def test_load_plan_not_found(tmp_path: Path):
    orch = _make_orch()
    orch._progress_path = lambda task: tmp_path / "nao-existe"
    result = orch.load_plan("tarefa inexistente")
    assert result is None


def test_save_and_load_progress(tmp_path: Path):
    orch = _make_orch()
    orch._progress_path = lambda task: tmp_path / "progress"

    results = [
        StepResult(id=1, goal="passo 1", model_used="phi4-mini", response="res 1"),
        StepResult(id=2, goal="passo 2", model_used="phi4-mini", response="res 2"),
    ]
    orch.save_progress("tarefa", results)
    loaded = orch.load_progress("tarefa")
    assert len(loaded) == 2
    assert {r.id for r in loaded} == {1, 2}


def test_load_progress_empty(tmp_path: Path):
    orch = _make_orch()
    orch._progress_path = lambda task: tmp_path / "nao-existe"
    assert orch.load_progress("qualquer") == []


def test_clear_progress(tmp_path: Path):
    orch = _make_orch()
    p = tmp_path / "progress"
    p.mkdir()
    orch._progress_path = lambda task: p
    assert p.exists()
    orch.clear_progress("tarefa")
    assert not p.exists()


def test_has_saved_progress_false(tmp_path: Path):
    orch = _make_orch()
    orch._progress_path = lambda task: tmp_path / "nao-existe"
    assert orch.has_saved_progress("qualquer") is False


def test_has_saved_progress_true(tmp_path: Path):
    orch = _make_orch()
    p = tmp_path / "progress"
    p.mkdir()
    orch._progress_path = lambda task: p
    assert orch.has_saved_progress("qualquer") is True


# ─── synthesize ──────────────────────────────────────────────────────────────


def test_synthesize_combines_results():
    orch = _make_orch("Resposta sintetizada.")
    results = [
        StepResult(id=1, goal="passo 1", model_used="phi4-mini", response="Dados coletados."),
        StepResult(id=2, goal="passo 2", model_used="phi4-mini", response="Analise concluida."),
    ]
    final = orch.synthesize("objetivo principal", results)
    assert final == "Resposta sintetizada."


def test_synthesize_includes_tool_log():
    orch = _make_orch("Sintese com tools.")
    results = [
        StepResult(
            id=1, goal="tarefa", model_used="phi4-mini", response="ok",
            tool_log=[{"tool": "list_dir", "result": "arquivos.txt"}],
        ),
    ]
    final = orch.synthesize("objetivo", results)
    assert final == "Sintese com tools."


# ─── run() completo ──────────────────────────────────────────────────────────


def test_run_full_flow(tmp_path: Path):
    """Testa o fluxo completo plan→execute→synthesize com mocks."""
    orch = _make_orch()

    plan_json = json.dumps({
        "objective": "fazer algo",
        "steps": [
            {"id": 1, "goal": "passo simples", "tools": False, "depends_on": [], "agent": ""},
        ],
    })

    call_seq = [plan_json, "passo executado", "sintese final"]
    it = iter(call_seq)

    def stream_side_effect(model, messages):
        return iter([next(it)])

    orch._planner_client.chat_stream.side_effect = stream_side_effect
    orch.client.chat_stream.side_effect = stream_side_effect

    # Usa tmp_path para progress
    orch._progress_path = lambda task: tmp_path / "progress"

    final, results = orch.run("fazer algo")
    assert isinstance(final, str)
    assert len(results) >= 1


def test_run_with_resume(tmp_path: Path):
    """resume=True carrega plano e progresso salvos."""
    orch = _make_orch()
    orch._progress_path = lambda task: tmp_path / "p"

    # Salva plano previamente
    steps = [{"id": 1, "goal": "passo ja feito", "tools": False, "depends_on": [], "agent": ""}]
    orch.save_plan("tarefa", steps)
    # Salva resultado do passo 1
    orch.save_progress("tarefa", [
        StepResult(id=1, goal="passo ja feito", model_used="m", response="resultado cached"),
    ])
    # Mocka sintetize
    orch._planner_client.chat_stream.return_value = iter(["sintese com resume"])

    final, results = orch.run("tarefa", resume=True)
    assert isinstance(final, str)
    # O passo 1 nao deve ser executado novamente (ja estava em done)
    assert any(r.response == "resultado cached" for r in results)
