"""Event schema for auditable Bauer runtime actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, get_args
from uuid import uuid4

EventType = Literal[
    "run.created",
    "run.planning.started",
    "run.context.built",
    "run.workspace.created",
    "run.started",
    "run.progress.warning",
    "run.validation.started",
    "run.validation.failed",
    "run.replanning",
    "run.completed",
    "run.failed",
    "run.cancelled",
    "run.workspace.cleaned",
    "run.state.changed",
    "tool.call.requested",
    "tool.call.completed",
    "tool.call.failed",
    "tool.denied",
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
    "model.route.selected",
    "loop.round.completed",
    "delegation.requested",
    "delegation.accepted",
    "delegation.denied",
    "memory.written",
    "memory.revised",
    "memory.expired",
    "voice.turn.completed",
]

#: DERIVADO do Literal acima, não uma segunda lista escrita à mão. As duas
#: existiam em paralelo e já estavam a um esquecimento de divergir: quem
#: acrescentasse um tipo só no `Literal` passaria no type checker e tomaria
#: `ValueError` em runtime no `__post_init__` — telemetria que morre justamente
#: no evento novo.
EVENT_TYPES: tuple[str, ...] = get_args(EventType)


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
