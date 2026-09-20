from pathlib import Path

import pytest
from typer.testing import CliRunner

from bauer.core.runtime import (
    AgentManager,
    AgentProcess,
    RuntimeAgentRegistry,
)


def test_agents_cli_exposes_runtime_views(tmp_path: Path):
    from bauer.cli import app

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["agents", "running", "--state-dir", str(tmp_path / "runtime")],
    )
    assert result.exit_code == 0
    assert "Bauer Agents" in result.stdout


def _manager(tmp_path: Path) -> AgentManager:
    agents = tmp_path / "agents.yaml"
    agents.write_text(
        """
agents:
  - id: parent
    name: parent
    version: 1.0.0
    runtime_adapter: bauer_native
    permissions: [runtime.execute]
  - id: child
    name: child
    version: 1.0.0
    runtime_adapter: bauer_native
    permissions: [runtime.execute, filesystem.read]
""",
        encoding="utf-8",
    )
    registry = RuntimeAgentRegistry([agents])
    return AgentManager(root=tmp_path / "runtime", agent_registry=registry)


def test_spawn_persists_process_session_and_runtime_state(tmp_path: Path):
    manager = _manager(tmp_path)

    process = manager.spawn_agent(
        agent_id="parent",
        message="coordinate",
        model={"provider": "local", "name": "qwen"},
        budget={"max_cost_usd": 1.5},
        tools=["read_file"],
        permissions=["runtime.execute"],
        workspace=str(tmp_path / "workspace"),
    )

    assert isinstance(process, AgentProcess)
    assert process.status == "running"
    assert process.session_id
    assert process.run_id
    assert process.model == {"provider": "local", "name": "qwen"}
    assert process.budget == {"max_cost_usd": 1.5}
    assert process.tools == ["read_file"]
    assert process.workspace == str(tmp_path / "workspace")
    assert "agent.started" in process.events

    restored = AgentManager(
        root=tmp_path / "runtime",
        agent_registry=manager.agent_registry,
    ).inspect_agent(process.id)
    assert restored is not None
    assert restored.id == process.id
    assert restored.session_id == process.session_id
    assert restored.agent_id == "parent"


def test_mailbox_tree_and_lifecycle_are_durable(tmp_path: Path):
    manager = _manager(tmp_path)
    parent = manager.spawn_agent(agent_id="parent")
    child = manager.spawn_agent(agent_id="child", parent_agent_id=parent.id)

    message = manager.send_agent(
        from_agent_id=parent.id,
        to_agent_id=child.id,
        message="prepare the report",
    )
    assert message.status == "pending"
    assert [item.id for item in manager.mailbox.receive(child.id)] == [message.id]
    assert manager.mailbox.receive(child.id, unread_only=True)[0].id == message.id

    tree = manager.supervisor.tree()
    assert tree[0]["id"] == parent.id
    assert tree[0]["children"][0]["id"] == child.id
    assert [item.id for item in manager.supervisor.running()] == [parent.id, child.id]

    paused = manager.pause_agent(child.id)
    assert paused.status == "paused"
    assert manager.resume_agent(child.id).status == "running"
    assert manager.cancel_agent(child.id).status == "cancelled"
    assert manager.cancel_agent(child.id).status == "cancelled"
    assert manager.wait_agent(child.id).status == "cancelled"


def test_parent_relationship_cannot_cycle_or_target_terminal_process(tmp_path: Path):
    manager = _manager(tmp_path)
    parent = manager.spawn_agent(agent_id="parent")
    child = manager.spawn_agent(agent_id="child", parent_agent_id=parent.id)
    manager.cancel_agent(child.id)

    with pytest.raises(ValueError, match="terminal"):
        manager.send_agent(from_agent_id=parent.id, to_agent_id=child.id, message="late")

    with pytest.raises(ValueError, match="cycle"):
        manager.set_parent(parent.id, child.id)


def test_wait_agent_can_observe_without_blocking(tmp_path: Path):
    manager = _manager(tmp_path)
    process = manager.spawn_agent(agent_id="parent")

    assert manager.wait_agent(process.id, timeout=0).id == process.id
    assert manager.wait_agent("missing", timeout=0) is None
