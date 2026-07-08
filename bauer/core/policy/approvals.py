"""Approval request persistence for policy decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..events import EventBus
from ..runtime.state_store import JsonlStateStore


@dataclass(slots=True)
class ApprovalRecord:
    id: str
    operation: str
    tool_name: str
    status: str
    reason: str
    risk_level: str
    payload: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    session_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    resolved_at: str | None = None


class ApprovalManager:
    def __init__(self, *, root: str | Path = "memory/runtime", event_bus: EventBus | None = None):
        self.root = Path(root)
        self.store = JsonlStateStore(self.root)
        self.event_bus = event_bus or EventBus(root=self.root)

    def request(
        self,
        *,
        operation: str,
        tool_name: str,
        reason: str,
        risk_level: str,
        payload: dict[str, Any] | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> ApprovalRecord:
        record = ApprovalRecord(
            id=f"appr-{uuid4()}",
            operation=operation,
            tool_name=tool_name,
            status="pending",
            reason=reason,
            risk_level=risk_level,
            payload=payload or {},
            run_id=run_id,
            session_id=session_id,
        )
        self.store.append("approvals", asdict(record))
        self.event_bus.publish(
            "approval.requested",
            run_id=run_id,
            session_id=session_id,
            tool_name=tool_name,
            status="pending",
            message=reason,
            data={"approval_id": record.id, "operation": operation, "risk_level": risk_level},
        )
        return record

    def approve(self, approval_id: str) -> ApprovalRecord:
        return self._resolve(approval_id, "approved")

    def deny(self, approval_id: str) -> ApprovalRecord:
        return self._resolve(approval_id, "denied")

    def get(self, approval_id: str) -> ApprovalRecord | None:
        for record in reversed(self.store.list("approvals")):
            if record.get("id") == approval_id:
                return ApprovalRecord(**record)
        return None

    def list(self, status: str | None = None) -> list[ApprovalRecord]:
        records = [ApprovalRecord(**record) for record in self.store.list("approvals")]
        latest: dict[str, ApprovalRecord] = {}
        for record in records:
            latest[record.id] = record
        values = list(latest.values())
        if status:
            values = [record for record in values if record.status == status]
        return values

    def is_approved(self, approval_id: str, *, operation: str | None = None, tool_name: str | None = None) -> bool:
        record = self.get(approval_id)
        if record is None or record.status != "approved":
            return False
        if operation is not None and record.operation != operation:
            return False
        if tool_name is not None and record.tool_name != tool_name:
            return False
        return True

    def _resolve(self, approval_id: str, status: str) -> ApprovalRecord:
        current = self.get(approval_id)
        if current is None:
            raise KeyError(f"Approval not found: {approval_id}")
        record = ApprovalRecord(**{**asdict(current), "status": status, "resolved_at": datetime.now(UTC).isoformat()})
        self.store.append("approvals", asdict(record))
        self.event_bus.publish(
            "approval.accepted" if status == "approved" else "approval.denied",
            run_id=record.run_id,
            session_id=record.session_id,
            tool_name=record.tool_name,
            status=status,
            message=record.reason,
            data={"approval_id": record.id, "operation": record.operation, "risk_level": record.risk_level},
        )
        return record
