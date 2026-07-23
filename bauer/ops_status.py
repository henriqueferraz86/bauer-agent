"""Shared operational status projection for dispatcher/Kanban runtime."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent_lanes import UNLIMITED_LANE_CONCURRENCY, resolve_agent_lane
from .automation_store import AutomationStore
from .gateway_channels import GatewayChannelRegistry
from .gateway_outbox import GatewayOutbox
from .kanban_store import KanbanStore
from .orchestration_store import OrchestrationStore
from .schema_migrations import MigrationLedger
from .supervisor import RuntimeSupervisor
from .trajectory_store import TrajectoryStore
from .workspace_manager import Task
from .workspace_manager_factory import get_workspace_manager


def build_ops_status(
    workspace: str | Path = "workspace",
    *,
    limit: int = 10,
) -> dict[str, Any]:
    workspace_path = Path(workspace).resolve()
    wm = get_workspace_manager(workspace_path)
    store = KanbanStore(workspace_path)
    tasks = wm.list_tasks()
    now = int(time.time())

    status_counts = Counter(task.status for task in tasks)
    lanes: dict[str, dict[str, Any]] = {}
    active_claims: list[dict[str, Any]] = []

    for task in tasks:
        selection = resolve_agent_lane(task, workspace=workspace_path)
        lane_name = task.metadata.get("lane") or selection.lane or "default"
        lane = lanes.setdefault(
            lane_name,
            {
                "lane": lane_name,
                "agent": selection.agent or task.metadata.get("agent", ""),
                "capability": selection.capability or task.metadata.get("capability", ""),
                "configured": bool(selection.configured),
                "max_concurrent": _display_capacity(selection.max_concurrent),
                "counts": {},
                "ready": 0,
                "running": 0,
                "blocked": 0,
                "failed": 0,
                "done": 0,
                "todo": 0,
            },
        )
        lane["counts"][task.status] = int(lane["counts"].get(task.status, 0)) + 1
        if task.status == "READY":
            lane["ready"] += 1
        elif task.status == "IN_PROGRESS":
            lane["running"] += 1
        elif task.status == "BLOCKED":
            lane["blocked"] += 1
        elif task.status == "FAILED":
            lane["failed"] += 1
        elif task.status == "DONE":
            lane["done"] += 1
        elif task.status == "TODO":
            lane["todo"] += 1

        if task.status == "IN_PROGRESS" and task.metadata.get("dispatch") == "true":
            active_claims.append(_active_claim(task, now))

    runs = [asdict(run) for run in store.list_runs(limit=limit)]
    events = [asdict(event) for event in store.list_events(limit=limit)]
    orch_store = OrchestrationStore(workspace_path)
    orchestrations = []
    for run in orch_store.list_runs(limit=limit):
        data = asdict(run)
        data["nodes"] = [asdict(node) for node in orch_store.list_nodes(run.run_id)]
        orchestrations.append(data)
    automation_store = AutomationStore(workspace_path)
    automation_jobs = [asdict(job) for job in automation_store.list_jobs(limit=limit)]
    automation_runs = [asdict(run) for run in automation_store.list_runs(limit=limit)]
    outbox_messages = [asdict(message) for message in GatewayOutbox(workspace_path).list_messages(limit=limit)]
    try:
        gateway_channels = [channel.to_public_dict() for channel in GatewayChannelRegistry(workspace_path).list_channels(include_disabled=True)]
    except Exception as exc:
        gateway_channels = [{"error": str(exc)}]
    migrations = [asdict(record) for record in MigrationLedger(workspace_path).list_records()]
    trajectories = [asdict(record) for record in TrajectoryStore(workspace_path).list(limit=limit)]
    try:
        runtime_supervisor = RuntimeSupervisor(workspace_path).status().to_public_dict()
    except Exception as exc:
        runtime_supervisor = {"state": "unknown", "error": str(exc)}
    lane_list = sorted(
        lanes.values(),
        key=lambda item: (-int(item.get("running", 0)), -int(item.get("ready", 0)), item["lane"]),
    )

    return {
        "workspace": str(workspace_path),
        "generated_at": datetime.now().astimezone().isoformat(),
        "status_counts": {status: int(status_counts.get(status, 0)) for status in (
            "TODO",
            "READY",
            "IN_PROGRESS",
            "BLOCKED",
            "FAILED",
            "DONE",
        )},
        "total_tasks": len(tasks),
        "lanes": lane_list,
        "active_claims": active_claims,
        "recent_runs": runs,
        "recent_events": events,
        "recent_orchestrations": orchestrations,
        "automation_jobs": automation_jobs,
        "automation_runs": automation_runs,
        "gateway_outbox": outbox_messages,
        "gateway_channels": gateway_channels,
        "schema_migrations": migrations,
        "trajectories": trajectories,
        "runtime_supervisor": runtime_supervisor,
    }


def _active_claim(task: Task, now: int) -> dict[str, Any]:
    expires = _to_int(task.metadata.get("claim_expires"))
    heartbeat_epoch = _parse_iso_epoch(task.metadata.get("heartbeat_at", ""))
    pid = _to_int(task.metadata.get("worker_pid")) or None
    return {
        "task_id": task.id,
        "public_id": _public_id(task.id),
        "title": task.title,
        "lane": task.metadata.get("lane") or task.assignee or "default",
        "agent": task.metadata.get("agent") or task.assignee,
        "claim_id": task.metadata.get("claim_id", ""),
        "claim_expires": expires,
        "claim_seconds_left": expires - now if expires else None,
        "claim_expired": bool(expires and expires <= now),
        "heartbeat_at": task.metadata.get("heartbeat_at", ""),
        "heartbeat_age_seconds": now - heartbeat_epoch if heartbeat_epoch else None,
        "run_id": task.metadata.get("run_id", ""),
        "worker_pid": pid,
        "worker_alive": _pid_alive(pid) if pid else None,
        "log": task.metadata.get("log", ""),
    }


def _display_capacity(value: int) -> int | str:
    return "unlimited" if value >= UNLIMITED_LANE_CONCURRENCY else max(1, int(value))


def _to_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _parse_iso_epoch(value: str) -> int:
    if not value:
        return 0
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return 0


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except Exception:
        return True


def _public_id(task_id: str) -> str:
    raw = str(task_id).strip()
    if raw.upper().startswith("T"):
        raw = raw[1:]
    return f"T{int(raw):04d}" if raw.isdigit() else raw
