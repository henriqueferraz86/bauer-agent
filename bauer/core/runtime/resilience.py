"""Runtime resilience: worker heartbeats, kill switch and recovery."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .run_manager import TERMINAL_RUN_STATUSES, RunManager
from .state_store import JsonlStateStore


@dataclass(slots=True)
class WorkerHeartbeat:
    id: str
    status: str
    pid: int
    started_at: str
    last_seen_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkerRegistry:
    def __init__(self, *, root: str | Path = "memory/runtime", store: JsonlStateStore | None = None):
        self.store = store or JsonlStateStore(root)

    def heartbeat(self, worker_id: str, *, status: str = "online", metadata: dict[str, Any] | None = None) -> WorkerHeartbeat:
        now = _now_iso()
        current = self.get(worker_id)
        record = WorkerHeartbeat(
            id=worker_id,
            status=status,
            pid=os.getpid(),
            started_at=current.started_at if current else now,
            last_seen_at=now,
            metadata=metadata or (current.metadata if current else {}),
        )
        self.store.upsert("workers", record)
        return record

    def get(self, worker_id: str) -> WorkerHeartbeat | None:
        data = self.store.latest("workers", worker_id)
        return WorkerHeartbeat(**data) if data else None

    def list(self, *, stale_after_s: int = 90) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(seconds=stale_after_s)
        workers = [WorkerHeartbeat(**record) for record in self.store.list_latest("workers")]
        result: list[dict[str, Any]] = []
        for worker in workers:
            status = worker.status
            if _parse_datetime(worker.last_seen_at) < cutoff:
                status = "offline"
            result.append({**_worker_to_dict(worker), "computed_status": status})
        return result


class RuntimeControl:
    def __init__(self, *, root: str | Path = "memory/runtime", store: JsonlStateStore | None = None):
        self.store = store or JsonlStateStore(root)

    def set_kill_switch(self, enabled: bool) -> dict[str, Any]:
        record = {
            "id": "kill_switch",
            "enabled": bool(enabled),
            "updated_at": _now_iso(),
        }
        self.store.upsert("runtime_control", record)
        return record

    def kill_switch_enabled(self) -> bool:
        record = self.store.latest("runtime_control", "kill_switch")
        return bool(record and record.get("enabled"))


class RuntimeRecovery:
    def __init__(self, *, root: str | Path = "memory/runtime", store: JsonlStateStore | None = None):
        self.store = store or JsonlStateStore(root)
        self.run_manager = RunManager(store=self.store)

    def recover_stuck_runs(self, *, max_age_s: int = 900) -> list[dict[str, Any]]:
        cutoff = datetime.now(UTC) - timedelta(seconds=max_age_s)
        recovered: list[dict[str, Any]] = []
        for run in self.run_manager.list_runs():
            if run.status in TERMINAL_RUN_STATUSES:
                continue
            marker = run.updated_at or run.started_at
            if _parse_datetime(marker) > cutoff:
                continue
            failed = self.run_manager.fail_run(run.id, f"runtime recovery: run stuck for more than {max_age_s}s")
            recovered.append({"run_id": failed.id, "status": failed.status, "error": failed.error})
        return recovered


def _worker_to_dict(worker: WorkerHeartbeat) -> dict[str, Any]:
    return {
        "id": worker.id,
        "status": worker.status,
        "pid": worker.pid,
        "started_at": worker.started_at,
        "last_seen_at": worker.last_seen_at,
        "metadata": dict(worker.metadata),
    }


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
