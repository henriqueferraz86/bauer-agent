"""The runtime store switches its SQLite file to WAL once per process.

journal_mode persists in the file. Re-issuing the pragma on every connection,
while holding the process-wide write lock with a 30s busy timeout, let one
lock wait on the file (on Windows, rebuilding the WAL after the closing
checkpoint) freeze every runtime thread behind that lock.
"""

from __future__ import annotations

import sqlite3

import bauer.core.runtime.state_store as state_store


def test_journal_mode_pragma_runs_once_per_file(tmp_path, monkeypatch):
    statements: list[str] = []
    real_connect = sqlite3.connect

    def traced_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(state_store.sqlite3, "connect", traced_connect)
    store = state_store.SqliteStateStore(tmp_path / "runtime")
    for index in range(5):
        store.upsert("runs", {"id": f"run-{index}", "status": "running"})
        store.latest("runs", f"run-{index}")

    pragmas = [s for s in statements if "journal_mode" in s.lower()]
    assert len(pragmas) == 1, pragmas
    with real_connect(store.path) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
