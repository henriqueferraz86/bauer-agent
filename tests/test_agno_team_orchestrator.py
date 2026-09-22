from __future__ import annotations

from pathlib import Path
from typing import Any

from bauer.config_loader import RuntimeSection
from bauer.core.events import EventBus
from bauer.core.kernel import BauerKernel
from bauer.core.runtime import (
    AgnoTeamOrchestrator,
    RunManager,
    RuntimeAgentRegistry,
    TeamRegistry,
)


class FakeTeamAdapter:
    name = "agno"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def run_team(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "status": "completed",
            "event": "run.completed",
            "run_id": request["run_id"],
            "runtime_adapter": self.name,
            "output": "team completed",
            "cost_estimate": 0.2,
        }


def _write_agent(path: Path, agent_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
id: {agent_id}
name: {agent_id}
version: 0.1.0
runtime_adapter: agno
model: offline-echo
provider: local
permissions:
  - runtime.execute
""".strip(),
        encoding="utf-8",
    )


def _orchestrator(tmp_path: Path) -> tuple[AgnoTeamOrchestrator, FakeTeamAdapter]:
    agents_root = tmp_path / "agents"
    teams_root = tmp_path / "teams"
    for agent_id in ("agent.product", "agent.dev", "agent.qa"):
        _write_agent(agents_root / agent_id / "agent.yaml", agent_id)
    team_path = teams_root / "team.dev" / "team.yaml"
    team_path.parent.mkdir(parents=True)
    team_path.write_text(
        """
id: team.dev
name: Dev Team
agents: [agent.product, agent.dev, agent.qa]
coordination:
  mode: supervisor
  supervisor: agent.product
limits:
  max_parallel_runs: 2
  max_daily_budget_usd: 3.0
""".strip(),
        encoding="utf-8",
    )
    agent_registry = RuntimeAgentRegistry([agents_root])
    team_registry = TeamRegistry([teams_root], agent_registry=agent_registry)
    bus = EventBus(root=tmp_path / "runtime")
    runs = RunManager(root=tmp_path / "runtime", event_bus=bus, agent_registry=agent_registry)
    kernel = BauerKernel(runs=runs, bus=bus, heartbeat_interval_s=0)
    adapter = FakeTeamAdapter()
    return (
        AgnoTeamOrchestrator(
            kernel=kernel,
            root=tmp_path / "runtime",
            team_registry=team_registry,
            agent_registry=agent_registry,
            adapter=adapter,
        ),
        adapter,
    )


def test_agno_is_the_runtime_default() -> None:
    assert RuntimeSection().default_adapter == "agno"


def test_team_run_is_governed_and_builds_all_members(tmp_path: Path) -> None:
    orchestrator, adapter = _orchestrator(tmp_path)

    result = orchestrator.run("team.dev", "implemente a tarefa")

    assert result.ok
    assert result.governed
    assert result.result["output"] == "team completed"
    assert result.run_id
    request = adapter.requests[0]
    assert request["team_spec"]["mode"] == "coordinate"
    assert [item["id"] for item in request["agent_specs"]] == [
        "agent.product",
        "agent.dev",
        "agent.qa",
    ]
    event_types = [event.event_type for event in orchestrator.event_bus.list_events()]
    assert "team.run.requested" in event_types
    assert "team.run.completed" in event_types


def test_agno_adapter_executes_coordinate_team(tmp_path: Path) -> None:
    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter

    adapter = AgnoRuntimeAdapter(adapter_config={"db_file": str(tmp_path / "agno.db")})
    result = adapter.run_team(
        {
            "team_id": "team.smoke",
            "team_spec": {
                "id": "team.smoke",
                "name": "Smoke Team",
                "mode": "coordinate",
            },
            "agent_specs": [
                {"id": "agent.one", "name": "Agent One"},
                {"id": "agent.two", "name": "Agent Two"},
            ],
            "supervisor_spec": {"id": "agent.one", "name": "Agent One"},
            "task": "responda smoke",
            "session_id": "team-smoke-session",
            "user_id": "test-user",
        }
    )

    assert result["status"] == "completed"
    assert result["runtime_adapter"] == "agno"
    assert "user:responda smoke" in result["output"]
