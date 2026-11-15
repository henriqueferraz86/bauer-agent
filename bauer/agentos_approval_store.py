"""Owner-scoped, single-use remote approval decisions.

No tool is executed by this module. A consumer must independently validate the
remote run is paused and the current tool call matches the recorded digest.
After a crash during CONSUMING the result is uncertain and must be reconciled,
never executed again automatically.
"""
from __future__ import annotations

import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class ApprovalConflict(ValueError):
    pass


class ApprovalNotFound(LookupError):
    """Also used for ownership mismatch to avoid disclosing another user's run."""


@dataclass(frozen=True)
class RemoteApproval:
    approval_id: str
    owner_id: str
    entity_type: str
    entity_id: str
    run_id: str
    session_id: str
    tool_call_id: str
    payload_digest: str
    state: str
    expires_at: float


class RemoteApprovalStore:
    def __init__(self, path: str | Path):
        if not isinstance(path, (str, Path)) or not str(path):
            raise ValueError("path must be a non-empty filesystem path")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS remote_approvals (
                approval_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                entity_type TEXT NOT NULL CHECK (entity_type IN ('agents', 'teams')),
                entity_id TEXT NOT NULL, run_id TEXT NOT NULL, session_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL, payload_digest TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('PENDING', 'APPROVED', 'DENIED',
                                                     'CONSUMING', 'CONSUMED', 'UNKNOWN')),
                expires_at REAL NOT NULL,
                UNIQUE (entity_type, entity_id, run_id, tool_call_id))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS remote_cancel_intents (
                owner_id TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
                run_id TEXT NOT NULL, session_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (owner_id, entity_type, entity_id, run_id, session_id))""")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> RemoteApproval:
        if row is None:
            raise ApprovalNotFound("approval not found")
        return RemoteApproval(**dict(row))

    def issue(self, *, owner_id: str, entity_type: str, entity_id: str, run_id: str,
              session_id: str, tool_call_id: str, payload_digest: str,
              expires_at: float) -> RemoteApproval:
        """Internal operation: only a trusted observer of a paused run may call it."""
        fields = (owner_id, entity_id, run_id, session_id, tool_call_id)
        if any(not isinstance(v, str) or not v.strip() for v in fields):
            raise ValueError("approval identifiers must be non-empty")
        if entity_type not in {"agents", "teams"}:
            raise ValueError("invalid entity type")
        if not isinstance(payload_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", payload_digest):
            raise ValueError("payload_digest must be a SHA256 digest")
        if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)) or not time.time() < expires_at < time.time() + 86400:
            raise ValueError("invalid approval expiry")
        approval_id = uuid.uuid4().hex
        with self._connect() as conn:
            try:
                conn.execute("INSERT INTO remote_approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)",
                             (approval_id, owner_id, entity_type, entity_id, run_id, session_id,
                              tool_call_id, payload_digest, expires_at))
            except sqlite3.IntegrityError as exc:
                raise ApprovalConflict("tool call already registered") from exc
            return self._row(conn.execute("SELECT * FROM remote_approvals WHERE approval_id=?",
                                          (approval_id,)).fetchone())

    def get(self, approval_id: str, *, owner_id: str) -> RemoteApproval:
        with self._connect() as conn:
            return self._row(conn.execute(
                "SELECT * FROM remote_approvals WHERE approval_id=? AND owner_id=?",
                (approval_id, owner_id)).fetchone())

    def decide(self, approval_id: str, *, owner_id: str, approve: bool) -> RemoteApproval:
        if not isinstance(approve, bool):
            raise ValueError("approval decision must be a boolean")
        desired = "APPROVED" if approve else "DENIED"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._row(conn.execute(
                "SELECT * FROM remote_approvals WHERE approval_id=? AND owner_id=?",
                (approval_id, owner_id)).fetchone())
            if record.expires_at <= time.time():
                raise ApprovalConflict("approval already decided or expired")
            if record.state == desired:
                return record
            if record.state != "PENDING":
                raise ApprovalConflict("approval already decided or expired")
            conn.execute("UPDATE remote_approvals SET state=? WHERE approval_id=?", (desired, approval_id))
            return self._row(conn.execute("SELECT * FROM remote_approvals WHERE approval_id=?",
                                          (approval_id,)).fetchone())

    def claim(self, approval_id: str, *, owner_id: str, payload_digest: str) -> RemoteApproval:
        """Atomically reserve a single execution; the consumer verifies paused state separately."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._row(conn.execute(
                "SELECT * FROM remote_approvals WHERE approval_id=? AND owner_id=?",
                (approval_id, owner_id)).fetchone())
            if (record.state != "APPROVED" or record.payload_digest != payload_digest
                    or record.expires_at <= time.time()):
                raise ApprovalConflict("approval cannot be consumed")
            if conn.execute(
                "SELECT 1 FROM remote_cancel_intents WHERE owner_id=? AND entity_type=? "
                "AND entity_id=? AND run_id=? AND session_id=?",
                (record.owner_id, record.entity_type, record.entity_id, record.run_id, record.session_id),
            ).fetchone():
                raise ApprovalConflict("run cancellation was requested")
            conn.execute("UPDATE remote_approvals SET state='CONSUMING' WHERE approval_id=?", (approval_id,))
            return self._row(conn.execute("SELECT * FROM remote_approvals WHERE approval_id=?",
                                          (approval_id,)).fetchone())

    def request_cancel(self, *, owner_id: str, entity_type: str, entity_id: str,
                       run_id: str, session_id: str) -> None:
        """Serialize cancellation intent with tool consumption across processes.

        Caller must authenticate the owner and independently verify the run.
        If a tool may be in flight, refuse cancellation; a caller cannot claim
        cancellation succeeded until it can reconcile the run terminal state.
        Intents are retained even if a subsequent remote cancel fails.
        """
        values = (owner_id, entity_id, run_id, session_id)
        if entity_type not in {"agents", "teams"} or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise ValueError("invalid cancellation identity")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            consuming = conn.execute(
                "SELECT 1 FROM remote_approvals WHERE owner_id=? AND entity_type=? AND entity_id=? "
                "AND run_id=? AND session_id=? AND state IN ('CONSUMING', 'UNKNOWN') LIMIT 1",
                (owner_id, entity_type, entity_id, run_id, session_id),
            ).fetchone()
            if consuming:
                raise ApprovalConflict("a tool may still be in flight")
            conn.execute("INSERT OR IGNORE INTO remote_cancel_intents VALUES (?, ?, ?, ?, ?, ?)",
                         (owner_id, entity_type, entity_id, run_id, session_id, time.time()))

    def settle(self, approval_id: str, *, owner_id: str, completed: bool) -> RemoteApproval:
        if not isinstance(completed, bool):
            raise ValueError("completion must be a boolean")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            record = self._row(conn.execute(
                "SELECT * FROM remote_approvals WHERE approval_id=? AND owner_id=?",
                (approval_id, owner_id)).fetchone())
            if record.state != "CONSUMING":
                raise ApprovalConflict("approval is not being consumed")
            state = "CONSUMED" if completed else "UNKNOWN"
            conn.execute("UPDATE remote_approvals SET state=? WHERE approval_id=?", (state, approval_id))
            return self._row(conn.execute("SELECT * FROM remote_approvals WHERE approval_id=?",
                                          (approval_id,)).fetchone())
