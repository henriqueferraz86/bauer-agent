"""Skill executor MVP."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..events import EventBus
from ..policy import ApprovalManager, PolicyEngine
from .manifest import SkillManifest
from .posix import execute_posix_skill, supports_posix_skill
from .windows import execute_windows_skill, supports_windows_skill


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
        runtime_root: str | Path = "memory/runtime",
        policy_engine: PolicyEngine | None = None,
        event_bus: EventBus | None = None,
    ):
        self.workspace = Path(workspace)
        self.runtime_root = Path(runtime_root)
        self.policy_engine = policy_engine or PolicyEngine(workspace=self.workspace)
        self.event_bus = event_bus

    def execute(self, manifest: SkillManifest, inputs: dict[str, Any] | None = None) -> SkillExecutionResult:
        inputs = inputs or {}
        if self.event_bus is not None:
            self.event_bus.publish(
                "skill.selected",
                skill_id=manifest.id,
                status="selected",
                data={"capabilities": manifest.capabilities, "risk": manifest.risk},
            )
        decision = self.policy_engine.evaluate(
            "skill.execute",
            {"skill_id": manifest.id, "permissions": manifest.permissions, **inputs},
        )
        approval_manager = ApprovalManager(root=self.runtime_root, event_bus=self.event_bus)
        if decision.action == "ask" and approval_manager.is_approved(
            str(inputs.get("approval_id") or ""),
            operation="skill.execute",
            tool_name=manifest.id,
        ):
            decision.action = "allow"
            decision.reason = "approved policy request"
        if self.event_bus is not None:
            self.event_bus.publish(
                "policy.evaluated",
                skill_id=manifest.id,
                status=decision.action,
                message=decision.reason,
                data={"risk_level": decision.risk_level, "matched_rules": decision.matched_rules},
            )
        if decision.action in {"deny", "ask"}:
            approval = None
            if decision.action == "ask":
                approval = approval_manager.request(
                    operation="skill.execute",
                    tool_name=manifest.id,
                    reason=decision.reason,
                    risk_level=decision.risk_level,
                    payload={"skill_id": manifest.id, "permissions": manifest.permissions, **inputs},
                )
            return SkillExecutionResult(
                skill_id=manifest.id,
                status="waiting_approval" if decision.action == "ask" else "denied",
                output={"decision": asdict(decision), "approval": asdict(approval) if approval else None},
            )
        if supports_windows_skill(manifest.id) or supports_posix_skill(manifest.id):
            return self._execute_builtin_os(manifest, inputs)
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
            output={"manifest": manifest.to_dict(), "inputs": inputs},
        )

    def _execute_builtin_os(self, manifest: SkillManifest, inputs: dict[str, Any]) -> SkillExecutionResult:
        if self.event_bus is not None:
            self.event_bus.publish(
                "tool.call.requested",
                skill_id=manifest.id,
                tool_name=manifest.id,
                status="requested",
                data={"inputs": inputs},
            )
        try:
            if supports_windows_skill(manifest.id):
                output = execute_windows_skill(manifest.id, inputs)
            else:
                output = execute_posix_skill(manifest.id, inputs)
        except Exception as exc:
            if self.event_bus is not None:
                self.event_bus.publish(
                    "tool.call.failed",
                    skill_id=manifest.id,
                    tool_name=manifest.id,
                    status="failed",
                    message=str(exc),
                    data={"inputs": inputs},
                )
                self.event_bus.publish(
                    "skill.executed",
                    skill_id=manifest.id,
                    status="failed",
                    message=str(exc),
                    data={"capabilities": manifest.capabilities},
                )
            return SkillExecutionResult(skill_id=manifest.id, status="failed", output={"error": str(exc)})
        if self.event_bus is not None:
            self.event_bus.publish(
                "tool.call.completed",
                skill_id=manifest.id,
                tool_name=manifest.id,
                status="completed",
                data={"output": output},
            )
            self.event_bus.publish(
                "skill.executed",
                skill_id=manifest.id,
                status="completed",
                data={"capabilities": manifest.capabilities},
            )
        return SkillExecutionResult(skill_id=manifest.id, status="completed", output=output)
