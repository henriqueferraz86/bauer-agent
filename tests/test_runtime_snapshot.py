from __future__ import annotations

import sqlite3

import pytest

from bauer.core.runtime.snapshot import (
    SnapshotError, assert_runtime_writable, create_snapshot, maintenance_lock,
    restore_snapshot, verify_snapshot,
)


def _database(path, value: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE state (value TEXT)")
        conn.execute("INSERT INTO state VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def test_snapshot_copies_and_verifies_runtime_databases(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    _database(root / "runtime_state.sqlite3", "run")
    _database(root / "budget_ledger.sqlite3", "budget")

    target = create_snapshot(root, tmp_path / "snapshot")
    manifest = verify_snapshot(target)

    assert {entry["name"] for entry in manifest["databases"]} == {
        "runtime_state.sqlite3", "budget_ledger.sqlite3"
    }


def test_snapshot_rejects_changed_file(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    _database(root / "runtime_state.sqlite3", "run")
    target = create_snapshot(root, tmp_path / "snapshot")
    (target / "runtime_state.sqlite3").write_bytes(b"not sqlite")

    with pytest.raises(SnapshotError, match="Checksum"):
        verify_snapshot(target)


def test_restore_replaces_only_verified_runtime_databases(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    _database(root / "runtime_state.sqlite3", "before")
    snapshot = create_snapshot(root, tmp_path / "snapshot")
    conn = sqlite3.connect(root / "runtime_state.sqlite3")
    try:
        conn.execute("UPDATE state SET value='after'")
        conn.commit()
    finally:
        conn.close()

    restore_snapshot(root, snapshot)

    with sqlite3.connect(root / "runtime_state.sqlite3") as conn:
        assert conn.execute("SELECT value FROM state").fetchone()[0] == "before"


def test_maintenance_lock_blocks_cooperative_runtime_access(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    with maintenance_lock(root):
        with pytest.raises(RuntimeError, match="manutenção"):
            assert_runtime_writable(root)
    assert_runtime_writable(root)
