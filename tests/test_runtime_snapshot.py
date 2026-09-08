from __future__ import annotations

import sqlite3

import pytest

from bauer.core.runtime.snapshot import SnapshotError, create_snapshot, verify_snapshot


def _database(path, value: str) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE state (value TEXT)")
        conn.execute("INSERT INTO state VALUES (?)", (value,))


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
