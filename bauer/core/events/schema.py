"""Event schema for auditable Bauer runtime actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

EventType = Literal[
    "run.created",
    "run.started",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "tool.call.requested",
    "tool.call.completed",
    "tool.call.failed",
    "skill.selected",
    "skill.executed",
    "policy.evaluated",
    "approval.requested",
    "approval.accepted",
    "approval.denied",
    "schedule.triggered",
    "schedule.skipped",
    "schedule.failed",
    "budget.warning",
    "budget.exceeded",
    "autonomy.changed",
    "delegation.requested",
    "delegation.accepted",
    "delegation.denied",
]

EVENT_TYPES: tuple[str, ...] = (
    "run.created",
    "run.started",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "tool.call.requested",
    "tool.call.completed",
    "tool.call.failed",
    "skill.selected",
    "skill.executed",
    "policy.evaluated",
    "approval.requested",
    "approval.accepted",
    "approval.denied",
    "schedule.triggered",
    "schedule.skipped",
    "schedule.failed",
    "budget.warning",
    "budget.exceeded",
    "autonomy.changed",
    "delegation.requested",
    "delegation.accepted",
    "delegation.denied",
)


@dataclass
class Event:
    event_type: EventType
    id: str = field(default_factory=lambda: f"evt-{uuid4()}")
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    run_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    skill_id: str | None = None
    tool_name: str | None = None
    status: str | None = None
    message: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"Invalid event_type: {self.event_type}")
