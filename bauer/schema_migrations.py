"""Shared schema migration ledger for Bauer sidecar stores."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


MigrationFn = Callable[[], None]


@dataclass(frozen=True)
class MigrationRecord:
    store: str
    version: int
    name: str
    applied_at: str


class MigrationLedger:
    """Idempotent migration ledger scoped to one workspace."""

    def __init__(self, workspace: str | Path = "workspace"):
        self.workspace = Path(workspace).resolve()
        self.store_dir = self.workspace / ".bauer_meta"
        self.db_path = self.store_dir / "schema_migrations.sqlite3"

    def apply_once(self, *, store: str, version: int, name: str, fn: MigrationFn | None = None) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE store = ? AND version = ?",
                (store, int(version)),
            ).fetchone()
            if row:
                return False
            if fn:
                fn()
            conn.execute(
                """
                INSERT INTO schema_migrations (store, version, name, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (store, int(version), name, _now_iso()),
            )
        return True

    def list_records(self) -> list[MigrationRecord]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM schema_migrations ORDER BY store ASC, version ASC"
            ).fetchall()
        return [
            MigrationRecord(
                store=row["store"],
                version=int(row["version"]),
                name=row["name"],
                applied_at=row["applied_at"],
            )
            for row in rows
        ]

    def _connect(self) -> sqlite3.Connection:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
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
            CREATE TABLE IF NOT EXISTS schema_migrations (
                store TEXT NOT NULL,
                version INTEGER NOT NULL,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                PRIMARY KEY (store, version)
            );
            """
        )


def ensure_level8_migrations(workspace: str | Path = "workspace") -> list[MigrationRecord]:
    """Record the current baseline migrations for all known sidecar stores."""
    ledger = MigrationLedger(workspace)
    baselines = [
        ("kanban", 1, "kanban events and task runs"),
        ("orchestration", 1, "orchestration runs and nodes"),
        ("automation", 1, "automation jobs and runs"),
        ("gateway_outbox", 1, "gateway delivery outbox"),
        ("memory_index", 1, "memory FTS index"),
        ("trajectory", 1, "research trajectory jsonl manifest"),
    ]
    for store, version, name in baselines:
        ledger.apply_once(store=store, version=version, name=name)
    return ledger.list_records()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
