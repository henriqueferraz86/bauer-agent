"""Bind server-generated streaming runs to authenticated owners before exposure.

This isolated primitive does not install a cancel route or assert that a run
has stopped. The integration must verify the JWT and requested session before
creating the tracker, and reconcile cancellation to a terminal remote state.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path


class ActiveRunConflict(ValueError):
    """A run identifier is already bound to a different context."""


class ActiveRunRegistry:
    """Durable immutable ownership records shared by server processes."""

    def __init__(self, path: Path):
        if not isinstance(path, Path):
            raise ValueError("registry path must be a Path")
        self.path = path
        # Distinguish independently built apps AND pre-fork worker processes.
        # A restart intentionally makes prior in-memory cancellation state
        # unavailable rather than falsely accepting cancellation.
        self._instance_id = uuid.uuid4().hex
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS agentos_active_runs ("
                       "run_id TEXT PRIMARY KEY, owner TEXT NOT NULL, entity_type TEXT NOT NULL, "
                       "entity_id TEXT NOT NULL, session_id TEXT NOT NULL, server_id TEXT NOT NULL)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(agentos_active_runs)")}
            if "server_id" not in columns:
                # Legacy ownership is preserved but can no longer prove which
                # in-memory manager owns the run; cancellation fails closed.
                db.execute("ALTER TABLE agentos_active_runs ADD COLUMN server_id TEXT NOT NULL DEFAULT ''")
            if "active" not in columns:
                # Existing records predate lifecycle tracking. Do not infer
                # that an old run is still active after upgrade or restart.
                db.execute("ALTER TABLE agentos_active_runs ADD COLUMN active INTEGER NOT NULL DEFAULT 0")

    def _server_id(self) -> str:
        return f"{self._instance_id}:{os.getpid()}"

    def record(self, *, run_id: str, owner: str, entity_type: str,
               entity_id: str, session_id: str) -> None:
        values = (run_id, owner, entity_id, session_id)
        if (entity_type not in {"agents", "teams"} or any(
            not isinstance(v, str) or not v.strip() or v != v.strip() or len(v) > 256 for v in values
        )):
            raise ValueError("invalid active run identity")
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT owner, entity_type, entity_id, session_id, server_id "
                             "FROM agentos_active_runs WHERE run_id=?", (run_id,)).fetchone()
            identity = (owner, entity_type, entity_id, session_id, self._server_id())
            if row is not None:
                raise ActiveRunConflict("run identity already registered")
            db.execute("INSERT INTO agentos_active_runs "
                       "(run_id, owner, entity_type, entity_id, session_id, server_id, active) "
                       "VALUES (?, ?, ?, ?, ?, ?, 1)", (run_id, *identity))

    def finish(self, *, run_id: str, owner: str, entity_type: str,
               entity_id: str, session_id: str) -> None:
        """Retire an observed terminal run without releasing its identity."""
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("UPDATE agentos_active_runs SET active=0 WHERE run_id=? AND owner=? "
                       "AND entity_type=? AND entity_id=? AND session_id=? AND server_id=?",
                       (run_id, owner, entity_type, entity_id, session_id, self._server_id()))

    def owns(self, *, run_id: str, owner: str, entity_type: str,
             entity_id: str, session_id: str) -> bool:
        with sqlite3.connect(self.path, timeout=30) as db:
            return db.execute("SELECT 1 FROM agentos_active_runs WHERE run_id=? AND owner=? "
                              "AND entity_type=? AND entity_id=? AND session_id=? AND active=1",
                              (run_id, owner, entity_type, entity_id, session_id)).fetchone() is not None

    def owned_here(self, *, run_id: str, owner: str, entity_type: str,
                   entity_id: str, session_id: str) -> bool:
        """Only the originating process can reach its in-memory manager."""
        with sqlite3.connect(self.path, timeout=30) as db:
            return db.execute("SELECT 1 FROM agentos_active_runs WHERE run_id=? AND owner=? "
                              "AND entity_type=? AND entity_id=? AND session_id=? AND server_id=? AND active=1",
                              (run_id, owner, entity_type, entity_id, session_id, self._server_id())).fetchone() is not None


class StartedEventTracker:
    """Buffer SSE frames and bind a top-level start event before forwarding it."""

    def __init__(self, registry: ActiveRunRegistry, *, owner: str, entity_type: str,
                 entity_id: str, session_id: str, max_frame_bytes: int = 1024 * 1024):
        self.registry = registry
        self.owner, self.entity_type, self.entity_id, self.session_id = owner, entity_type, entity_id, session_id
        self.max_frame_bytes = max_frame_bytes
        self._buffer = b""
        self._run_id: str | None = None

    def feed(self, chunk: bytes, *, final: bool = False) -> list[bytes]:
        if not isinstance(chunk, bytes) or self.max_frame_bytes <= 0:
            raise ValueError("invalid SSE input")
        self._buffer += chunk
        frames: list[bytes] = []
        while True:
            # Agno emits LF, while proxies may preserve CRLF.
            positions = [(self._buffer.find(marker), marker) for marker in (b"\n\n", b"\r\n\r\n")]
            positions = [(pos, marker) for pos, marker in positions if pos >= 0]
            if not positions:
                break
            pos, marker = min(positions, key=lambda item: item[0])
            end = pos + len(marker)
            if end > self.max_frame_bytes:
                raise ValueError("SSE frame too large")
            frame, self._buffer = self._buffer[:end], self._buffer[end:]
            self._record_started(frame)
            frames.append(frame)
        if len(self._buffer) > self.max_frame_bytes:
            raise ValueError("SSE frame too large")
        if final and self._buffer:
            raise ValueError("truncated SSE frame")
        if final:
            # Once this stream ends the process can no longer prove liveness
            # from the in-memory cancellation manager alone.
            self.close()
        return frames

    def close(self) -> None:
        if self._run_id is not None:
            self.registry.finish(run_id=self._run_id, owner=self.owner, entity_type=self.entity_type,
                                 entity_id=self.entity_id, session_id=self.session_id)

    def _record_started(self, frame: bytes) -> None:
        lines = frame.decode("utf-8", errors="strict").splitlines()
        event_name = next((line[6:].lstrip(" ") for line in lines if line.startswith("event:")), "")
        data = [line[5:].removeprefix(" ") for line in lines if line.startswith("data:")]
        if not data:
            return
        payload = json.loads("\n".join(data))
        if not isinstance(payload, dict):
            raise ValueError("invalid SSE payload")
        name = payload.get("event") or event_name
        expected = "RunStarted" if self.entity_type == "agents" else "TeamRunStarted"
        terminal_events = ({"RunCompleted", "RunCancelled", "RunError"} if self.entity_type == "agents"
                           else {"TeamRunCompleted", "TeamRunCancelled", "TeamRunError"})
        if name in terminal_events and self._run_id is not None and payload.get("run_id") == self._run_id:
            self.registry.finish(run_id=self._run_id, owner=self.owner, entity_type=self.entity_type,
                                 entity_id=self.entity_id, session_id=self.session_id)
            return
        if name != expected:
            return
        id_field = "agent_id" if self.entity_type == "agents" else "team_id"
        if (payload.get(id_field) != self.entity_id or payload.get("session_id") != self.session_id
                or payload.get("parent_run_id")):
            raise ValueError("start event context mismatch")
        run_id = payload.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("start event has no run identity")
        self.registry.record(run_id=run_id, owner=self.owner, entity_type=self.entity_type,
                             entity_id=self.entity_id, session_id=self.session_id)
        self._run_id = run_id
