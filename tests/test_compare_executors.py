"""Testa a MECÂNICA do harness de comparação (evals/compare_executors), não um
provider real — isto roda na suíte hermética padrão; o harness em si (invocado
via ``python -m evals.compare_executors``) não."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("agno")

from evals.compare_executors.runner import (
    ComparisonRow,
    ExecutionResult,
    Report,
    _CostRecorder,
    _run_agno,
    _run_bauer_native,
    formatar,
    run_comparison,
)
from evals.compare_executors.tasks import Task


def _fake_cfg():
    return SimpleNamespace(model=SimpleNamespace(provider="openrouter", name="deepseek/deepseek-chat"))


def test_cost_recorder_accumulates_across_calls():
    recorder = _CostRecorder()
    recorder("openrouter", "deepseek/deepseek-chat", {"prompt_tokens": 10, "completion_tokens": 5}, 0.001)
    recorder("openrouter", "deepseek/deepseek-chat", {"prompt_tokens": 3, "completion_tokens": 2}, 0.0003)
    assert recorder.total_usd == pytest.approx(0.0013)
    assert recorder.prompt_tokens == 13
    assert recorder.completion_tokens == 7


def test_run_bauer_native_reports_cost_and_latency(monkeypatch, tmp_path):
    from bauer.cost_meter import report_llm_cost

    def fake_build_client(cfg):
        return object()

    def fake_build_router(cfg, workspace, llm_client=None, session_id=""):
        return object()

    def fake_run_one_turn_with_fallback(ctx, router, client, model_name, **kwargs):
        report_llm_cost("openrouter", model_name, {"prompt_tokens": 50, "completion_tokens": 20})
        return "resposta de teste", []

    monkeypatch.setattr("evals.compare_executors.runner._build_client", fake_build_client)
    monkeypatch.setattr("evals.compare_executors.runner._build_router", fake_build_router)
    monkeypatch.setattr(
        "bauer.agent.run_one_turn_with_fallback", fake_run_one_turn_with_fallback
    )

    task = Task(name="t1", prompt="oi")
    result = _run_bauer_native(task, _fake_cfg(), tmp_path)

    assert result.executor == "bauer_native"
    assert result.output == "resposta de teste"
    assert result.prompt_tokens == 50
    assert result.completion_tokens == 20
    assert result.cost_usd > 0.0
    assert result.latency_s >= 0.0
    assert result.error == ""


def test_run_bauer_native_captures_exception_as_error(monkeypatch, tmp_path):
    def boom(cfg):
        raise RuntimeError("sem credenciais")

    monkeypatch.setattr("evals.compare_executors.runner._build_client", boom)

    task = Task(name="t1", prompt="oi")
    result = _run_bauer_native(task, _fake_cfg(), tmp_path)

    assert result.error
    assert "sem credenciais" in result.error
    assert result.output == ""


def test_run_agno_reports_cost_and_latency(monkeypatch, tmp_path):
    from agno.metrics import RunMetrics
    from agno.run.agent import RunCompletedEvent, RunContentEvent

    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    class ControlledAgent:
        def run(self, *args, **kwargs):
            yield RunContentEvent(content="resposta agno")
            yield RunCompletedEvent(
                content="resposta agno",
                metrics=RunMetrics(input_tokens=40, output_tokens=15),
            )

    monkeypatch.setattr(
        AgnoRuntimeAdapter, "_runtime_for_request", lambda *a, **k: ControlledAgent()
    )

    task = Task(name="t1", prompt="oi")
    result = _run_agno(task, _fake_cfg(), tmp_path)

    assert result.executor == "agno"
    assert result.output == "resposta agno"
    assert result.prompt_tokens == 40
    assert result.completion_tokens == 15
    assert result.cost_usd >= 0.0
    assert result.error == ""


def test_run_comparison_builds_one_row_per_task(monkeypatch):
    fake_tasks = (Task(name="a", prompt="p1"), Task(name="b", prompt="p2"))

    def fake_native(task, cfg, workspace):
        return ExecutionResult(executor="bauer_native", task_name=task.name, cost_usd=0.001)

    def fake_agno(task, cfg, workspace):
        return ExecutionResult(executor="agno", task_name=task.name, cost_usd=0.002)

    monkeypatch.setattr("evals.compare_executors.runner._run_bauer_native", fake_native)
    monkeypatch.setattr("evals.compare_executors.runner._run_agno", fake_agno)

    report = run_comparison(_fake_cfg(), tasks=fake_tasks)

    assert [row.task_name for row in report.rows] == ["a", "b"]
    assert all(row.quality_verdict == "" for row in report.rows)
    assert report.to_dict()["rows"][0]["bauer_native"]["cost_usd"] == 0.001


def test_formatar_shows_pending_quality_verdict_when_unset():
    row = ComparisonRow(
        task_name="t1",
        bauer_native=ExecutionResult(executor="bauer_native", task_name="t1"),
        agno=ExecutionResult(executor="agno", task_name="t1"),
    )
    text = formatar(Report(rows=[row]))
    assert "pendente" in text
    assert "t1" in text


def test_formatar_shows_error_when_execution_failed():
    row = ComparisonRow(
        task_name="t1",
        bauer_native=ExecutionResult(executor="bauer_native", task_name="t1", error="RuntimeError: falhou"),
        agno=ExecutionResult(executor="agno", task_name="t1"),
    )
    text = formatar(Report(rows=[row]))
    assert "ERRO" in text
    assert "falhou" in text


def test_formatar_includes_response_text_for_manual_quality_review():
    """Sem o texto aqui, revisão pareada exigiria abrir o JSON à parte."""
    row = ComparisonRow(
        task_name="t1",
        bauer_native=ExecutionResult(executor="bauer_native", task_name="t1", output="resposta nativa"),
        agno=ExecutionResult(executor="agno", task_name="t1", output="resposta do agno"),
    )
    text = formatar(Report(rows=[row]))
    assert "resposta nativa" in text
    assert "resposta do agno" in text
