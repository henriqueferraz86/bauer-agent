from __future__ import annotations

from dataclasses import dataclass

import pytest

from bauer.chat_team_routing import (
    TeamRoutingDecision,
    project_scoped_team_task,
    resolve_team_intent,
)


@dataclass(frozen=True)
class FakeTeam:
    id: str
    name: str


class FakeTeamRegistry:
    def __init__(self, *teams: FakeTeam):
        self._teams = teams

    def list(self) -> list[FakeTeam]:
        return list(self._teams)


SOFTWARE_TEAM = FakeTeam("bauer.software_team", "Bauer Software Team")


def test_project_task_includes_existing_project_context_and_blocks_app_factory():
    task = project_scoped_team_task(
        "inicie a fase 11", "Investimentos OS", "/workspace/investimentosOS",
    )

    assert "PROJETO SELECIONADO: Investimentos OS" in task
    assert "WORKSPACE RAIZ: /workspace/investimentosOS" in task
    assert "docs/PROGRESS.md" in task
    assert "Não chame app_factory_init" in task
    assert "inicie a fase 11" in task


@pytest.mark.parametrize(
    ("message", "requested_agents"),
    [
        ("Henrique, use 3 agentes e analise o projeto.", 3),
        ("Use três agentes para revisar isto.", 3),
        ("Use múltiplos agentes para investigar.", None),
        ("Use o time para resolver esta tarefa.", None),
        ("Inicie a fase 11 com um time de agentes.", None),
        ("Trabalhe em equipe e entregue uma conclusão.", None),
        ("Use 4 agents to review this project.", 4),
        ("Use multiple agents for this investigation.", None),
        ("Work as a team and consolidate the findings.", None),
    ],
)
def test_explicit_pt_en_intent_uses_the_only_formal_team(message, requested_agents):
    decision = resolve_team_intent(message, FakeTeamRegistry(SOFTWARE_TEAM))

    assert decision == TeamRoutingDecision(
        team_id="bauer.software_team",
        requested_agents=requested_agents,
    )


def test_explicit_team_id_wins_with_multiple_teams():
    registry = FakeTeamRegistry(
        FakeTeam("team.research", "Research Crew"),
        SOFTWARE_TEAM,
    )

    decision = resolve_team_intent("Use o time bauer.software_team com 3 agentes.", registry)

    assert decision == TeamRoutingDecision("bauer.software_team", 3)


def test_explicit_team_name_is_case_and_accent_insensitive():
    registry = FakeTeamRegistry(
        FakeTeam("team.revisao", "Equipe de Revisão"),
        SOFTWARE_TEAM,
    )

    decision = resolve_team_intent("UTILIZE A EQUIPE DE REVISAO para isso.", registry)

    assert decision == TeamRoutingDecision("team.revisao")


def test_id_has_precedence_over_a_different_team_name():
    registry = FakeTeamRegistry(
        FakeTeam("team.alpha", "bauer.software_team"),
        SOFTWARE_TEAM,
    )

    decision = resolve_team_intent("Use bauer.software_team para a tarefa.", registry)

    assert decision == TeamRoutingDecision("bauer.software_team")


@pytest.mark.parametrize(
    "message",
    [
        "Analise o projeto atual.",
        "Explique o que é um time de agentes.",
        "Quantos agentes existem no Bauer Software Team?",
        "Hoje conversei com vários agentes.",
        "Do not use the team; answer directly.",
        "Não use agentes, responda sozinho.",
        "Use 1 agent to answer.",
    ],
)
def test_common_or_negated_conversation_does_not_select_a_team(message):
    assert resolve_team_intent(message, FakeTeamRegistry(SOFTWARE_TEAM)) is None


def test_fallback_is_not_used_when_multiple_teams_exist():
    registry = FakeTeamRegistry(
        SOFTWARE_TEAM,
        FakeTeam("team.research", "Research Team"),
    )

    assert resolve_team_intent("Use 3 agentes para analisar.", registry) is None


def test_no_registered_team_returns_no_decision():
    assert resolve_team_intent("Use the team for this task.", FakeTeamRegistry()) is None


def test_unrelated_tool_negation_does_not_cancel_team_request():
    decision = resolve_team_intent(
        "Use 3 agentes para analisar isto. Não use ferramentas.",
        FakeTeamRegistry(SOFTWARE_TEAM),
    )

    assert decision == TeamRoutingDecision("bauer.software_team", 3)


def test_explicit_name_without_generic_team_word_can_be_resolved():
    registry = FakeTeamRegistry(
        FakeTeam("team.research", "Research Crew"),
        SOFTWARE_TEAM,
    )

    decision = resolve_team_intent("Please use Research Crew for this investigation.", registry)

    assert decision == TeamRoutingDecision("team.research")
