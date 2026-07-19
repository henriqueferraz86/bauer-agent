"""Minimal JSONL persistence for runtime state."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

# Locks POR ARQUIVO (não por instância): scheduler, run_manager, event_bus,
# budget e a thread do /stream escrevem/leem os MESMOS .jsonl, muitas vezes por
# instâncias distintas de JsonlStateStore apontando pro mesmo root. Um lock por
# instância não coordenaria essas — o lock keyed pelo caminho resolvido sim.
# RLock porque latest()/list_latest() chamam list() (re-entrância na mesma thread).
_FILE_LOCKS: dict[str, threading.RLock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _FILE_LOCKS_GUARD:
        lock = _FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _FILE_LOCKS[key] = lock
        return lock


class JsonlStateStore:
    """Append-friendly store with latest-record lookup by ``id``.

    Serializa append/list por um lock por-arquivo — sem isso, um append
    concorrente podia deixar uma linha parcial que o list() pulava (leitura
    obsoleta), ou dois writes se intercalavam corrompendo o JSONL.
    """

    def __init__(self, root: str | Path = "memory/runtime"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, collection: str, record: Any) -> dict[str, Any]:
        data = self._to_dict(record)
        path = self._path(collection)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n"
        with _lock_for(path):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
        return data

    def upsert(self, collection: str, record: Any) -> dict[str, Any]:
        data = self._to_dict(record)
        return self.append(collection, data)

    def latest(self, collection: str, record_id: str) -> dict[str, Any] | None:
        with _lock_for(self._path(collection)):
            for record in reversed(self.list(collection)):
                if record.get("id") == record_id:
                    return record
        return None

    def list(self, collection: str) -> list[dict[str, Any]]:
        path = self._path(collection)
        with _lock_for(path):
            if not path.exists():
                return []
            records: list[dict[str, Any]] = []
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict):
                        records.append(item)
            return records

    def list_latest(self, collection: str) -> list[dict[str, Any]]:
        with _lock_for(self._path(collection)):
            latest_by_id: dict[str, dict[str, Any]] = {}
            for record in self.list(collection):
                record_id = str(record.get("id", ""))
                if record_id:
                    latest_by_id[record_id] = record
            return sorted(
                latest_by_id.values(),
                key=lambda r: str(r.get("updated_at") or r.get("started_at") or ""),
            )

    def _path(self, collection: str) -> Path:
        safe = collection.strip().replace("/", "_").replace("\\", "_")
        return self.root / f"{safe}.jsonl"

    @staticmethod
    def _to_dict(record: Any) -> dict[str, Any]:
        if is_dataclass(record):
            return asdict(record)
        if isinstance(record, dict):
            return dict(record)
        raise TypeError(f"Unsupported record type: {type(record)!r}")
