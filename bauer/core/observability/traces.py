"""Run trace construction from runtime events."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..events.schema import Event
from ..runtime.state_store import JsonlStateStore


@dataclass(slots=True)
class TraceSpan:
    id: str
    run_id: str
    timestamp: str
    name: str
    status: str | None = None
    duration_ms: float | None = None
    parent_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


class RunTraceStore:
    def __init__(self, store: JsonlStateStore):
        self.store = store

    def record_event(self, event: Event) -> TraceSpan | None:
        if not event.run_id:
            return None
        span = TraceSpan(
            id=event.id,
            run_id=event.run_id,
            timestamp=event.timestamp,
            name=event.event_type,
            status=event.status,
            duration_ms=None,
            parent_id=event.run_id,
            attributes={
                "session_id": event.session_id,
                "agent_id": event.agent_id,
                "skill_id": event.skill_id,
                "tool_name": event.tool_name,
                "message": event.message,
                **(event.data or {}),
            },
        )
        self.store.append("traces", span)
        return span

    def get_trace(self, run_id: str) -> dict[str, Any]:
        spans = [
            TraceSpan(**record)
            for record in self.store.list("traces")
            if record.get("run_id") == run_id
        ]
        spans = sorted(spans, key=lambda span: span.timestamp)
        started_at = spans[0].timestamp if spans else None
        finished_at = _terminal_timestamp(spans)
        return {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": _duration_ms(started_at, finished_at),
            "spans": [self.to_dict(span) for span in spans],
        }

    @staticmethod
    def to_dict(span: TraceSpan) -> dict[str, Any]:
        return {
            "id": span.id,
            "run_id": span.run_id,
            "timestamp": span.timestamp,
            "name": span.name,
            "status": span.status,
            "duration_ms": span.duration_ms,
            "parent_id": span.parent_id,
            "attributes": dict(span.attributes),
        }


def _terminal_timestamp(spans: list[TraceSpan]) -> str | None:
    for span in reversed(spans):
        if span.name in {"run.completed", "run.failed", "run.cancelled"}:
            return span.timestamp
    return None


def _duration_ms(started_at: str | None, finished_at: str | None) -> float | None:
    if not started_at or not finished_at:
        return None
    try:
        started = datetime.fromisoformat(started_at)
        finished = datetime.fromisoformat(finished_at)
    except ValueError:
        return None
    return round((finished - started).total_seconds() * 1000, 2)
