"""Snapshots verificáveis dos bancos operacionais do runtime."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

RUNTIME_DATABASES = ("runtime_state.sqlite3", "budget_ledger.sqlite3")
MANIFEST_NAME = "manifest.json"


class SnapshotError(ValueError):
    """Snapshot ausente, incompleto ou inconsistente."""


def create_snapshot(root: str | Path, destination: str | Path) -> Path:
    """Copia bancos SQLite online usando a API consistente do próprio SQLite."""
    source_root = Path(root)
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=False)
    databases: list[dict[str, str | int]] = []
    try:
        for name in RUNTIME_DATABASES:
            source = source_root / name
            if not source.exists():
                continue
            copied = target / name
            with sqlite3.connect(str(source), timeout=5.0) as origin:
                with sqlite3.connect(str(copied)) as snapshot:
                    origin.backup(snapshot)
            _integrity_check(copied)
            databases.append({
                "name": name,
                "sha256": _sha256(copied),
                "bytes": copied.stat().st_size,
            })
        if not databases:
            raise SnapshotError(f"Nenhum banco de runtime encontrado em {source_root}")
        manifest = {
            "version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "databases": databases,
            "excluded": ["sessions", "decision memory", "credentials"],
        }
        (target / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except Exception:
        for path in target.iterdir():
            path.unlink()
        target.rmdir()
        raise
    return target


def verify_snapshot(destination: str | Path) -> dict:
    """Valida manifesto, hashes e integridade de cada banco copiado."""
    target = Path(destination)
    try:
        manifest = json.loads((target / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError("Manifesto de snapshot ausente ou inválido") from exc
    databases = manifest.get("databases")
    if not isinstance(databases, list) or not databases:
        raise SnapshotError("Manifesto sem bancos de runtime")
    for entry in databases:
        if not isinstance(entry, dict) or entry.get("name") not in RUNTIME_DATABASES:
            raise SnapshotError("Manifesto contém banco não permitido")
        path = target / str(entry["name"])
        if not path.is_file() or _sha256(path) != entry.get("sha256"):
            raise SnapshotError(f"Checksum inválido: {entry['name']}")
        _integrity_check(path)
    return manifest


def _integrity_check(path: Path) -> None:
    try:
        with sqlite3.connect(str(path), timeout=5.0) as conn:
            result = conn.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise SnapshotError(f"Banco inválido: {path.name}") from exc
    if result is None or result[0] != "ok":
        raise SnapshotError(f"Integridade inválida: {path.name}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
