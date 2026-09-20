"""Persistent DAG execution for hierarchical agent teams."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..events import EventBus
from .team_registry import DelegationManager
from .state_store import RuntimeStateStore, SqliteStateStore


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class TeamRun:
    id: str
    team_id: str
    objective: str
    status: str = "running"
    task_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    error: str | None = None


@dataclass
class TeamTask:
    id: str
    team_run_id: str
    agent_id: str
    objective: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    delegation_id: str | None = None
    run_id: str | None = None
    output: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)


class TeamRunManager:
    def __init__(
        self,
        *,
        root: str | Path = "memory/runtime",
        store: RuntimeStateStore | None = None,
        delegation_manager: DelegationManager | None = None,
        event_bus: EventBus | None = None,
    ):
        self.store = store or SqliteStateStore(root)
        self.event_bus = event_bus or EventBus(store=self.store)
        self.delegation_manager = delegation_manager or DelegationManager(root=root, event_bus=self.event_bus)

    def start_team(
        self,
        *,
        team_id: str,
        objective: str,
        tasks: list[dict[str, Any]],
    ) -> TeamRun:
        team = self.delegation_manager.team_registry.get(team_id)
        if team is None:
            raise KeyError(f"Team not found: {team_id}")
        normalized = self._validate_tasks(team, tasks)
        run = TeamRun(
            id=f"team-run-{uuid4()}",
            team_id=team.id,
            objective=objective,
            task_ids=[task.id for task in normalized],
        )
        self.store.append("team_runs", run)
        for task in normalized:
            task.team_run_id = run.id
            self.store.append("team_tasks", task)
        self.advance(run.id)
        return self.get_run(run.id) or run

    def advance(self, team_run_id: str) -> TeamRun:
        run = self.get_run(team_run_id)
        if run is None:
            raise KeyError(f"Team run not found: {team_run_id}")
        if run.status in {"completed", "failed", "cancelled"}:
            return run
        team = self.delegation_manager.team_registry.get(run.team_id)
        if team is None:
            return self._update_run(run, status="failed", error="team not found")
        coordinator = team.coordinator or str(team.coordination.get("supervisor") or "")
        tasks = self.list_tasks(team_run_id)
        by_id = {task.id: task for task in tasks}
        for task in tasks:
            if task.status != "pending" or any(by_id[item].status != "completed" for item in task.depends_on):
                continue
            record = self.delegation_manager.delegate(
                team_id=team.id,
                from_agent_id=coordinator,
                to_agent_id=task.agent_id,
                input={
                    "objective": task.objective,
                    "team_run_id": run.id,
                    "task_id": task.id,
                },
            )
            if record.status != "accepted":
                task.status = "failed"
                task.error = record.reason
                self._save_task(task)
                self.event_bus.publish(
                    "agent.failed",  # type: ignore[arg-type]
                    agent_id=task.agent_id,
                    status=task.status,
                    message=task.error,
                    data={"team_run_id": run.id, "task_id": task.id},
                )
                continue
            task.status = "queued"
            task.delegation_id = record.id
            task.run_id = record.run_id
            self._save_task(task)
            self.event_bus.publish(
                "task.delegated",  # type: ignore[arg-type]
                run_id=record.run_id,
                agent_id=task.agent_id,
                status=task.status,
                message=task.objective,
                data={"team_run_id": run.id, "task_id": task.id, "delegation_id": record.id},
            )
        tasks = self.list_tasks(team_run_id)
        if all(task.status == "completed" for task in tasks):
            return self._update_run(run, status="completed")
        if any(task.status == "failed" for task in tasks):
            return self._update_run(run, status="failed", error="team task failed")
        return self._update_run(run, status="running")

    def complete_task(self, team_run_id: str, task_id: str, *, output: dict[str, Any] | None = None) -> TeamTask:
        task = self.get_task(team_run_id, task_id)
        if task is None:
            raise KeyError(f"Team task not found: {task_id}")
        if task.status == "completed":
            return task
        if task.status != "queued":
            raise ValueError("only a queued team task can be completed")
        task.status = "completed"
        task.output = output or {}
        self._save_task(task)
        self.event_bus.publish(
            "agent.finished",  # type: ignore[arg-type]
            run_id=task.run_id,
            agent_id=task.agent_id,
            status=task.status,
            data={"team_run_id": team_run_id, "task_id": task.id},
        )
        self.advance(team_run_id)
        return self.get_task(team_run_id, task_id) or task

    def get_run(self, team_run_id: str) -> TeamRun | None:
        data = self.store.latest("team_runs", team_run_id)
        return TeamRun(**data) if data else None

    def list_tasks(self, team_run_id: str) -> list[TeamTask]:
        return [
            TeamTask(**item)
            for item in self.store.list_latest("team_tasks")
            if item.get("team_run_id") == team_run_id
        ]

    def get_task(self, team_run_id: str, task_id: str) -> TeamTask | None:
        data = self.store.latest("team_tasks", f"{team_run_id}:{task_id}")
        if data:
            return TeamTask(**data)
        for task in self.list_tasks(team_run_id):
            if task.id == task_id:
                return task
        return None

    def _validate_tasks(self, team: Any, raw_tasks: list[dict[str, Any]]) -> list[TeamTask]:
        if not raw_tasks:
            raise ValueError("team tasks must not be empty")
        task_ids = [str(item.get("id") or "").strip() for item in raw_tasks]
        if any(not task_id for task_id in task_ids) or len(set(task_ids)) != len(task_ids):
            raise ValueError("team task ids must be unique and non-empty")
        known = set(task_ids)
        tasks: list[TeamTask] = []
        for item, task_id in zip(raw_tasks, task_ids):
            agent_id = str(item.get("agent") or item.get("agent_id") or "").strip()
            if agent_id not in team.agents:
                raise ValueError(f"agent is not a team member: {agent_id}")
            dependencies = _string_list(item.get("depends_on"))
            missing = [dependency for dependency in dependencies if dependency not in known]
            if missing:
                raise ValueError(f"unknown dependency: {missing[0]}")
            tasks.append(
                TeamTask(
                    id=task_id,
                    team_run_id="pending",
                    agent_id=agent_id,
                    objective=str(item.get("objective") or item.get("goal") or ""),
                    depends_on=dependencies,
                )
            )
        graph = {task.id: task.depends_on for task in tasks}
        if _has_cycle(graph):
            raise ValueError("team task graph contains a cycle")
        return tasks

    def _save_task(self, task: TeamTask) -> TeamTask:
        task.updated_at = _now_iso()
        self.store.append("team_tasks", task)
        return task

    def _update_run(self, run: TeamRun, *, status: str, error: str | None = None) -> TeamRun:
        run.status = status
        run.error = error
        run.updated_at = _now_iso()
        self.store.append("team_runs", run)
        return run


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(parent) for parent in graph[node]):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []
