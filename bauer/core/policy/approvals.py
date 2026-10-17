"""Approval request persistence for policy decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..events import EventBus
from ..runtime.run_manager import TERMINAL_RUN_STATUSES
from ..runtime.state_store import SqliteStateStore


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
    scope: str = "once"
    payload_digest: str | None = None
    consumed_at: str | None = None
    governed: bool = False


def _payload_digest(payload: dict[str, Any]) -> str:
    # Hash BEFORE persistence redaction: two different secrets must not become
    # the same approved argument. approval_id is transport metadata only.
    data = {key: value for key, value in payload.items() if key != "approval_id"}
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


class ApprovalManager:
    def __init__(self, *, root: str | Path = "memory/runtime", event_bus: EventBus | None = None):
        self.root = Path(root)
        self.store = SqliteStateStore(self.root)
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
        suspend_run: bool = False,
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
            payload_digest=_payload_digest(payload or {}),
        )
        with self.store.transaction() as tx:
            run = tx.latest("runs", run_id) if run_id else None
            record.governed = suspend_run or run is not None
            if run is not None and record.session_id is None:
                record.session_id = run.get("session_id")
            if suspend_run:
                run = self._active_run(tx, record)
                if run is None or run["status"] != "running":
                    raise ValueError("Approval requires a running run")
                tx.upsert("runs", {**run, "status": "waiting_approval",
                                   "updated_at": datetime.now(UTC).isoformat()})
            tx.upsert("approvals", asdict(record))
        self.event_bus.publish(
            "approval.requested",
            run_id=run_id,
            session_id=record.session_id,
            tool_name=tool_name,
            status="pending",
            message=reason,
            data={"approval_id": record.id, "operation": operation, "risk_level": risk_level},
        )
        return record

    def approve(self, approval_id: str, *, scope: str = "once") -> ApprovalRecord:
        return self._resolve(approval_id, "approved", scope=scope)[0]

    def deny(self, approval_id: str) -> ApprovalRecord:
        return self._resolve(approval_id, "denied")[0]

    def get(self, approval_id: str) -> ApprovalRecord | None:
        record = self.store.latest("approvals", approval_id)
        return ApprovalRecord(**record) if record else None

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
        """Status inspection only; execution must use ``authorize`` to consume."""
        record = self.get(approval_id)
        if record is None or record.status != "approved":
            return False
        if operation is not None and record.operation != operation:
            return False
        if tool_name is not None and record.tool_name != tool_name:
            return False
        return True

    @staticmethod
    def _active_run(
        tx: Any, record: ApprovalRecord, *, allow_standalone: bool = False,
    ) -> dict[str, Any] | None:
        if not record.run_id:
            return None
        run = tx.latest("runs", record.run_id)
        if run is None:
            if allow_standalone and not record.governed and record.scope == "once":
                return None
            raise KeyError(f"Run not found: {record.run_id}")
        if run.get("session_id") != record.session_id:
            raise ValueError("Approval session does not match run")
        if run["status"] in TERMINAL_RUN_STATUSES:
            raise ValueError(f"Run is terminal: {run['status']}")
        return run

    def resolve_for_run(
        self, approval_id: str, status: str, *, scope: str = "once",
        run_status: str,
    ) -> tuple[ApprovalRecord, bool]:
        """Persist a Kernel-directed decision and run transition atomically."""
        return self._resolve(approval_id, status, scope=scope, run_status=run_status)

    def _resolve(
        self, approval_id: str, status: str, *, scope: str = "once",
        run_status: str | None = None,
    ) -> tuple[ApprovalRecord, bool]:
        if not isinstance(scope, str) or scope not in {"once", "run", "always"}:
            raise ValueError(f"Invalid approval scope: {scope}")
        if status not in {"approved", "denied", "expired"}:
            raise ValueError(f"Invalid approval decision: {status}")
        with self.store.transaction() as tx:
            data = tx.latest("approvals", approval_id)
            if data is None:
                raise KeyError(f"Approval not found: {approval_id}")
            current = ApprovalRecord(**data)
            if current.status != "pending":
                if current.status == status and current.scope == scope:
                    return current, False
                raise ValueError("Approval decision or scope already resolved")
            if scope in {"run", "always"} and (
                status != "approved" or current.operation != "shell.execute"
                or current.tool_name != "run_command"
                or not current.run_id or not current.session_id
            ):
                raise ValueError("Run scope requires shell.execute/run_command and a run/session")
            run = tx.latest("runs", current.run_id) if current.run_id else None
            closing_terminal = bool(
                status == "expired" and run and run["status"] in TERMINAL_RUN_STATUSES
                and run.get("session_id") == current.session_id
            )
            if not closing_terminal:
                run = self._active_run(
                    tx, current, allow_standalone=run_status is None and scope == "once",
                )
            now = datetime.now(UTC).isoformat()
            if run is not None and run_status is not None and not closing_terminal:
                if run_status == "queued":
                    if status != "approved" or run["status"] != "waiting_approval":
                        raise ValueError("Run is not waiting for this approval")
                    changes = {"status": "queued", "updated_at": now}
                elif run_status == "failed" and status in {"denied", "expired"}:
                    reason = "aprovação negada" if status == "denied" else "approval expired"
                    changes = {"status": "failed", "updated_at": now,
                               "finished_at": now, "error": f"{reason}: {current.reason}"}
                else:
                    raise ValueError("Invalid approval run transition")
                tx.upsert("runs", {**run, **changes})
            record = ApprovalRecord(**{
                **asdict(current), "status": status, "scope": scope, "resolved_at": now,
            })
            tx.upsert("approvals", asdict(record))
        self.event_bus.publish(
            "approval.accepted" if status == "approved" else "approval.denied",
            run_id=record.run_id,
            session_id=record.session_id,
            tool_name=record.tool_name,
            status=status,
            message=record.reason,
            data={"approval_id": record.id, "operation": record.operation,
                  "risk_level": record.risk_level, "scope": scope},
        )
        return record, True

    def authorize(
        self, approval_id: str | None, *, operation: str, tool_name: str,
        payload: dict[str, Any], run_id: str | None, session_id: str | None,
        resume_run: bool = False,
    ) -> bool:
        """Consume one exact call, or use an explicitly granted shell run scope.

        A supplied ID is always bound to the original arguments and single use.
        An implicit run grant is available only to run_command in that run and
        session. Missing legacy fingerprints fail closed for exact-call replay.
        """
        candidates = [approval_id] if approval_id else [
            record.id for record in self.list("approved")
            if record.scope == "run" and record.run_id == run_id
            and record.session_id == session_id
        ]
        for candidate in candidates:
            resumed = None
            with self.store.transaction() as tx:
                data = tx.latest("approvals", candidate)
                if data is None:
                    continue
                record = ApprovalRecord(**data)
                if (record.status != "approved" or record.operation != operation
                        or record.tool_name != tool_name or record.run_id != run_id
                        or record.session_id != session_id):
                    continue
                if approval_id:
                    if record.consumed_at or record.payload_digest != _payload_digest(payload):
                        continue
                elif not (
                    record.scope == "run" and operation == "shell.execute"
                    and tool_name == "run_command" and run_id and session_id
                ):
                    continue
                run = self._active_run(tx, record, allow_standalone=True)
                if run is not None:
                    if run["status"] == "queued" and resume_run:
                        resumed = tx.upsert("runs", {
                            **run, "status": "running", "updated_at": datetime.now(UTC).isoformat(),
                        })
                    elif run["status"] != "running":
                        continue
                if approval_id:
                    tx.upsert("approvals", {
                        **asdict(record), "consumed_at": datetime.now(UTC).isoformat(),
                    })
            if record.scope == "always":
                from ...shell_runner import ShellRunner, add_learned_command, validate_command_syntax

                base = ShellRunner._base_command(validate_command_syntax(str(payload.get("command", ""))))
                add_learned_command(base, strict=True)
            if resumed is not None:
                self.event_bus.publish(
                    "run.started", run_id=run_id, session_id=session_id,
                    agent_id=resumed.get("agent_id"), status="running",
                    data={"runtime_adapter": resumed.get("runtime_adapter")},
                )
            return True
        return False
