"""Snapshot SQLite databases from a mounted Bauer memory volume."""
from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path


SOURCE = Path(os.environ.get("BAUER_BACKUP_SOURCE", "/source"))
DESTINATION = Path(os.environ.get("BAUER_BACKUP_DESTINATION", "/backup"))
DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def main() -> None:
    if not SOURCE.is_dir():
        raise SystemExit("backup source is unavailable")
    DESTINATION.mkdir(parents=True, exist_ok=True)
    databases = sorted(path for path in SOURCE.rglob("*")
                       if path.is_file() and not path.is_symlink() and path.suffix.lower() in DATABASE_SUFFIXES)
    if not databases:
        raise SystemExit("no SQLite databases found in the memory volume")
    checked = 0
    for source_path in databases:
        relative = source_path.relative_to(SOURCE)
        target_path = DESTINATION / relative
        target_path.parent.mkdir(parents=True, exist_ok=True)
        uri = source_path.resolve().as_uri()
        try:
            with closing(sqlite3.connect(uri, uri=True, timeout=30)) as source_db:
                source_db.execute("PRAGMA query_only=ON")
                with closing(sqlite3.connect(target_path)) as target_db:
                    source_db.backup(target_db)
                    result = target_db.execute("PRAGMA quick_check").fetchone()
                    if not result or result[0] != "ok":
                        raise SystemExit("SQLite backup validation failed")
        except sqlite3.Error as exc:
            raise SystemExit(f"SQLite backup failed for {relative.name}: {type(exc).__name__}") from None
        os.chmod(target_path, 0o600)
        checked += 1
    print(f"SQLite backup complete: {checked} databases passed quick_check.")


if __name__ == "__main__":
    os.umask(0o077)
    main()
