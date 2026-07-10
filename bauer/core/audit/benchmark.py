"""Scenario-driven end-to-end benchmarks for the Bauer runtime."""

from __future__ import annotations

import platform
from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .run_auditor import audit_run
from .schemas import BenchmarkResult, BenchmarkScenario, BenchmarkSuiteReport
from .score import score_run

ScenarioExecutor = Callable[[BenchmarkScenario, Path], str]


def load_benchmark_scenarios(scenarios_dir: str | Path) -> list[BenchmarkScenario]:
    """Load and validate benchmark YAML files."""
    root = Path(scenarios_dir)
    scenarios: list[BenchmarkScenario] = []
    if not root.exists():
        return scenarios
    for path in sorted([*root.glob("*.yaml"), *root.glob("*.yml")]):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: benchmark must be a mapping")
        expected = raw.get("expected") or {}
        if not isinstance(expected, dict):
            raise ValueError(f"{path}: expected must be a mapping")
        scenario_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        if not scenario_id or not name or not prompt:
            raise ValueError(f"{path}: id, name and prompt are required")
        scenarios.append(BenchmarkScenario(
            id=scenario_id,
            name=name,
            prompt=prompt,
            expected_files=_strings(expected.get("files")),
            expected_commands=_strings(expected.get("commands")),
            expected_events=_strings(expected.get("events")),
            min_score=int(raw.get("min_score", expected.get("min_score", 4))),
            platforms=_strings(raw.get("platforms")),
        ))
    return scenarios


def run_benchmark_suite(
    runtime_root: str | Path,
    scenarios: Iterable[BenchmarkScenario],
    execute: ScenarioExecutor,
    *,
    workspace_root: str | Path,
) -> BenchmarkSuiteReport:
    """Execute scenarios, audit their runs and persist a repeatable report."""
    from ..events import EventBus
    from ..runtime.state_store import JsonlStateStore

    root = Path(runtime_root)
    suite = BenchmarkSuiteReport(
        id=f"benchmark-{uuid4()}",
        started_at=datetime.now(UTC).isoformat(),
    )
    current_platform = platform.system().lower()

    for scenario in scenarios:
        if scenario.platforms and current_platform not in {item.lower() for item in scenario.platforms}:
            continue
        workspace = Path(workspace_root) / suite.id / scenario.id
        workspace.mkdir(parents=True, exist_ok=True)
        result = BenchmarkResult(
            scenario_id=scenario.id,
            scenario_name=scenario.name,
            min_score=scenario.min_score,
        )
        try:
            result.run_id = execute(scenario, workspace)
            audited = audit_run(root, result.run_id, include_events=True, include_tools=True)
            if audited is None:
                raise RuntimeError(f"executor returned unknown run_id: {result.run_id}")
            scored = score_run(audited)
            result.score = scored.score
            result.duration_ms = audited.duration_ms
            _evaluate_expectations(result, scenario, audited, workspace, EventBus(root=root))
            if scored.score >= scenario.min_score:
                result.checks.append(f"score {scored.score} >= {scenario.min_score}")
            else:
                result.failures.append(f"score {scored.score} < {scenario.min_score}")
            if audited.status != "completed":
                result.failures.append(f"run status is {audited.status}")
            result.passed = not result.failures
        except Exception as exc:  # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"
            result.failures.append(result.error)
        suite.results.append(result)

    suite.total = len(suite.results)
    suite.passed = sum(1 for item in suite.results if item.passed)
    suite.failed = suite.total - suite.passed
    suite.finished_at = datetime.now(UTC).isoformat()
    JsonlStateStore(root).upsert("benchmark_reports", asdict(suite))
    return suite


def list_benchmark_reports(runtime_root: str | Path, *, limit: int = 20) -> list[dict[str, Any]]:
    from ..runtime.state_store import JsonlStateStore

    records = JsonlStateStore(runtime_root).list_latest("benchmark_reports")
    return list(reversed(records[-max(0, limit):]))


def _evaluate_expectations(result, scenario, audited, workspace: Path, bus) -> None:
    for expected in scenario.expected_files:
        changed = any(_same_path(expected, item) for item in audited.files_changed)
        exists = (workspace / expected).exists()
        if changed or exists:
            result.checks.append(f"file: {expected}")
        else:
            result.failures.append(f"missing file: {expected}")
    for expected in scenario.expected_commands:
        if any(expected.lower() in command.lower() for command in audited.commands_executed):
            result.checks.append(f"command: {expected}")
        else:
            result.failures.append(f"missing command: {expected}")
    event_types = {event.event_type for event in bus.list_events(run_id=result.run_id)}
    for expected in scenario.expected_events:
        if expected in event_types:
            result.checks.append(f"event: {expected}")
        else:
            result.failures.append(f"missing event: {expected}")


def _same_path(expected: str, actual: str) -> bool:
    left = expected.replace("\\", "/").lstrip("./").lower()
    right = actual.replace("\\", "/").lstrip("./").lower()
    return right == left or right.endswith(f"/{left}")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
