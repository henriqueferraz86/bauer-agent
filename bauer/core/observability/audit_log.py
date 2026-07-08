"""Persistent audit log for runtime decisions and actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..events.schema import Event
from ..runtime.state_store import JsonlStateStore


@dataclass(slots=True)
class AuditRecord:
    id: str
    timestamp: str
    action: str
    run_id: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    skill_id: str | None = None
    tool_name: str | None = None
    status: str | None = None
    reason: str | None = None
    risk_level: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AuditLog:
    def __init__(self, store: JsonlStateStore):
        self.store = store

    def record_event(self, event: Event) -> AuditRecord:
        metadata = dict(event.data or {})
        record = AuditRecord(
            id=event.id,
            timestamp=event.timestamp,
            action=event.event_type,
            run_id=event.run_id,
            session_id=event.session_id,
            agent_id=event.agent_id,
            skill_id=event.skill_id,
            tool_name=event.tool_name,
            status=event.status,
            reason=event.message,
            risk_level=_risk_from_event(event),
            metadata=metadata,
        )
        self.store.append("audit", record)
        return record

    def list_records(
        self,
        *,
        run_id: str | None = None,
        limit: int | None = 100,
    ) -> list[AuditRecord]:
        records = self.store.list("audit")
        if run_id:
            records = [record for record in records if record.get("run_id") == run_id]
        if limit is not None and limit >= 0:
            records = records[-limit:]
        return [AuditRecord(**record) for record in records]

    @staticmethod
    def to_dict(record: AuditRecord) -> dict[str, Any]:
        return {
            "id": record.id,
            "timestamp": record.timestamp,
            "action": record.action,
            "run_id": record.run_id,
            "session_id": record.session_id,
            "agent_id": record.agent_id,
            "skill_id": record.skill_id,
            "tool_name": record.tool_name,
            "status": record.status,
            "reason": record.reason,
            "risk_level": record.risk_level,
            "metadata": dict(record.metadata),
        }


def _risk_from_event(event: Event) -> str | None:
    value = (event.data or {}).get("risk_level")
    return str(value) if value else None
