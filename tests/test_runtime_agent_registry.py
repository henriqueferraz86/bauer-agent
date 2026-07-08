from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bauer.cli import app
from bauer.core.runtime import AgentRegistry, RunManager


def _write_agent(path: Path, *, version: str, permissions: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""
id: bauer.dev
name: Bauer Dev Agent
version: {version}
description: Agente formal de teste.
runtime_adapter: agno
model:
  provider: openrouter
  name: auto
skills:
  - bauer.coding
permissions:
{chr(10).join(f"  - {item}" for item in (permissions or ["filesystem.read"]))}
autonomy:
  mode: supervised
limits:
  max_runtime_s: 900
  max_tool_calls: 300
""".strip(),
        encoding="utf-8",
    )


def test_formal_agent_registry_loads_versioned_specs(tmp_path):
    root = tmp_path / "agents"
    _write_agent(root / "bauer.dev" / "0.1.0.yaml", version="0.1.0", permissions=["filesystem.read"])
    _write_agent(root / "bauer.dev" / "0.2.0.yaml", version="0.2.0", permissions=["filesystem.read", "shell.execute"])

    registry = AgentRegistry([root])

    latest = registry.get("bauer.dev")
    assert latest is not None
    assert latest.version == "0.2.0"
    assert latest.runtime_adapter == "agno"
    assert latest.model == "auto"
    assert latest.provider == "openrouter"
    assert latest.autonomy["mode"] == "supervised"
    assert latest.limits["max_tool_calls"] == 300
    assert registry.versions("bauer.dev") == ["0.1.0", "0.2.0"]
    assert registry.get("bauer.dev", version="0.1.0").permissions == ["filesystem.read"]  # type: ignore[union-attr]


def test_agent_registry_tracks_permissions(tmp_path):
    root = tmp_path / "agents"
    _write_agent(root / "bauer.dev" / "agent.yaml", version="0.1.0", permissions=["filesystem.read", "shell.execute"])

    matches = AgentRegistry([root]).by_permission("shell.execute")

    assert [match.id for match in matches] == ["bauer.dev"]


def test_agent_registry_adapts_legacy_agents_yaml_permissions(tmp_path):
    agents_yaml = tmp_path / "agents.yaml"
    agents_yaml.write_text(
        """
agents:
  - name: legacy-code
    description: Legacy agent.
    system: You are legacy.
    tools: [read_file, run_command, web_search]
""".strip(),
        encoding="utf-8",
    )

    spec = AgentRegistry([agents_yaml]).get("legacy-code")

    assert spec is not None
    assert spec.version == "0.1.0"
    assert "filesystem.read" in spec.permissions
    assert "shell.execute" in spec.permissions
    assert "network.http" in spec.permissions


def test_run_manager_creates_run_from_agent_registry(tmp_path):
    root = tmp_path / "agents"
    _write_agent(root / "bauer.dev" / "agent.yaml", version="0.1.0", permissions=["filesystem.read", "shell.execute"])

    run = RunManager(root=tmp_path / "runtime", agent_registry=AgentRegistry([root])).create_run_for_agent(
        agent_id="bauer.dev",
        session_id="session-1",
        input={"message": "teste"},
    )

    assert run.agent_id == "bauer.dev"
    assert run.runtime_adapter == "agno"
    assert run.input["agent_version"] == "0.1.0"
    assert run.input["permissions"] == ["filesystem.read", "shell.execute"]
    assert run.input["limits"]["max_runtime_s"] == 900


def test_builtin_bauer_dev_agent_is_registered():
    spec = AgentRegistry().get("bauer.dev")

    assert spec is not None
    assert spec.version == "0.1.0"
    assert spec.runtime_adapter == "agno"
    assert "shell.execute" in spec.permissions


def test_runtime_agents_cli_lists_shows_and_runs(tmp_path):
    runner = CliRunner()

    listed = runner.invoke(app, ["runtime", "agents", "list"])
    shown = runner.invoke(app, ["runtime", "agents", "show", "bauer.dev"])
    run = runner.invoke(
        app,
        ["runtime", "agents", "run", "bauer.dev", "smoke", "--state-dir", str(tmp_path / "runtime")],
    )

    assert listed.exit_code == 0
    assert "bauer.dev" in listed.output
    assert shown.exit_code == 0
    assert '"version": "0.1.0"' in shown.output
    assert run.exit_code == 0
    assert "queued" in run.output
