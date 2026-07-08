"""Minimal JSONL persistence for runtime state."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


class JsonlStateStore:
    """Append-friendly store with latest-record lookup by ``id``."""

    def __init__(self, root: str | Path = "memory/runtime"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, collection: str, record: Any) -> dict[str, Any]:
        data = self._to_dict(record)
        path = self._path(collection)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")
        return data

    def upsert(self, collection: str, record: Any) -> dict[str, Any]:
        data = self._to_dict(record)
        return self.append(collection, data)

    def latest(self, collection: str, record_id: str) -> dict[str, Any] | None:
        for record in reversed(self.list(collection)):
            if record.get("id") == record_id:
                return record
        return None

    def list(self, collection: str) -> list[dict[str, Any]]:
        path = self._path(collection)
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
        latest_by_id: dict[str, dict[str, Any]] = {}
        for record in self.list(collection):
            record_id = str(record.get("id", ""))
            if record_id:
                latest_by_id[record_id] = record
        return sorted(latest_by_id.values(), key=lambda r: str(r.get("updated_at") or r.get("started_at") or ""))

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
