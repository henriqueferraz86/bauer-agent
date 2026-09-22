"""Contrato dos comandos de teams no agente e no desktop server."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from rich.console import Console  # noqa: E402

from bauer import desktop_api  # noqa: E402
from bauer.core.kernel.entry import GovernedResult  # noqa: E402
from bauer.core.runtime.team_registry import TeamRegistry  # noqa: E402


def _console() -> Console:
    return Console(record=True, width=140)


def test_team_slash_lists_and_shows_formal_team():
    from bauer.agent import _handle_team_cmd

    console = _console()
    _handle_team_cmd("/teams", console)
    _handle_team_cmd("/team show bauer.software_team", console)

    output = console.export_text()
    assert "bauer.software_team" in output
    assert "bauer.product" in output
    assert "bauer.dev" in output
    assert "bauer.qa" in output
    assert "bauer.devops" in output


def test_team_slash_run_uses_orchestrator(monkeypatch):
    from bauer.agent import _handle_team_cmd

    @dataclass
    class FakeOrchestrator:
        kernel: object

        def __init__(self, **kwargs):
            self.kernel = kwargs["kernel"]
            self.team_registry = TeamRegistry()

        def run(self, team_id, task, **kwargs):
            assert team_id == "bauer.software_team"
            assert task == "verificar o contrato"
            return GovernedResult(
                status="completed", run_id="run-team-test", output="ok", governed=True,
            )

    kernel = SimpleNamespace(runs=SimpleNamespace(store=SimpleNamespace(root=Path("memory/runtime"))))
    console = _console()
    with patch("bauer.core.runtime.agno_team_orchestrator.AgnoTeamOrchestrator", FakeOrchestrator):
        _handle_team_cmd("/team run bauer.software_team verificar o contrato", console, kernel=kernel)

    output = console.export_text()
    assert "run-team-test" in output
    assert "ok" in output


@pytest.fixture
def team_client(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("model:\n  provider: opencode\n  name: deepseek\n", encoding="utf-8")
    app = FastAPI()
    app.include_router(desktop_api.build_desktop_router(
        get_config_path=lambda: config,
        get_workspace=lambda: tmp_path,
        runtime_root=tmp_path / "runtime",
        logs_dir=tmp_path / "logs",
    ))
    return TestClient(app)


def test_team_api_lists_members_and_budget(team_client):
    response = team_client.get("/api/teams")
    assert response.status_code == 200
    payload = response.json()
    team = next(item for item in payload["teams"] if item["id"] == "bauer.software_team")
    assert {member["id"] for member in team["members"]} == {
        "bauer.product", "bauer.dev", "bauer.qa", "bauer.devops",
        "bauer.architect", "bauer.security", "bauer.research", "bauer.docs", "bauer.data",
    }
    budget = team_client.get("/api/teams/bauer.software_team/budget")
    assert budget.status_code == 200
    assert budget.json()["team_id"] == "bauer.software_team"


def test_team_api_validates_unknown_team_and_missing_task(team_client):
    assert team_client.get("/api/teams/does-not-exist").status_code == 404
    missing = team_client.post("/api/teams/bauer.software_team/runs", json={})
    assert missing.status_code == 422


def test_team_api_run_serializes_governed_result(team_client):
    class FakeOrchestrator:
        def __init__(self, **kwargs):
            self.team_registry = TeamRegistry()

        def run(self, team_id, task, **kwargs):
            assert task == "rodar smoke"
            return GovernedResult(
                status="completed", run_id="run-api-test", output={"answer": "ok"}, governed=True,
            )

    with patch("bauer.core.runtime.agno_team_orchestrator.AgnoTeamOrchestrator", FakeOrchestrator):
        response = team_client.post(
            "/api/teams/bauer.software_team/runs", json={"task": "rodar smoke"},
        )
    assert response.status_code == 200
    assert response.json()["run_id"] == "run-api-test"
    assert response.json()["ok"] is True
