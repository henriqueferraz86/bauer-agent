"""Pure, deterministic routing for explicit chat team requests.

The registry is injected so importing this module never reads team specs from
disk.  Callers decide which concrete ``TeamRegistry`` instance to provide.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Iterable, Protocol


class TeamReference(Protocol):
    """Minimum team data required by the chat router."""

    id: str
    name: str


class TeamRegistryLike(Protocol):
    """Injectable subset of ``TeamRegistry`` used by this module."""

    def list(self) -> Iterable[TeamReference]: ...


@dataclass(frozen=True, slots=True)
class TeamRoutingDecision:
    """Team selected for one chat turn."""

    team_id: str
    requested_agents: int | None = None


# Compatibility-friendly descriptive alias for callers that prefer the module
# context in the type name.
ChatTeamRoutingDecision = TeamRoutingDecision


_AGENT_WORDS = r"(?:agente|agentes|agent|agents)"
_TEAM_WORDS = r"(?:time|equipe|team)"
_REQUEST_VERBS = (
    r"use|utilize|empregue|chame|convoque|coordene|orquestre|delegue|"
    r"work\s+(?:with|as)|collaborate\s+with|employ|assemble|coordinate|"
    r"orchestrate|delegate\s+to"
)
_MULTIPLE_WORDS = r"(?:multiplos|varios|diversos|multiple|several)"
_COUNT_WORDS = (
    r"(?:dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|"
    r"two|three|four|five|six|seven|eight|nine|ten)"
)
_NEGATED_REQUEST = re.compile(
    rf"\b(?:nao|nunca|sem|do\s+not|don't|dont|never|without)\s+"
    rf"(?:{_REQUEST_VERBS})\b(?:\s+\w+){{0,3}}\s+(?:{_TEAM_WORDS}|{_AGENT_WORDS})\b",
)
_TEAM_REQUEST = re.compile(
    rf"\b(?:{_REQUEST_VERBS})\b(?:\s+\w+){{0,4}}\s+"
    rf"(?:o\s+|a\s+|um\s+|uma\s+|the\s+|a\s+)?{_TEAM_WORDS}\b",
)
_MULTI_AGENT_REQUEST = re.compile(
    rf"\b(?:{_REQUEST_VERBS})\b(?:\s+\w+){{0,4}}\s+"
    rf"(?:{_MULTIPLE_WORDS}|{_COUNT_WORDS}|\d+)\s+{_AGENT_WORDS}\b",
)
_TEAMWORK_REQUEST = re.compile(
    r"\b(?:trabalhe|trabalhar|atue|atuar|work|collaborate)\s+"
    r"(?:como|em|com|as|in|with)\s+(?:um\s+|uma\s+|a\s+)?"
    rf"{_TEAM_WORDS}\b",
)
_ACTION_REQUEST = re.compile(rf"\b(?:{_REQUEST_VERBS})\b")
_REQUESTED_AGENT_COUNT = re.compile(
    rf"\b(?P<count>\d+|{_COUNT_WORDS})\s+{_AGENT_WORDS}\b",
)
_NUMBER_WORDS = {
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
    "sete": 7,
    "oito": 8,
    "nove": 9,
    "dez": 10,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def resolve_team_intent(
    message: str,
    team_registry: TeamRegistryLike,
) -> TeamRoutingDecision | None:
    """Resolve an explicit PT/EN team request against a formal registry.

    Explicit team ids take precedence over explicit names.  When no team is
    named, the sole registered team is selected as a deterministic fallback.
    No decision is returned for ordinary conversation or ambiguous fallback.
    """

    text = _normalize(message)
    if not text:
        return None

    teams = sorted(team_registry.list(), key=lambda team: str(team.id))
    explicit_team = _match_explicit_team(text, teams)
    requested_agents = _requested_agents(text)
    explicit_intent = _has_explicit_team_intent(text, requested_agents)

    # A direct request to use a formal id/name is explicit even when its name
    # does not contain a generic word such as "team" or "equipe".
    if explicit_team is not None and _has_positive_action(text):
        explicit_intent = True

    if not explicit_intent:
        return None
    if explicit_team is not None:
        return TeamRoutingDecision(
            team_id=str(explicit_team.id),
            requested_agents=requested_agents,
        )
    if len(teams) == 1:
        return TeamRoutingDecision(
            team_id=str(teams[0].id),
            requested_agents=requested_agents,
        )
    return None


def _has_explicit_team_intent(text: str, requested_agents: int | None) -> bool:
    if _NEGATED_REQUEST.search(text):
        return False
    if _TEAM_REQUEST.search(text) or _TEAMWORK_REQUEST.search(text):
        return True
    if requested_agents is not None and _MULTI_AGENT_REQUEST.search(text):
        return True
    return bool(re.search(rf"\b(?:{_REQUEST_VERBS})\b.*\b{_MULTIPLE_WORDS}\s+{_AGENT_WORDS}\b", text))


def _has_positive_action(text: str) -> bool:
    return _NEGATED_REQUEST.search(text) is None and _ACTION_REQUEST.search(text) is not None


def _match_explicit_team(text: str, teams: Iterable[TeamReference]) -> TeamReference | None:
    team_list = list(teams)
    for attribute in ("id", "name"):
        candidates = sorted(
            team_list,
            key=lambda team: (-len(_normalize(str(getattr(team, attribute)))), str(team.id)),
        )
        for team in candidates:
            value = _normalize(str(getattr(team, attribute)))
            if value and re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text):
                return team
    return None


def _requested_agents(text: str) -> int | None:
    match = _REQUESTED_AGENT_COUNT.search(text)
    if match is None:
        return None
    raw = match.group("count")
    count = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
    return count if count >= 2 else None


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value).casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.split())


__all__ = [
    "ChatTeamRoutingDecision",
    "TeamRegistryLike",
    "TeamRoutingDecision",
    "resolve_team_intent",
]
