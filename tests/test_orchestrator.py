"""Testes para AgentOrchestrator — DAG, paralelo e persistência."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bauer.orchestrator import (
    AgentOrchestrator,
    OrchestratorConfig,
    StepResult,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_orch(tmp_path: Path) -> AgentOrchestrator:
    """Cria um AgentOrchestrator com mocks para client/router/model_router."""
    client = MagicMock()
    client.chat_stream.return_value = iter(["resposta mock"])
    tool_router = MagicMock()
    model_router = MagicMock()
    model_router.select_model.return_value = ("phi4-mini", MagicMock())
    cfg = OrchestratorConfig(planner_model="qwen3:0.6b", synthesizer_model="phi4-mini")
    orch = AgentOrchestrator(client, tool_router, model_router, cfg)
    # Redireciona progresso para tmp_path
    orch._base_progress_dir = tmp_path
    # Patch _progress_path para usar tmp_path
    original = orch._progress_path

    def _patched(task: str) -> Path:
        import hashlib
        h = hashlib.md5(task.encode("utf-8")).hexdigest()[:10]
        return tmp_path / h

    orch._progress_path = _patched  # type: ignore[method-assign]
    return orch


# ─── Testes: _build_dag ───────────────────────────────────────────────────────


def test_build_dag_basic(tmp_path):
    orch = _make_orch(tmp_path)
    steps = [
        {"id": 1, "goal": "a", "tools": False, "depends_on": []},
        {"id": 2, "goal": "b", "tools": False, "depends_on": [1]},
        {"id": 3, "goal": "c", "tools": False, "depends_on": [1]},
    ]
    dag = orch._build_dag(steps)
    assert dag == {1: [], 2: [1], 3: [1]}


def test_build_dag_missing_depends_on(tmp_path):
    """depends_on ausente deve ser tratado como []."""
    orch = _make_orch(tmp_path)
    steps = [{"id": 1, "goal": "a", "tools": False}]
    dag = orch._build_dag(steps)
    assert dag == {1: []}


# ─── Testes: _topological_batches ────────────────────────────────────────────


def test_topological_batches_linear(tmp_path):
    """Passos 1→2→3 devem gerar 3 ondas de 1 passo cada."""
    orch = _make_orch(tmp_path)
    steps = [
        {"id": 1, "goal": "a", "tools": False, "depends_on": []},
        {"id": 2, "goal": "b", "tools": False, "depends_on": [1]},
        {"id": 3, "goal": "c", "tools": False, "depends_on": [2]},
    ]
    batches = orch._topological_batches(steps)
    assert len(batches) == 3
    assert [s["id"] for s in batches[0]] == [1]
    assert [s["id"] for s in batches[1]] == [2]
    assert [s["id"] for s in batches[2]] == [3]


def test_topological_batches_parallel(tmp_path):
    """Passos 1→{2,3}→4: onda 1 tem 2 passos paralelos."""
    orch = _make_orch(tmp_path)
    steps = [
        {"id": 1, "goal": "a", "tools": False, "depends_on": []},
        {"id": 2, "goal": "b", "tools": False, "depends_on": [1]},
        {"id": 3, "goal": "c", "tools": False, "depends_on": [1]},
        {"id": 4, "goal": "d", "tools": False, "depends_on": [2, 3]},
    ]
    batches = orch._topological_batches(steps)
    assert len(batches) == 3
    assert [s["id"] for s in batches[0]] == [1]
    assert {s["id"] for s in batches[1]} == {2, 3}  # ordem pode variar
    assert [s["id"] for s in batches[2]] == [4]


def test_topological_batches_all_independent(tmp_path):
    """Todos os passos independentes: 1 onda com todos."""
    orch = _make_orch(tmp_path)
    steps = [
        {"id": 1, "goal": "a", "tools": False, "depends_on": []},
        {"id": 2, "goal": "b", "tools": False, "depends_on": []},
        {"id": 3, "goal": "c", "tools": False, "depends_on": []},
    ]
    batches = orch._topological_batches(steps)
    assert len(batches) == 1
    assert {s["id"] for s in batches[0]} == {1, 2, 3}


def test_topological_batches_circular_fallback(tmp_path):
    """Dependência circular deve cair no fallback sequencial sem travar."""
    orch = _make_orch(tmp_path)
    steps = [
        {"id": 1, "goal": "a", "tools": False, "depends_on": [2]},
        {"id": 2, "goal": "b", "tools": False, "depends_on": [1]},
    ]
    batches = orch._topological_batches(steps)
    # Deve retornar algo (não travar) — dois batches de 1 cada no fallback
    total_steps = sum(len(b) for b in batches)
    assert total_steps == 2


# ─── Testes: persistência ────────────────────────────────────────────────────


def test_save_and_load_plan(tmp_path):
    orch = _make_orch(tmp_path)
    task = "tarefa de teste"
    steps = [
        {"id": 1, "goal": "passo 1", "tools": True, "depends_on": []},
        {"id": 2, "goal": "passo 2", "tools": False, "depends_on": [1]},
    ]
    orch.save_plan(task, steps)
    loaded = orch.load_plan(task)
    assert loaded == steps


def test_load_plan_not_found(tmp_path):
    orch = _make_orch(tmp_path)
    assert orch.load_plan("tarefa inexistente") is None


def test_save_and_load_progress(tmp_path):
    orch = _make_orch(tmp_path)
    task = "tarefa de teste"
    results = [
        StepResult(id=1, goal="passo 1", model_used="phi4-mini", response="ok", tool_log=[]),
        StepResult(id=2, goal="passo 2", model_used="qwen3:0.6b", response="feito", tool_log=[]),
    ]
    orch.save_progress(task, results)
    loaded = orch.load_progress(task)
    assert len(loaded) == 2
    assert loaded[0].id == 1
    assert loaded[1].id == 2
    assert loaded[0].response == "ok"
    assert loaded[1].model_used == "qwen3:0.6b"


def test_load_progress_not_found(tmp_path):
    orch = _make_orch(tmp_path)
    assert orch.load_progress("tarefa inexistente") == []


def test_clear_progress(tmp_path):
    orch = _make_orch(tmp_path)
    task = "tarefa clara"
    orch.save_plan(task, [{"id": 1, "goal": "x", "tools": False, "depends_on": []}])
    assert orch.has_saved_progress(task)
    orch.clear_progress(task)
    assert not orch.has_saved_progress(task)


def test_has_saved_progress_false(tmp_path):
    orch = _make_orch(tmp_path)
    assert not orch.has_saved_progress("nada aqui")


def test_step_result_timestamp(tmp_path):
    """StepResult deve ter timestamp automatico."""
    before = time.time()
    r = StepResult(id=1, goal="g", model_used="m", response="r")
    after = time.time()
    assert before <= r.timestamp <= after


def test_progress_serialization_roundtrip(tmp_path):
    """Serialização e desserialização de StepResult deve preservar todos os campos."""
    orch = _make_orch(tmp_path)
    task = "roundtrip"
    ts = time.time()
    original = StepResult(
        id=3,
        goal="executar algo",
        model_used="smollm3",
        response="resultado longo aqui",
        tool_log=[{"tool": "read_file", "result": "conteudo"}],
        timestamp=ts,
    )
    orch.save_progress(task, [original])
    loaded = orch.load_progress(task)
    assert len(loaded) == 1
    r = loaded[0]
    assert r.id == original.id
    assert r.goal == original.goal
    assert r.model_used == original.model_used
    assert r.response == original.response
    assert r.tool_log == original.tool_log
    assert abs(r.timestamp - ts) < 0.001


# ─── Testes: execute_parallel_steps ──────────────────────────────────────────


def test_execute_parallel_single_step(tmp_path):
    """Batch de 1 passo deve chamar execute_step direto (sem ThreadPoolExecutor)."""
    orch = _make_orch(tmp_path)
    step = {"id": 1, "goal": "passo unico", "tools": False, "depends_on": []}
    expected = StepResult(id=1, goal="passo unico", model_used="phi4-mini", response="ok")

    with patch.object(orch, "execute_step", return_value=expected) as mock_exec:
        results = orch.execute_parallel_steps([step], [])
    assert len(results) == 1
    assert results[0] is expected
    mock_exec.assert_called_once_with(step, [])


def test_execute_parallel_multiple_steps(tmp_path):
    """Batch de N passos deve retornar N resultados ordenados por id."""
    orch = _make_orch(tmp_path)
    steps = [
        {"id": 2, "goal": "passo 2", "tools": False, "depends_on": []},
        {"id": 3, "goal": "passo 3", "tools": False, "depends_on": []},
    ]
    results_map = {
        2: StepResult(id=2, goal="passo 2", model_used="phi4-mini", response="r2"),
        3: StepResult(id=3, goal="passo 3", model_used="phi4-mini", response="r3"),
    }

    def fake_execute(step, prev):
        return results_map[step["id"]]

    with patch.object(orch, "execute_step", side_effect=fake_execute):
        results = orch.execute_parallel_steps(steps, [])

    assert len(results) == 2
    ids = [r.id for r in results]
    assert ids == sorted(ids)  # deve estar ordenado por id


# ─── Testes: plan() com depends_on ───────────────────────────────────────────


def test_plan_defaults_depends_on(tmp_path):
    """plan() deve garantir depends_on em todos os passos mesmo se o modelo omitir."""
    orch = _make_orch(tmp_path)
    raw_plan = json.dumps({
        "objective": "fazer algo",
        "steps": [
            {"id": 1, "goal": "passo 1", "tools": True},          # sem depends_on
            {"id": 2, "goal": "passo 2", "tools": False, "depends_on": [1]},
        ],
    })
    orch._call_ollama = MagicMock(return_value=raw_plan)
    steps = orch.plan("fazer algo")
    assert all("depends_on" in s for s in steps)
    assert steps[0]["depends_on"] == []
    assert steps[1]["depends_on"] == [1]


# ─── Testes: retry por passo ──────────────────────────────────────────────────


def test_retry_succeeds_on_second_attempt(tmp_path):
    """Passo deve ter sucesso se falhar na 1a tentativa e passar na 2a."""
    orch = _make_orch(tmp_path)
    orch.config.max_retries = 2
    orch.config.retry_delay_s = 0  # sem delay nos testes

    step = {"id": 1, "goal": "passo com falha inicial", "tools": False}
    success = StepResult(id=1, goal="passo com falha inicial", model_used="phi4-mini", response="ok")

    call_count = {"n": 0}

    def flaky_execute(s, prev):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("timeout simulado")
        return success

    with patch.object(orch, "execute_step", side_effect=flaky_execute):
        result = orch._execute_step_with_retry(step, [])

    assert result.response == "ok"
    assert call_count["n"] == 2  # falhou 1x, passou na 2a


def test_retry_exhausted_returns_error_result(tmp_path):
    """Passo deve retornar StepResult de erro apos esgotar todas as tentativas."""
    orch = _make_orch(tmp_path)
    orch.config.max_retries = 2
    orch.config.retry_delay_s = 0

    step = {"id": 5, "goal": "passo que sempre falha", "tools": False}

    with patch.object(orch, "execute_step", side_effect=RuntimeError("ollama offline")):
        result = orch._execute_step_with_retry(step, [])

    assert result.id == 5
    assert result.model_used == "(erro)"
    assert "3 tentativa" in result.response  # max_retries=2 → 3 tentativas no total
    assert "ollama offline" in result.response


def test_retry_zero_means_no_retry(tmp_path):
    """Com max_retries=0, deve tentar apenas uma vez e falhar imediatamente."""
    orch = _make_orch(tmp_path)
    orch.config.max_retries = 0
    orch.config.retry_delay_s = 0

    step = {"id": 2, "goal": "falha unica", "tools": False}
    call_count = {"n": 0}

    def always_fail(s, prev):
        call_count["n"] += 1
        raise ValueError("erro unico")

    with patch.object(orch, "execute_step", side_effect=always_fail):
        result = orch._execute_step_with_retry(step, [])

    assert call_count["n"] == 1  # so tentou 1 vez
    assert result.model_used == "(erro)"
    assert "1 tentativa" in result.response


def test_retry_wave_continues_after_step_failure(tmp_path):
    """Onda com 2 passos: se um falha (apos retries), o outro ainda deve executar."""
    orch = _make_orch(tmp_path)
    orch.config.max_retries = 0
    orch.config.retry_delay_s = 0
    orch.config.parallel_steps = False  # sequencial

    steps = [
        {"id": 1, "goal": "passo que falha", "tools": False, "depends_on": []},
        {"id": 2, "goal": "passo normal", "tools": False, "depends_on": []},
    ]
    ok_result = StepResult(id=2, goal="passo normal", model_used="phi4-mini", response="sucesso")

    def selective_fail(s, prev):
        if s["id"] == 1:
            raise RuntimeError("falha proposital")
        return ok_result

    with patch.object(orch, "execute_step", side_effect=selective_fail):
        results = orch.execute_parallel_steps(steps, [])

    assert len(results) == 2
    error_r = next(r for r in results if r.id == 1)
    ok_r = next(r for r in results if r.id == 2)
    assert error_r.model_used == "(erro)"
    assert ok_r.response == "sucesso"  # onda continuou mesmo com passo 1 falhando
