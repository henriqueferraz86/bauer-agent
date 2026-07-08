"""Auditable runtime memory records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..events import EventBus
from .state_store import JsonlStateStore

MEMORY_SCOPES = {"user", "company", "project", "agent", "skill"}


@dataclass
class MemoryRecord:
    id: str
    scope: str
    content: str
    source: str
    confidence: float
    valid_until: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryRecord":
        return cls(
            id=str(data["id"]),
            scope=str(data["scope"]),
            content=str(data["content"]),
            source=str(data["source"]),
            confidence=float(data["confidence"]),
            valid_until=str(data["valid_until"]) if data.get("valid_until") else None,
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
        )


class RuntimeMemoryManager:
    """Persistent memory manager for runtime facts and decisions."""

    collection = "memory_records"

    def __init__(
        self,
        *,
        root: str | Path = "memory/runtime",
        store: JsonlStateStore | None = None,
        event_bus: EventBus | None = None,
    ):
        self.store = store or JsonlStateStore(root)
        self.event_bus = event_bus or EventBus(store=self.store)

    def write(
        self,
        *,
        scope: str,
        content: str,
        source: str,
        confidence: float = 1.0,
        valid_until: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        skill_id: str | None = None,
    ) -> MemoryRecord:
        now = _now()
        record = MemoryRecord(
            id=f"mem-{uuid4()}",
            scope=_validate_scope(scope),
            content=_validate_text(content, "content"),
            source=_validate_text(source, "source"),
            confidence=_validate_confidence(confidence),
            valid_until=_validate_datetime(valid_until, "valid_until"),
            created_at=now,
            updated_at=now,
        )
        self.store.upsert(self.collection, record)
        self.event_bus.publish(
            "memory.written",
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            skill_id=skill_id,
            status="completed",
            message="runtime memory written",
            data=_event_data(record),
        )
        return record

    def get(self, memory_id: str) -> MemoryRecord | None:
        record = self.store.latest(self.collection, memory_id)
        return MemoryRecord.from_dict(record) if record else None

    def list(self, *, scope: str | None = None, include_expired: bool = False) -> list[MemoryRecord]:
        if scope is not None:
            scope = _validate_scope(scope)
        records = [MemoryRecord.from_dict(record) for record in self.store.list_latest(self.collection)]
        if scope:
            records = [record for record in records if record.scope == scope]
        if not include_expired:
            records = [record for record in records if not self.is_expired(record)]
        return records

    def search(
        self,
        query: str,
        *,
        scope: str | None = None,
        include_expired: bool = False,
    ) -> list[MemoryRecord]:
        needle = query.strip().lower()
        if not needle:
            return []
        return [
            record
            for record in self.list(scope=scope, include_expired=include_expired)
            if needle in record.content.lower() or needle in record.source.lower()
        ]

    def revise(
        self,
        memory_id: str,
        *,
        content: str | None = None,
        source: str | None = None,
        confidence: float | None = None,
        valid_until: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        skill_id: str | None = None,
    ) -> MemoryRecord:
        current = self.get(memory_id)
        if current is None:
            raise KeyError(f"memory record not found: {memory_id}")

        revised = MemoryRecord(
            id=current.id,
            scope=current.scope,
            content=_validate_text(content, "content") if content is not None else current.content,
            source=_validate_text(source, "source") if source is not None else current.source,
            confidence=_validate_confidence(confidence) if confidence is not None else current.confidence,
            valid_until=_validate_datetime(valid_until, "valid_until") if valid_until is not None else current.valid_until,
            created_at=current.created_at,
            updated_at=_now(),
        )
        self.store.upsert(self.collection, revised)
        self.event_bus.publish(
            "memory.revised",
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            skill_id=skill_id,
            status="completed",
            message="runtime memory revised",
            data=_event_data(revised),
        )
        return revised

    def expire(
        self,
        memory_id: str,
        *,
        reason: str = "manual",
        run_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        skill_id: str | None = None,
    ) -> MemoryRecord:
        current = self.get(memory_id)
        if current is None:
            raise KeyError(f"memory record not found: {memory_id}")

        expired = MemoryRecord(
            id=current.id,
            scope=current.scope,
            content=current.content,
            source=current.source,
            confidence=current.confidence,
            valid_until=_now(),
            created_at=current.created_at,
            updated_at=_now(),
        )
        self.store.upsert(self.collection, expired)
        data = _event_data(expired)
        data["reason"] = reason
        self.event_bus.publish(
            "memory.expired",
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            skill_id=skill_id,
            status="completed",
            message="runtime memory expired",
            data=data,
        )
        return expired

    @staticmethod
    def is_expired(record: MemoryRecord, *, now: datetime | None = None) -> bool:
        if not record.valid_until:
            return False
        expires_at = _parse_datetime(record.valid_until)
        return expires_at <= (now or datetime.now(UTC))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_scope(scope: str) -> str:
    value = scope.strip().lower()
    if value not in MEMORY_SCOPES:
        allowed = ", ".join(sorted(MEMORY_SCOPES))
        raise ValueError(f"invalid memory scope: {scope!r}; expected one of: {allowed}")
    return value


def _validate_text(value: str, field: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _validate_confidence(confidence: float) -> float:
    value = float(confidence)
    if value < 0 or value > 1:
        raise ValueError("confidence must be between 0 and 1")
    return value


def _validate_datetime(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    parsed = _parse_datetime(value)
    return parsed.isoformat()


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("datetime value is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid datetime for valid_until: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _event_data(record: MemoryRecord) -> dict[str, Any]:
    return {
        "memory_id": record.id,
        "scope": record.scope,
        "source": record.source,
        "confidence": record.confidence,
        "valid_until": record.valid_until,
    }
