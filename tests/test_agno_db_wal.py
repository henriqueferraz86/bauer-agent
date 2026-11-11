"""Agno's SqliteDb switches journal_mode to WAL on every new connection.

On a fresh DELETE-mode file that switch needs an exclusive lock, so a new
connection waits behind any open writer (up to sqlite3's busy timeout). The
adapter puts the file in WAL once, before Agno connects, so later switches
are no-ops that don't queue behind writers.
"""

from __future__ import annotations

import sqlite3
import threading
import time

from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter


def test_agno_db_is_in_wal_before_agno_connects(tmp_path):
    adapter = AgnoRuntimeAdapter(adapter_config={"db_file": str(tmp_path / "agno.db")})

    adapter._build_db()

    with sqlite3.connect(tmp_path / "agno.db") as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_new_agno_connection_does_not_queue_behind_an_open_writer(tmp_path):
    adapter = AgnoRuntimeAdapter(adapter_config={"db_file": str(tmp_path / "agno.db")})
    adapter._build_db()

    writer = sqlite3.connect(tmp_path / "agno.db", isolation_level=None)
    writer.execute("CREATE TABLE IF NOT EXISTS hold (x)")
    writer.execute("BEGIN EXCLUSIVE")  # every DELETE-mode commit takes this lock
    writer.execute("INSERT INTO hold VALUES (1)")
    elapsed: list[float] = []

    def connect() -> None:
        started = time.monotonic()
        db = adapter._build_db()
        with db.db_engine.connect():
            pass
        elapsed.append(time.monotonic() - started)

    try:
        worker = threading.Thread(target=connect)
        worker.start()
        worker.join(timeout=30)
    finally:
        writer.execute("ROLLBACK")
        writer.close()

    assert elapsed and elapsed[0] < 2.0, elapsed
