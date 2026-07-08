"""Durable gateway delivery outbox.

This is the platform-delivery foundation: producers enqueue delivery intents and
workers/drivers deliver them independently. V1 supports file and webhook
targets plus registered platform adapters.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .secret_policy import sanitize_mapping, sanitize_text
from .gateway_adapters import GatewayDeliveryAdapter, SUPPORTED_GATEWAY_CHANNELS


TERMINAL_STATUSES = {"delivered", "failed", "cancelled"}


@dataclass(frozen=True)
class OutboxMessage:
    message_id: str
    channel: str
    target: str
    payload: dict[str, Any]
    status: str = "pending"
    attempts: int = 0
    max_attempts: int = 3
    created_at: str = ""
    updated_at: str = ""
    delivered_at: str = ""
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeliveryResult:
    delivered: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


class GatewayOutbox:
    """SQLite-backed delivery outbox scoped to one workspace."""

    def __init__(self, workspace: str | Path = "workspace"):
        self.workspace = Path(workspace).resolve()
        self.store_dir = self.workspace / ".bauer_gateway"
        self.db_path = self.store_dir / "outbox.sqlite3"

    def enqueue(
        self,
        *,
        channel: str,
        target: str,
        payload: dict[str, Any],
        message_id: str = "",
        max_attempts: int = 3,
        metadata: dict[str, Any] | None = None,
    ) -> OutboxMessage:
        channel = channel.strip().lower()
        if channel not in SUPPORTED_GATEWAY_CHANNELS:
            allowed = ", ".join(sorted(SUPPORTED_GATEWAY_CHANNELS))
            raise ValueError(f"channel must be one of: {allowed}")
        target = target.strip()
        if not target:
            raise ValueError("target is required")
        message_id = message_id or f"msg-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO outbox_messages
                    (message_id, channel, target, payload_json, status, attempts,
                     max_attempts, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    channel,
                    target,
                    _json(payload),
                    max(1, int(max_attempts)),
                    now,
                    now,
                    _json(metadata or {}),
                ),
            )
            row = conn.execute("SELECT * FROM outbox_messages WHERE message_id = ?", (message_id,)).fetchone()
        return _message_from_row(row)

    def pending(self, *, limit: int = 20) -> list[OutboxMessage]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM outbox_messages
                WHERE status IN ('pending', 'retrying') AND attempts < max_attempts
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        return [_message_from_row(row) for row in rows]

    def get_message(self, message_id: str) -> OutboxMessage | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM outbox_messages WHERE message_id = ?", (message_id,)).fetchone()
        return _message_from_row(row) if row else None

    def list_messages(self, *, limit: int = 50) -> list[OutboxMessage]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM outbox_messages ORDER BY updated_at DESC, created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [_message_from_row(row) for row in rows]

    def deliver_once(self, *, limit: int = 20, dry_run: bool = False) -> DeliveryResult:
        result = DeliveryResult()
        for message in self.pending(limit=limit):
            self._deliver_with_result(message, result, dry_run=dry_run)
        return result

    def deliver_message(self, message_id: str, *, dry_run: bool = False) -> DeliveryResult:
        result = DeliveryResult()
        message = self.get_message(message_id)
        if message is None or message.status not in {"pending", "retrying"} or message.attempts >= message.max_attempts:
            result.skipped.append(message_id)
            return result
        self._deliver_with_result(message, result, dry_run=dry_run)
        return result

    def _deliver_with_result(self, message: OutboxMessage, result: DeliveryResult, *, dry_run: bool = False) -> None:
        if dry_run:
            result.skipped.append(message.message_id)
            return
        try:
            self._deliver(message)
            self.update_message(message.message_id, status="delivered", error="")
            result.delivered.append(message.message_id)
        except Exception as exc:
            attempts = message.attempts + 1
            status = "failed" if attempts >= message.max_attempts else "retrying"
            self.update_message(message.message_id, status=status, error=str(exc), attempts=attempts)
            result.failed.append(message.message_id)

    def update_message(
        self,
        message_id: str,
        *,
        status: str | None = None,
        error: str | None = None,
        attempts: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OutboxMessage | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM outbox_messages WHERE message_id = ?", (message_id,)).fetchone()
            if row is None:
                return None
            current = _message_from_row(row)
            next_status = status or current.status
            merged_metadata = dict(current.metadata)
            if metadata:
                merged_metadata.update(metadata)
            now = _now_iso()
            delivered_at = current.delivered_at
            if next_status == "delivered" and not delivered_at:
                delivered_at = now
            conn.execute(
                """
                UPDATE outbox_messages
                SET status = ?, attempts = ?, updated_at = ?, delivered_at = ?,
                    error = ?, metadata_json = ?
                WHERE message_id = ?
                """,
                (
                    next_status,
                    attempts if attempts is not None else current.attempts,
                    now,
                    delivered_at,
                    sanitize_text(error or "") if error is not None else current.error,
                    _json(merged_metadata),
                    message_id,
                ),
            )
            row = conn.execute("SELECT * FROM outbox_messages WHERE message_id = ?", (message_id,)).fetchone()
        return _message_from_row(row)

    def _deliver(self, message: OutboxMessage) -> None:
        payload = sanitize_mapping(message.payload)
        GatewayDeliveryAdapter(self.workspace).deliver(
            channel=message.channel,
            target=message.target,
            payload=payload,
            metadata=message.metadata,
            message_id=message.message_id,
        )

    def _connect(self) -> sqlite3.Connection:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self._ensure_schema(conn)
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS outbox_messages (
                message_id TEXT PRIMARY KEY,
                channel TEXT NOT NULL,
                target TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                delivered_at TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_outbox_status_created
                ON outbox_messages(status, created_at);
            """
        )


def _message_from_row(row: sqlite3.Row) -> OutboxMessage:
    return OutboxMessage(
        message_id=row["message_id"],
        channel=row["channel"],
        target=row["target"],
        payload=_loads_dict(row["payload_json"]),
        status=row["status"],
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        delivered_at=row["delivered_at"],
        error=row["error"],
        metadata=_loads_dict(row["metadata_json"]),
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads_dict(value: str) -> dict[str, Any]:
    try:
        raw = json.loads(value or "{}")
        return raw if isinstance(raw, dict) else {}
    except json.JSONDecodeError:
        return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
