"""Formal Session model and manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .state_store import JsonlStateStore


@dataclass
class Session:
    id: str
    user_id: str
    company_id: str | None
    agent_id: str
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())
    state: dict[str, Any] = field(default_factory=dict)


class SessionManager:
    def __init__(self, store: JsonlStateStore | None = None, root: str | Path = "memory/runtime"):
        self.store = store or JsonlStateStore(root)

    def create_session(
        self,
        *,
        session_id: str | None = None,
        user_id: str = "local",
        company_id: str | None = None,
        agent_id: str = "default",
        state: dict[str, Any] | None = None,
    ) -> Session:
        session = Session(
            id=session_id or f"session-{uuid4()}",
            user_id=user_id,
            company_id=company_id,
            agent_id=agent_id,
            state=state or {},
        )
        self.store.upsert("sessions", session)
        return session

    def get_session(self, session_id: str) -> Session | None:
        data = self.store.latest("sessions", session_id)
        return Session(**data) if data else None

    def get_or_create_session(
        self,
        session_id: str | None,
        *,
        user_id: str = "local",
        company_id: str | None = None,
        agent_id: str = "default",
        state: dict[str, Any] | None = None,
    ) -> Session:
        if session_id:
            existing = self.get_session(session_id)
            if existing is not None:
                return self.touch_session(session_id, state=state)
            return self.create_session(
                session_id=session_id,
                user_id=user_id,
                company_id=company_id,
                agent_id=agent_id,
                state=state,
            )
        return self.create_session(user_id=user_id, company_id=company_id, agent_id=agent_id, state=state)

    def list_sessions(self) -> list[Session]:
        return [Session(**item) for item in self.store.list_latest("sessions")]

    def touch_session(self, session_id: str, *, state: dict[str, Any] | None = None) -> Session:
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"Session not found: {session_id}")
        data = session.__dict__.copy()
        data["updated_at"] = _now_iso()
        if state is not None:
            merged = dict(data.get("state") or {})
            merged.update(state)
            data["state"] = merged
        updated = Session(**data)
        self.store.upsert("sessions", updated)
        return updated


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
