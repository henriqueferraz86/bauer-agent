"""Sprint 64 — custo de setup do turno governado e hermeticidade do boot.

A causa comum das falhas do CI Windows era o trabalho feito ANTES do turno:
cada listagem de times relia e re-parseava todos os specs de agent uma vez por
agent do time (O(times × agents) parses por turno, em PyYAML puro). O boot do
servidor também rodava `docker ps -a` a cada `create_app`. As asserções aqui
contam trabalho, não medem relógio.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bauer.core.runtime.agent_registry import RuntimeAgentRegistry
from bauer.core.runtime.team_registry import TeamRegistry, TeamRegistryError


def _write_agent(root: Path, agent_id: str) -> Path:
    path = root / agent_id / "agent.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({
        "id": agent_id, "name": agent_id.title(), "version": "0.1.0",
        "runtime_adapter": "bauer_native", "permissions": ["filesystem.read"],
    }), encoding="utf-8")
    return path


def _write_team(root: Path, team_id: str, agents: list[str]) -> Path:
    path = root / team_id / "team.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"id": team_id, "name": team_id, "agents": agents}),
                    encoding="utf-8")
    return path


@pytest.fixture
def registries(tmp_path):
    agents_root, teams_root = tmp_path / "agents", tmp_path / "teams"
    ids = [f"agent{i}" for i in range(6)]
    for agent_id in ids:
        _write_agent(agents_root, agent_id)
    _write_team(teams_root, "alpha", ids[:4])
    _write_team(teams_root, "beta", ids[2:])
    agents = RuntimeAgentRegistry(roots=[agents_root])
    return TeamRegistry(roots=[teams_root], agent_registry=agents), agents_root, teams_root


@pytest.fixture
def parse_counter(monkeypatch):
    calls: list[int] = []
    original = yaml.load

    def counting(stream, Loader):  # noqa: N803 - mirrors yaml.load
        calls.append(1)
        return original(stream, Loader=Loader)

    monkeypatch.setattr(yaml, "load", counting)
    return calls


def test_team_listing_parses_each_spec_at_most_once(registries, parse_counter):
    teams, _agents_root, _teams_root = registries
    # Conteúdo único por teste: o cache é por conteúdo e não pode mascarar a
    # contagem com specs idênticos de outro teste.
    assert [team.id for team in teams.list()] == ["alpha", "beta"]
    # 6 agents + 2 times. Antes: 1 + 8 listagens completas de agents = 50 parses.
    assert len(parse_counter) <= 8


def test_repeated_listing_does_not_reparse_unchanged_specs(registries, parse_counter):
    teams, _agents_root, _teams_root = registries
    teams.list()
    first = len(parse_counter)
    for _ in range(5):
        teams.list()
    assert len(parse_counter) == first


def test_edited_spec_is_seen_on_next_listing(registries):
    teams, agents_root, teams_root = registries
    assert len(teams.get("alpha").agents) == 4
    _write_agent(agents_root, "agent-late")
    path = teams_root / "alpha" / "team.yaml"
    path.write_text(yaml.safe_dump({"id": "alpha", "name": "alpha",
                                    "agents": ["agent0", "agent-late"]}), encoding="utf-8")
    assert teams.get("alpha").agents == ["agent0", "agent-late"]
    path.write_text(yaml.safe_dump({"id": "alpha", "name": "alpha", "agents": ["ghost"]}),
                    encoding="utf-8")
    with pytest.raises(TeamRegistryError, match="unknown agents: ghost"):
        teams.list()


def test_cached_spec_is_not_shared_between_callers(registries):
    _teams, agents_root, _teams_root = registries
    registry = RuntimeAgentRegistry(roots=[agents_root])
    spec = registry.get("agent0")
    spec.permissions.append("shell.execute")
    assert registry.get("agent0").permissions == ["filesystem.read"]


def test_docker_discovery_can_be_disabled_without_spawning(monkeypatch):
    import bauer.continuous_autonomy as ca

    def explode(*_a, **_k):
        raise AssertionError("docker must not be spawned")

    monkeypatch.setattr(ca.subprocess, "run", explode)
    monkeypatch.setenv("BAUER_DOCKER_DISCOVERY", "0")
    assert ca.discover_docker_containers() is None
    # An explicit runner (the inventory tests) is still honoured.
    class Done:
        returncode, stdout = 0, '{"Names": "web", "ID": "1", "Image": "x", "State": "running"}\n'

    assert ca.discover_docker_containers(runner=lambda *a, **k: Done())[0]["name"] == "web"


def test_suite_boot_is_hermetic_for_docker():
    import os

    assert os.environ.get("BAUER_DOCKER_DISCOVERY") == "0"
