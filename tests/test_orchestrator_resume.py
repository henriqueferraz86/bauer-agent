"""Robustness regression tests for persisted orchestrator progress."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from bauer.orchestrator import AgentOrchestrator, OrchestratorConfig, StepResult


def _orchestrator(tmp_path: Path, console: Console | None = None) -> AgentOrchestrator:
    client = MagicMock()
    router = MagicMock()
    model_router = MagicMock()
    orchestrator = AgentOrchestrator(
        client,
        router,
        model_router,
        OrchestratorConfig(),
        console=console,
    )
    orchestrator._progress_path = lambda task: tmp_path / "progress"  # type: ignore[method-assign]
    return orchestrator


def _result_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "id": 1,
        "goal": "goal",
        "model_used": "model",
        "response": "response",
        "tool_log": [],
        "timestamp": 1.0,
    }
    data.update(overrides)
    return data


def test_step_result_from_dict_ignores_extra_keys():
    result = StepResult.from_dict(_result_data(future_field="compatible"))
    assert result.id == 1
    assert result.goal == "goal"


@pytest.mark.parametrize(
    "data",
    [
        {"goal": "missing id"},
        _result_data(id=True),
        _result_data(tool_log={"tool": "not-a-list"}),
    ],
)
def test_step_result_from_dict_rejects_invalid_structures(data: dict[str, object]):
    with pytest.raises(ValueError):
        StepResult.from_dict(data)


def test_load_progress_skips_corrupt_and_incompatible_files(tmp_path: Path):
    output = StringIO()
    orchestrator = _orchestrator(tmp_path, Console(file=output, force_terminal=False))
    progress = orchestrator._progress_path("task")
    progress.mkdir()
    (progress / "step_001.json").write_text(json.dumps(_result_data()), encoding="utf-8")
    (progress / "step_002.json").write_text("{corrompido", encoding="utf-8")
    (progress / "step_003.json").write_text(
        json.dumps(_result_data(tool_log={"unexpected": "mapping"})), encoding="utf-8",
    )

    loaded = orchestrator.load_progress("task")

    assert [result.id for result in loaded] == [1]
    assert output.getvalue().count("ignorando progresso corrompido") == 2


def test_circular_dag_falls_back_and_warns(tmp_path: Path):
    output = StringIO()
    orchestrator = _orchestrator(tmp_path, Console(file=output, force_terminal=False))
    steps = [
        {"id": 1, "goal": "a", "depends_on": [2]},
        {"id": 2, "goal": "b", "depends_on": [1]},
    ]

    batches = orchestrator._topological_batches(steps)

    assert [[step["id"] for step in batch] for batch in batches] == [[1], [2]]
    assert "dependência circular" in output.getvalue()


def test_circular_dag_without_console_stays_safe(tmp_path: Path):
    orchestrator = _orchestrator(tmp_path)
    batches = orchestrator._topological_batches([
        {"id": 1, "goal": "a", "depends_on": [1]},
    ])
    assert [[step["id"] for step in batch] for batch in batches] == [[1]]
