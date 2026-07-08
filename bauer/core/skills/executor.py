"""Skill executor MVP."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..events import EventBus
from ..policy import PolicyEngine
from .manifest import SkillManifest


@dataclass(slots=True)
class SkillExecutionResult:
    skill_id: str
    status: str
    output: dict[str, Any]


class SkillExecutor:
    def __init__(
        self,
        *,
        workspace: str | Path = "workspace",
        policy_engine: PolicyEngine | None = None,
        event_bus: EventBus | None = None,
    ):
        self.workspace = Path(workspace)
        self.policy_engine = policy_engine or PolicyEngine(workspace=self.workspace)
        self.event_bus = event_bus

    def execute(self, manifest: SkillManifest, inputs: dict[str, Any] | None = None) -> SkillExecutionResult:
        if self.event_bus is not None:
            self.event_bus.publish(
                "skill.selected",
                skill_id=manifest.id,
                status="selected",
                data={"capabilities": manifest.capabilities, "risk": manifest.risk},
            )
        decision = self.policy_engine.evaluate(
            "skill.execute",
            {"skill_id": manifest.id, "permissions": manifest.permissions, **(inputs or {})},
        )
        if self.event_bus is not None:
            self.event_bus.publish(
                "policy.evaluated",
                skill_id=manifest.id,
                status=decision.action,
                message=decision.reason,
                data={"risk_level": decision.risk_level, "matched_rules": decision.matched_rules},
            )
        if decision.action in {"deny", "ask"}:
            return SkillExecutionResult(
                skill_id=manifest.id,
                status="waiting_approval" if decision.action == "ask" else "denied",
                output={"decision": asdict(decision)},
            )
        if self.event_bus is not None:
            self.event_bus.publish(
                "skill.executed",
                skill_id=manifest.id,
                status="completed",
                data={"capabilities": manifest.capabilities},
            )
        return SkillExecutionResult(
            skill_id=manifest.id,
            status="completed",
            output={"manifest": manifest.to_dict(), "inputs": inputs or {}},
        )
