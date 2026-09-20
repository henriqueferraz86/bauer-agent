from pathlib import Path

import pytest

from bauer.core.events import EventBus
from bauer.core.runtime import (
    DelegationManager,
    RunManager,
    RuntimeAgentRegistry,
    TeamRegistry,
    TeamRunManager,
)


def _write_agent(root: Path, agent_id: str) -> None:
    path = root / agent_id / "agent.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"""id: {agent_id}
name: {agent_id}
version: 1.0.0
runtime_adapter: bauer_native
permissions: [runtime.execute]
""",
        encoding="utf-8",
    )


def _manager(tmp_path: Path) -> tuple[TeamRegistry, TeamRunManager]:
    agents_root = tmp_path / "agents"
    for agent_id in ("architect", "backend", "qa"):
        _write_agent(agents_root, agent_id)
    teams_root = tmp_path / "teams"
    team_file = teams_root / "factory" / "team.yaml"
    team_file.parent.mkdir(parents=True)
    team_file.write_text(
        """team:
  id: team.factory
  name: Software Factory
  coordinator:
    agent: architect
  members:
    - architect
    - backend
    - qa
""",
        encoding="utf-8",
    )
    registry = RuntimeAgentRegistry([agents_root])
    teams = TeamRegistry([teams_root], agent_registry=registry)
    bus = EventBus(root=tmp_path / "runtime")
    delegation = DelegationManager(
        root=tmp_path / "runtime",
        team_registry=teams,
        agent_registry=registry,
        run_manager=RunManager(root=tmp_path / "runtime", event_bus=bus, agent_registry=registry),
        event_bus=bus,
    )
    return teams, TeamRunManager(root=tmp_path / "runtime", delegation_manager=delegation, event_bus=bus)


def test_new_team_shape_is_normalized(tmp_path: Path):
    teams, _ = _manager(tmp_path)

    team = teams.get("team.factory")
    assert team is not None
    assert team.coordinator == "architect"
    assert team.members == ["architect", "backend", "qa"]
    assert team.agents == team.members
    assert team.coordination["supervisor"] == "architect"


def test_team_graph_delegates_ready_tasks_and_unlocks_successors(tmp_path: Path):
    _, manager = _manager(tmp_path)
    run = manager.start_team(
        team_id="team.factory",
        objective="entregar uma feature",
        tasks=[
            {"id": "backend", "agent": "backend", "objective": "implementar API"},
            {"id": "qa", "agent": "qa", "objective": "validar API", "depends_on": ["backend"]},
        ],
    )

    tasks = {task.id: task for task in manager.list_tasks(run.id)}
    assert tasks["backend"].status == "queued"
    assert tasks["qa"].status == "pending"
    assert run.status == "running"

    completed = manager.complete_task(run.id, "backend", output={"ok": True})
    assert completed.status == "completed"
    tasks = {task.id: task for task in manager.list_tasks(run.id)}
    assert tasks["qa"].status == "queued"
    assert manager.get_run(run.id).status == "running"

    manager.complete_task(run.id, "qa", output={"tests": "green"})
    assert manager.get_run(run.id).status == "completed"
    event_types = [event.event_type for event in manager.event_bus.list_events()]
    assert "task.delegated" in event_types
    assert "agent.finished" in event_types


def test_team_graph_rejects_unknown_dependency_and_cycle(tmp_path: Path):
    _, manager = _manager(tmp_path)

    with pytest.raises(ValueError, match="unknown dependency"):
        manager.start_team(
            team_id="team.factory",
            objective="x",
            tasks=[{"id": "qa", "agent": "qa", "depends_on": ["missing"]}],
        )

    with pytest.raises(ValueError, match="cycle"):
        manager.start_team(
            team_id="team.factory",
            objective="x",
            tasks=[
                {"id": "a", "agent": "backend", "depends_on": ["b"]},
                {"id": "b", "agent": "qa", "depends_on": ["a"]},
            ],
        )

