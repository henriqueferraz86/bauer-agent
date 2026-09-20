from pathlib import Path

import pytest

from bauer.core.runtime import SkillLearningManager, SkillWorkshopManager


def _proposal(tmp_path: Path):
    learning = SkillLearningManager(root=tmp_path / "runtime")
    request = "investigue o crashloopbackoff do deployment payments e gere um relatorio"
    for session_id in ("s1", "s2", "s3"):
        learning.record_experience(session_id=session_id, user_message=request)
    return learning.propose()[0]


def test_workshop_validates_approves_and_keeps_history(tmp_path: Path):
    proposal = _proposal(tmp_path)
    workshop = SkillWorkshopManager(root=tmp_path / "runtime")

    assert workshop.validate(proposal.slug).valid is True
    destination = tmp_path / "skills"
    approved = workshop.approve(proposal.slug, destination=destination)

    assert approved.status == "approved"
    assert (destination / f"{proposal.slug}.yaml").read_text(encoding="utf-8") == proposal.draft_yaml
    assert workshop.history(proposal.slug)[-1]["action"] == "approved"


def test_workshop_rejects_and_reports_usage_metrics(tmp_path: Path):
    proposal = _proposal(tmp_path)
    workshop = SkillWorkshopManager(root=tmp_path / "runtime")
    rejected = workshop.reject(proposal.slug, reason="draft precisa de revisão")
    assert rejected.status == "rejected"
    assert workshop.history(proposal.slug)[-1]["reason"] == "draft precisa de revisão"

    workshop.record_usage(proposal.slug, success=True, steps=4, cost_usd=0.2, latency_ms=100)
    workshop.record_usage(proposal.slug, success=False, steps=6, cost_usd=0.4, latency_ms=300, human_override=True)
    metrics = workshop.benchmark(proposal.slug)
    assert metrics["executions"] == 2
    assert metrics["success_rate"] == 0.5
    assert metrics["average_steps"] == 5.0
    assert metrics["human_overrides"] == 1


def test_workshop_refuses_invalid_draft(tmp_path: Path):
    learning = SkillLearningManager(root=tmp_path / "runtime")
    learning.store.append(
        "skill_proposals",
        {
            "id": "proposal-bad",
            "slug": "bad",
            "occurrences": 3,
            "examples": [],
            "sessions": ["s1", "s2"],
            "draft_yaml": "name: bad\n",
            "status": "pending",
        },
    )
    workshop = SkillWorkshopManager(root=tmp_path / "runtime")
    with pytest.raises(ValueError, match="invoke"):
        workshop.approve("bad", destination=tmp_path / "skills")

