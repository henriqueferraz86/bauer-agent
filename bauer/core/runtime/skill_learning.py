"""Persistent experience extraction and skill proposal generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from ...skill_learning import draft_skill_yaml, find_skill_candidates
from .state_store import RuntimeStateStore, SqliteStateStore


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SkillExperience:
    id: str
    session_id: str
    user_message: str
    outcome: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)


@dataclass
class SkillProposal:
    id: str
    slug: str
    occurrences: int
    examples: list[str] = field(default_factory=list)
    sessions: list[str] = field(default_factory=list)
    draft_yaml: str = ""
    status: str = "pending"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)


class SkillLearningManager:
    """Persist experiences and produce human-reviewable skill proposals."""

    def __init__(self, *, root: str | Path = "memory/runtime", store: RuntimeStateStore | None = None):
        self.store = store or SqliteStateStore(root)

    def record_experience(
        self,
        *,
        session_id: str,
        user_message: str,
        outcome: dict[str, Any] | None = None,
    ) -> SkillExperience:
        message = user_message.strip()
        if not session_id.strip():
            raise ValueError("session_id is required")
        if not message:
            raise ValueError("user_message is required")
        experience = SkillExperience(
            id=f"experience-{uuid4()}",
            session_id=session_id,
            user_message=message,
            outcome=outcome or {},
        )
        self.store.append("skill_experiences", experience)
        return experience

    def propose(self, *, min_occurrences: int = 3, max_sessions: int = 200) -> list[SkillProposal]:
        candidates = find_skill_candidates(
            min_occurrences=min_occurrences,
            max_sessions=max_sessions,
            store=_RuntimeExperienceView(self.store),
        )
        proposals: list[SkillProposal] = []
        for candidate in candidates:
            existing = self.get_proposal(candidate.slug)
            proposal = SkillProposal(
                id=existing.id if existing else f"proposal-{candidate.slug}",
                slug=candidate.slug,
                occurrences=candidate.occurrences,
                examples=list(candidate.examples),
                sessions=list(candidate.sessions),
                draft_yaml=draft_skill_yaml(candidate),
                status=existing.status if existing else "pending",
                created_at=existing.created_at if existing else _now_iso(),
            )
            self.store.append("skill_proposals", proposal)
            proposals.append(proposal)
        return proposals

    def get_proposal(self, slug: str) -> SkillProposal | None:
        data = self.store.latest("skill_proposals", f"proposal-{slug}")
        return SkillProposal(**data) if data else None

    def list_proposals(self, *, status: str | None = None) -> list[SkillProposal]:
        records = cast(list[dict[str, Any]], self.store.list_latest("skill_proposals"))
        proposals = [SkillProposal(**item) for item in records]
        if status is not None:
            proposals = [proposal for proposal in proposals if proposal.status == status]
        return proposals


class _RuntimeExperienceView:
    """Adapter matching the legacy detector's session-store protocol."""

    def __init__(self, store: RuntimeStateStore):
        self.store = store

    def list_sessions(self) -> list[str]:
        records = cast(list[dict[str, Any]], self.store.list("skill_experiences"))
        return sorted({str(item.get("session_id")) for item in records})

    def load(self, session_id: str) -> list[dict[str, str]]:
        records = cast(list[dict[str, Any]], self.store.list("skill_experiences"))
        return [
            {"role": "user", "content": str(item.get("user_message") or "")}
            for item in records
            if str(item.get("session_id")) == session_id
        ]
