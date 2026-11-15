"""Durable, at-most-once Telegram inbox primitive (not wired into polling).

Store only the text and identity needed to process an update. Text may contain
private data: put the database in a restricted directory, set OS-appropriate
ACLs, back it up securely and purge terminal records according to a retention
policy. SQLite does not encrypt this database. Do not store bot credentials,
raw Telegram payloads, attachments, or provider secrets here.

Persist before acknowledging an update to Telegram. CLAIMED is intentionally
never leased or automatically retried: after a crash it may already have
triggered a remote side effect and must be reconciled with the remote run store.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class InboxConflict(ValueError):
    """The same update was reused for a different envelope or state."""


@dataclass(frozen=True)
class InboxUpdate:
    channel: str
    update_id: str
    user_id: str
    chat_id: str
    text: str
    state: str
    received_at: float
    updated_at: float


class TelegramInbox:
    def __init__(self, path: str | Path) -> None:
        if not isinstance(path, (str, Path)) or not str(path):
            raise ValueError("path must be a non-empty filesystem path")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS telegram_inbox (
                    channel TEXT NOT NULL,
                    update_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('RECEIVED', 'CLAIMED', 'UNKNOWN', 'FINISHED')),
                    received_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(channel, update_id)
                );
                CREATE INDEX IF NOT EXISTS telegram_inbox_state ON telegram_inbox(state, received_at);
                CREATE TABLE IF NOT EXISTS telegram_inbox_results (
                    channel TEXT NOT NULL,
                    update_id TEXT NOT NULL,
                    response TEXT NOT NULL,
                    settled INTEGER NOT NULL CHECK(settled IN (0, 1)),
                    PRIMARY KEY(channel, update_id)
                );
                CREATE TABLE IF NOT EXISTS telegram_inbox_scope (
                    channel TEXT NOT NULL, update_id TEXT NOT NULL,
                    project_id TEXT NOT NULL, workspace TEXT NOT NULL,
                    PRIMARY KEY(channel, update_id)
                );
                CREATE TABLE IF NOT EXISTS telegram_inbox_delivery_attempts (
                    channel TEXT NOT NULL, update_id TEXT NOT NULL,
                    PRIMARY KEY(channel, update_id)
                );
                CREATE TABLE IF NOT EXISTS telegram_inbox_media (
                    channel TEXT NOT NULL, update_id TEXT NOT NULL,
                    metadata TEXT NOT NULL, text_content TEXT,
                    PRIMARY KEY(channel, update_id)
                );
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> InboxUpdate:
        return InboxUpdate(**dict(row))

    @staticmethod
    def _validate(channel: str, update_id: str, user_id: str, chat_id: str, text: str) -> None:
        if channel != "telegram":
            raise ValueError("only Telegram updates are supported")
        if any(not isinstance(v, str) or not v.strip() for v in (update_id, user_id, chat_id, text)):
            raise ValueError("update identity and text must be non-empty strings")

    def put(self, *, channel: str, update_id: str, user_id: str,
            chat_id: str, text: str, project_id: str = "", workspace: str = "",
            media: dict | None = None) -> tuple[InboxUpdate, bool]:
        """Persist before acknowledging; exact replay returns the original row."""
        self._validate(channel, update_id, user_id, chat_id, text)
        if not isinstance(project_id, str) or not isinstance(workspace, str):
            raise ValueError("project scope must be strings")
        if media is not None and not isinstance(media, dict):
            raise ValueError("invalid media metadata")
        encoded_media = json.dumps(media, ensure_ascii=True, sort_keys=True, separators=(",", ":")) if media is not None else None
        if encoded_media is not None and len(encoded_media) > 20_000:
            raise ValueError("media metadata exceeds limit")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM telegram_inbox WHERE channel=? AND update_id=?",
                               (channel, update_id)).fetchone()
            if row:
                record = self._record(row)
                if (record.user_id, record.chat_id, record.text) != (user_id, chat_id, text):
                    raise InboxConflict("update already exists with different envelope")
                scope = conn.execute("SELECT project_id, workspace FROM telegram_inbox_scope "
                                     "WHERE channel=? AND update_id=?", (channel, update_id)).fetchone()
                if scope is None or (scope["project_id"], scope["workspace"]) != (project_id, workspace):
                    raise InboxConflict("update already exists with different project scope")
                media_row = conn.execute("SELECT metadata FROM telegram_inbox_media WHERE channel=? AND update_id=?",
                                         (channel, update_id)).fetchone()
                if (media_row["metadata"] if media_row else None) != encoded_media:
                    raise InboxConflict("update already exists with different media metadata")
                return record, False
            now = time.time()
            conn.execute("INSERT INTO telegram_inbox VALUES (?, ?, ?, ?, ?, 'RECEIVED', ?, ?)",
                         (channel, update_id, user_id, chat_id, text, now, now))
            conn.execute("INSERT INTO telegram_inbox_scope VALUES (?, ?, ?, ?)",
                         (channel, update_id, project_id, workspace))
            if encoded_media is not None:
                conn.execute("INSERT INTO telegram_inbox_media VALUES (?, ?, ?, NULL)",
                             (channel, update_id, encoded_media))
            row = conn.execute("SELECT * FROM telegram_inbox WHERE channel=? AND update_id=?",
                               (channel, update_id)).fetchone()
            return self._record(row), True

    def media(self, *, channel: str, update_id: str) -> tuple[dict, str | None] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT metadata, text_content FROM telegram_inbox_media "
                               "WHERE channel=? AND update_id=?", (channel, update_id)).fetchone()
        return (json.loads(row["metadata"]), row["text_content"]) if row else None

    def save_media_text(self, *, channel: str, update_id: str, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("media text must be non-empty")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state FROM telegram_inbox WHERE channel=? AND update_id=?",
                               (channel, update_id)).fetchone()
            if row is None or row["state"] != "CLAIMED":
                raise InboxConflict("update is not claimed")
            row = conn.execute("SELECT text_content FROM telegram_inbox_media WHERE channel=? AND update_id=?",
                               (channel, update_id)).fetchone()
            if row is None or (row["text_content"] is not None and row["text_content"] != text):
                raise InboxConflict("media not found or already converted differently")
            conn.execute("UPDATE telegram_inbox_media SET text_content=? WHERE channel=? AND update_id=?",
                         (text, channel, update_id))

    def scope(self, *, channel: str, update_id: str) -> tuple[str, str] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT project_id, workspace FROM telegram_inbox_scope "
                               "WHERE channel=? AND update_id=?", (channel, update_id)).fetchone()
        return (row["project_id"], row["workspace"]) if row else None

    def claim(self, *, channel: str, update_id: str) -> InboxUpdate | None:
        """Atomically claim a received update once; never reclaim a claimed row."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("UPDATE telegram_inbox SET state='CLAIMED', updated_at=? "
                                  "WHERE channel=? AND update_id=? AND state='RECEIVED'",
                                  (time.time(), channel, update_id))
            if cursor.rowcount != 1:
                return None
            row = conn.execute("SELECT * FROM telegram_inbox WHERE channel=? AND update_id=?",
                               (channel, update_id)).fetchone()
            return self._record(row)

    def get(self, *, channel: str, update_id: str) -> InboxUpdate | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM telegram_inbox WHERE channel=? AND update_id=?",
                               (channel, update_id)).fetchone()
        return self._record(row) if row else None

    def rearm_if_unsubmitted(self, *, channel: str, update_id: str) -> bool:
        """Requeue a claimed text only when no remote admission record exists.

        Both tables share one SQLite file and this check is serialized with
        RemoteRunStore.claim. Commands are excluded: their effects have a
        different persistence contract.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state, text FROM telegram_inbox WHERE channel=? AND update_id=?",
                               (channel, update_id)).fetchone()
            if row is None or row["state"] != "CLAIMED" or row["text"].lstrip().startswith("/"):
                return False
            if conn.execute("SELECT 1 FROM telegram_inbox_results WHERE channel=? AND update_id=?",
                            (channel, update_id)).fetchone():
                return False
            has_runs = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='remote_runs'").fetchone()
            if has_runs and conn.execute("SELECT 1 FROM remote_runs WHERE channel=? AND update_id=?",
                                        (channel, update_id)).fetchone():
                return False
            cursor = conn.execute("UPDATE telegram_inbox SET state='RECEIVED', updated_at=? "
                                  "WHERE channel=? AND update_id=? AND state='CLAIMED'",
                                  (time.time(), channel, update_id))
            return cursor.rowcount == 1

    def settle(self, *, channel: str, update_id: str, state: str) -> InboxUpdate:
        """Mark a claimed update FINISHED or UNKNOWN; never infer remote success."""
        if state not in {"FINISHED", "UNKNOWN"}:
            raise ValueError("state must be FINISHED or UNKNOWN")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("UPDATE telegram_inbox SET state=?, updated_at=? "
                                  "WHERE channel=? AND update_id=? AND state='CLAIMED'",
                                  (state, time.time(), channel, update_id))
            if cursor.rowcount != 1:
                raise InboxConflict("update is not claimed")
            row = conn.execute("SELECT * FROM telegram_inbox WHERE channel=? AND update_id=?",
                               (channel, update_id)).fetchone()
            return self._record(row)

    def recoverable(self) -> list[InboxUpdate]:
        """Expose unfinished rows; only RECEIVED rows may be newly claimed."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM telegram_inbox WHERE state != 'FINISHED' "
                                "ORDER BY received_at, channel, update_id").fetchall()
        return [self._record(row) for row in rows]

    def save_result(self, *, channel: str, update_id: str, response: str, settled: bool) -> None:
        """Persist a response before delivery so a restart can recover it."""
        if not isinstance(response, str) or not response:
            raise ValueError("response must be non-empty text")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state FROM telegram_inbox WHERE channel=? AND update_id=?",
                               (channel, update_id)).fetchone()
            if row is None or row["state"] != "CLAIMED":
                raise InboxConflict("update is not claimed")
            conn.execute("INSERT OR IGNORE INTO telegram_inbox_results VALUES (?, ?, ?, ?)",
                         (channel, update_id, response, int(settled)))
            saved = conn.execute("SELECT response, settled FROM telegram_inbox_results "
                                 "WHERE channel=? AND update_id=?", (channel, update_id)).fetchone()
            if saved["response"] != response or bool(saved["settled"]) != settled:
                raise InboxConflict("update already has a different result")

    def get_result(self, *, channel: str, update_id: str) -> tuple[str, bool] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT response, settled FROM telegram_inbox_results "
                               "WHERE channel=? AND update_id=?", (channel, update_id)).fetchone()
        return (row["response"], bool(row["settled"])) if row else None

    def begin_delivery(self, *, channel: str, update_id: str) -> bool:
        """Record one delivery attempt before sending; uncertain sends are never retried blindly."""
        with self._connect() as conn:
            cursor = conn.execute("INSERT OR IGNORE INTO telegram_inbox_delivery_attempts VALUES (?, ?)",
                                  (channel, update_id))
            return cursor.rowcount == 1

    def delivery_attempted(self, *, channel: str, update_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM telegram_inbox_delivery_attempts WHERE channel=? AND update_id=?",
                                (channel, update_id)).fetchone() is not None

    def finish_delivery(self, *, channel: str, update_id: str, settled: bool) -> InboxUpdate:
        """Atomically record Telegram acknowledgement and close the update state."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state FROM telegram_inbox WHERE channel=? AND update_id=?",
                               (channel, update_id)).fetchone()
            if row is None or row["state"] not in {"CLAIMED", "UNKNOWN"}:
                raise InboxConflict("update cannot finish delivery")
            state = "FINISHED" if settled else "UNKNOWN"
            conn.execute("UPDATE telegram_inbox SET state=?, updated_at=? WHERE channel=? AND update_id=?",
                         (state, time.time(), channel, update_id))
            conn.execute("DELETE FROM telegram_inbox_delivery_attempts WHERE channel=? AND update_id=?",
                         (channel, update_id))
            updated = conn.execute("SELECT * FROM telegram_inbox WHERE channel=? AND update_id=?",
                                   (channel, update_id)).fetchone()
            return self._record(updated)

    def uncertain_deliveries(self, *, channel: str, user_id: str, chat_id: str,
                             project_id: str, before_update_id: str) -> list[str]:
        """List unconfirmed sends in this owner's chat/project without content."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT i.update_id FROM telegram_inbox_delivery_attempts d "
                "JOIN telegram_inbox i USING(channel, update_id) "
                "JOIN telegram_inbox_scope s USING(channel, update_id) "
                "WHERE i.channel=? AND i.user_id=? AND i.chat_id=? AND s.project_id=? "
                "AND i.update_id<? ORDER BY i.received_at",
                (channel, user_id, chat_id, project_id, before_update_id),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def save_reconciled_result(self, *, channel: str, update_id: str, response: str) -> None:
        """Replace an uncertain notice only after independent Kernel confirmation."""
        if not isinstance(response, str) or not response:
            raise ValueError("response must be non-empty text")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT state FROM telegram_inbox WHERE channel=? AND update_id=?",
                               (channel, update_id)).fetchone()
            if row is None or row["state"] not in {"CLAIMED", "UNKNOWN"}:
                raise InboxConflict("update cannot be reconciled")
            previous = conn.execute("SELECT response, settled FROM telegram_inbox_results "
                                    "WHERE channel=? AND update_id=?", (channel, update_id)).fetchone()
            if previous and previous["settled"] and previous["response"] != response:
                raise InboxConflict("update already has a confirmed result")
            conn.execute("INSERT INTO telegram_inbox_results VALUES (?, ?, ?, 1) "
                         "ON CONFLICT(channel, update_id) DO UPDATE SET response=excluded.response, settled=1",
                         (channel, update_id, response))

    def finish_reconciled(self, *, channel: str, update_id: str) -> InboxUpdate:
        """Close UNKNOWN after the caller has independently reconciled it."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("UPDATE telegram_inbox SET state='FINISHED', updated_at=? "
                                  "WHERE channel=? AND update_id=? AND state='UNKNOWN'",
                                  (time.time(), channel, update_id))
            if cursor.rowcount != 1:
                raise InboxConflict("update is not uncertain")
            row = conn.execute("SELECT * FROM telegram_inbox WHERE channel=? AND update_id=?",
                               (channel, update_id)).fetchone()
            return self._record(row)

    def purge_finished(self, *, older_than: float) -> int:
        """Delete terminal data only after the caller's replay/retention window."""
        with self._connect() as conn:
            conn.execute("DELETE FROM telegram_inbox_results WHERE (channel, update_id) IN "
                         "(SELECT channel, update_id FROM telegram_inbox WHERE state='FINISHED' AND updated_at < ?)",
                         (older_than,))
            conn.execute("DELETE FROM telegram_inbox_scope WHERE (channel, update_id) IN "
                         "(SELECT channel, update_id FROM telegram_inbox WHERE state='FINISHED' AND updated_at < ?)",
                         (older_than,))
            conn.execute("DELETE FROM telegram_inbox_delivery_attempts WHERE (channel, update_id) IN "
                         "(SELECT channel, update_id FROM telegram_inbox WHERE state='FINISHED' AND updated_at < ?)",
                         (older_than,))
            conn.execute("DELETE FROM telegram_inbox_media WHERE (channel, update_id) IN "
                         "(SELECT channel, update_id FROM telegram_inbox WHERE state='FINISHED' AND updated_at < ?)",
                         (older_than,))
            return conn.execute("DELETE FROM telegram_inbox WHERE state='FINISHED' AND updated_at < ?",
                                (older_than,)).rowcount
