"""Human-governed validation, approval and measurement of skill proposals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from .skill_learning import SkillLearningManager, SkillProposal
from .state_store import RuntimeStateStore, SqliteStateStore


@dataclass
class SkillValidation:
    valid: bool
    errors: list[str]


class SkillWorkshopManager:
    def __init__(
        self,
        *,
        root: str | Path = "memory/runtime",
        store: RuntimeStateStore | None = None,
        learning_manager: SkillLearningManager | None = None,
    ):
        self.store = store or SqliteStateStore(root)
        self.learning_manager = learning_manager or SkillLearningManager(store=self.store)

    def validate(self, slug: str) -> SkillValidation:
        proposal = self._proposal(slug)
        if proposal is None:
            return SkillValidation(False, [f"proposal not found: {slug}"])
        errors: list[str] = []
        try:
            document = yaml.safe_load(proposal.draft_yaml) or {}
        except yaml.YAMLError as exc:
            return SkillValidation(False, [f"invalid YAML: {exc}"])
        if not isinstance(document, dict):
            errors.append("draft must be a mapping")
        else:
            if not str(document.get("name") or "").strip():
                errors.append("name is required")
            if not str(document.get("invoke") or "").strip():
                errors.append("invoke is required")
        return SkillValidation(not errors, errors)

    def approve(self, slug: str, *, destination: str | Path = "skills") -> SkillProposal:
        proposal = self._require_proposal(slug)
        validation = self.validate(slug)
        if not validation.valid:
            raise ValueError("; ".join(validation.errors))
        output_dir = Path(destination)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{slug}.yaml").write_text(proposal.draft_yaml, encoding="utf-8")
        proposal.status = "approved"
        self.store.append("skill_proposals", proposal)
        self._record_history(slug, "approved")
        return proposal

    def reject(self, slug: str, *, reason: str) -> SkillProposal:
        if not reason.strip():
            raise ValueError("rejection reason is required")
        proposal = self._require_proposal(slug)
        proposal.status = "rejected"
        self.store.append("skill_proposals", proposal)
        self._record_history(slug, "rejected", reason=reason)
        return proposal

    def history(self, slug: str | None = None) -> list[dict[str, Any]]:
        records = self.store.list("skill_proposal_history")
        if slug is not None:
            records = [record for record in records if record.get("slug") == slug]
        return records

    def record_usage(
        self,
        slug: str,
        *,
        success: bool,
        steps: int = 0,
        cost_usd: float = 0.0,
        latency_ms: float = 0.0,
        human_override: bool = False,
    ) -> None:
        self.store.append(
            "skill_usage",
            {
                "id": f"usage-{uuid4()}",
                "slug": slug,
                "success": bool(success),
                "steps": max(0, int(steps)),
                "cost_usd": max(0.0, float(cost_usd)),
                "latency_ms": max(0.0, float(latency_ms)),
                "human_override": bool(human_override),
                "created_at": _now_iso(),
            },
        )

    def benchmark(self, slug: str) -> dict[str, Any]:
        records = [record for record in self.store.list("skill_usage") if record.get("slug") == slug]
        count = len(records)
        successes = sum(1 for record in records if record.get("success"))
        return {
            "slug": slug,
            "executions": count,
            "success_rate": round(successes / count, 6) if count else 0.0,
            "failure_rate": round((count - successes) / count, 6) if count else 0.0,
            "average_steps": round(sum(float(record.get("steps", 0)) for record in records) / count, 6) if count else 0.0,
            "average_cost": round(sum(float(record.get("cost_usd", 0)) for record in records) / count, 6) if count else 0.0,
            "average_latency": round(sum(float(record.get("latency_ms", 0)) for record in records) / count, 6) if count else 0.0,
            "human_overrides": sum(1 for record in records if record.get("human_override")),
        }

    def _proposal(self, slug: str) -> SkillProposal | None:
        return self.learning_manager.get_proposal(slug)

    def _require_proposal(self, slug: str) -> SkillProposal:
        proposal = self._proposal(slug)
        if proposal is None:
            raise KeyError(f"proposal not found: {slug}")
        return proposal

    def _record_history(self, slug: str, action: str, *, reason: str | None = None) -> None:
        self.store.append(
            "skill_proposal_history",
            {
                "id": f"skill-history-{uuid4()}",
                "slug": slug,
                "action": action,
                "reason": reason,
                "created_at": _now_iso(),
            },
        )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()

