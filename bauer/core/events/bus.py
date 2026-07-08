"""Simple in-memory publish/subscribe event bus with JSONL persistence."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..runtime.state_store import JsonlStateStore
from .schema import Event, EventType

Subscriber = Callable[[Event], None]
logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self, store: JsonlStateStore | None = None, root: str | Path = "memory/runtime"):
        self.store = store or JsonlStateStore(root)
        self._subscribers: dict[str, list[Subscriber]] = {}

    def subscribe(self, event_type: str, handler: Subscriber) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    def publish(
        self,
        event_type: EventType,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        skill_id: str | None = None,
        tool_name: str | None = None,
        status: str | None = None,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> Event:
        event = Event(
            event_type=event_type,
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            skill_id=skill_id,
            tool_name=tool_name,
            status=status,
            message=message,
            data=data or {},
        )
        self.store.append("events", event)
        self._notify(event)
        return event

    def list_events(self, *, run_id: str | None = None, limit: int | None = None) -> list[Event]:
        records = self.store.list("events")
        if run_id:
            records = [record for record in records if record.get("run_id") == run_id]
        if limit is not None and limit >= 0:
            records = records[-limit:]
        return [Event(**record) for record in records]

    def _notify(self, event: Event) -> None:
        handlers = [*self._subscribers.get(event.event_type, []), *self._subscribers.get("*", [])]
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                logger.debug("event subscriber failed for %s: %s", event.event_type, exc)

    @staticmethod
    def to_dict(event: Event) -> dict[str, Any]:
        return asdict(event)
