from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bauer.cli import app
from bauer.core.events import EventBus
from bauer.core.policy import PolicyEngine
from bauer.core.runtime import AgentRegistry, DelegationManager, RunManager, TeamRegistry


def _write_agent(path: Path, agent_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
id: {agent_id}
name: {agent_id}
version: 0.1.0
description: Test agent.
runtime_adapter: bauer_native
model:
  provider: local
  name: fake
skills: []
permissions:
  - runtime.execute
autonomy:
  mode: supervised
limits:
  max_runtime_s: 100
  max_tool_calls: 20
""".strip(),
        encoding="utf-8",
    )


def _write_team(path: Path, *, budget: float = 3.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
id: team.dev
name: Dev Team
agents:
  - agent.product
  - agent.dev
  - agent.qa
coordination:
  mode: supervisor
  supervisor: agent.product
limits:
  max_parallel_runs: 3
  max_daily_budget_usd: {budget}
""".strip(),
        encoding="utf-8",
    )


def _manager(tmp_path: Path, *, budget: float = 3.0, policy: PolicyEngine | None = None) -> DelegationManager:
    agents_root = tmp_path / "agents"
    teams_root = tmp_path / "teams"
    for agent_id in ("agent.product", "agent.dev", "agent.qa"):
        _write_agent(agents_root / agent_id / "agent.yaml", agent_id)
    _write_team(teams_root / "team.dev" / "team.yaml", budget=budget)
    agent_registry = AgentRegistry([agents_root])
    team_registry = TeamRegistry([teams_root], agent_registry=agent_registry)
    bus = EventBus(root=tmp_path / "runtime")
    run_manager = RunManager(root=tmp_path / "runtime", event_bus=bus, agent_registry=agent_registry)
    return DelegationManager(
        root=tmp_path / "runtime",
        agent_registry=agent_registry,
        team_registry=team_registry,
        run_manager=run_manager,
        policy_engine=policy,
        event_bus=bus,
    )


def test_builtin_software_team_is_registered():
    team = TeamRegistry().get("bauer.software_team")

    assert team is not None
    assert team.coordination["supervisor"] == "bauer.product"
    assert "bauer.dev" in team.agents
    assert team.limits["max_daily_budget_usd"] == 3.0


def test_agent_can_delegate_and_delegation_creates_event_and_run(tmp_path):
    manager = _manager(tmp_path)

    record = manager.delegate(
        team_id="team.dev",
        from_agent_id="agent.product",
        to_agent_id="agent.dev",
        input={"message": "implemente"},
        estimated_cost_usd=0.25,
        session_id="session-1",
    )

    assert record.status == "accepted"
    assert record.run_id
    run = manager.run_manager.get_run(record.run_id)
    assert run is not None
    assert run.agent_id == "agent.dev"
    assert run.input["delegated_by"] == "agent.product"
    events = [event.event_type for event in manager.event_bus.list_events()]
    assert "delegation.requested" in events
    assert "delegation.accepted" in events


def test_non_supervisor_cannot_delegate_in_supervisor_mode(tmp_path):
    manager = _manager(tmp_path)

    record = manager.delegate(
        team_id="team.dev",
        from_agent_id="agent.dev",
        to_agent_id="agent.qa",
        input={"message": "teste"},
    )

    assert record.status == "denied"
    assert "only supervisor" in str(record.reason)
    assert "delegation.denied" in [event.event_type for event in manager.event_bus.list_events()]


def test_delegation_to_agent_outside_team_is_denied(tmp_path):
    manager = _manager(tmp_path)

    record = manager.delegate(
        team_id="team.dev",
        from_agent_id="agent.product",
        to_agent_id="agent.outside",
        input={"message": "fora"},
    )

    assert record.status == "denied"
    assert record.reason == "target agent is not part of team"


def test_team_daily_budget_blocks_delegation(tmp_path):
    manager = _manager(tmp_path, budget=1.0)
    manager.record_team_cost(team_id="team.dev", run_id="old", agent_id="agent.dev", cost_usd=0.90)

    record = manager.delegate(
        team_id="team.dev",
        from_agent_id="agent.product",
        to_agent_id="agent.qa",
        estimated_cost_usd=0.20,
    )

    assert record.status == "denied"
    assert "team budget exceeded" in str(record.reason)
    status = manager.team_budget_status("team.dev")
    assert status["used_usd"] == 0.9
    assert status["remaining_usd"] == 0.1


def test_policy_can_deny_delegation(tmp_path):
    policy = PolicyEngine(rules=[{"id": "delegation.deny.test", "operation": "agent.delegate", "action": "deny"}])
    manager = _manager(tmp_path, policy=policy)

    record = manager.delegate(
        team_id="team.dev",
        from_agent_id="agent.product",
        to_agent_id="agent.dev",
    )

    assert record.status == "denied"
    assert record.reason == "matched policy rule delegation.deny.test"


def test_runtime_teams_cli_lists_shows_delegates_and_budget(tmp_path):
    runner = CliRunner()

    listed = runner.invoke(app, ["runtime", "teams", "list"])
    shown = runner.invoke(app, ["runtime", "teams", "show", "bauer.software_team"])
    delegated = runner.invoke(
        app,
        [
            "runtime",
            "teams",
            "delegate",
            "bauer.software_team",
            "bauer.product",
            "bauer.dev",
            "implemente",
            "--state-dir",
            str(tmp_path / "runtime"),
        ],
    )
    budget = runner.invoke(app, ["runtime", "teams", "budget", "bauer.software_team", "--state-dir", str(tmp_path / "runtime")])

    assert listed.exit_code == 0
    assert "bauer.software_team" in listed.output
    assert shown.exit_code == 0
    assert '"supervisor": "bauer.product"' in shown.output
    assert delegated.exit_code == 0
    assert "delegated" in delegated.output
    assert budget.exit_code == 0
    assert '"team_id": "bauer.software_team"' in budget.output
