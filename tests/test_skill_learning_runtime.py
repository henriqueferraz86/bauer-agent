from pathlib import Path

from bauer.core.runtime import SkillLearningManager


def test_recurring_experiences_become_persistent_pending_proposal(tmp_path: Path):
    manager = SkillLearningManager(root=tmp_path / "runtime")
    request = "investigue o crashloopbackoff do deployment payments e gere um relatorio"
    manager.record_experience(session_id="s1", user_message=request)
    manager.record_experience(session_id="s2", user_message=request)
    manager.record_experience(session_id="s3", user_message=request)

    proposals = manager.propose(min_occurrences=3)

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.status == "pending"
    assert proposal.occurrences == 3
    assert len(proposal.sessions) == 3
    assert f"name: {proposal.slug}" in proposal.draft_yaml
    assert not (tmp_path / "workspace" / ".bauer_skills.json").exists()

    restored = SkillLearningManager(root=tmp_path / "runtime")
    assert restored.get_proposal(proposal.slug).draft_yaml == proposal.draft_yaml


def test_short_requests_and_single_session_repetition_are_ignored(tmp_path: Path):
    manager = SkillLearningManager(root=tmp_path / "runtime")
    for session_id in ("same-1", "same-2", "same-3"):
        manager.record_experience(session_id=session_id, user_message="ok")
    for _ in range(4):
        manager.record_experience(
            session_id="one-session",
            user_message="liste os pods do namespace payments e explique os erros",
        )

    assert manager.propose(min_occurrences=3) == []

