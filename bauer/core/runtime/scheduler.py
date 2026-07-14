"""Persistent runtime scheduler and local worker."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from ..events import EventBus
from .autonomy import BudgetExceededError, BudgetManager
from .run_manager import RunManager
from .resilience import RuntimeControl, WorkerRegistry
from .session_manager import SessionManager
from .state_store import JsonlStateStore


TaskStatus = str
AdapterFactory = Callable[[str], Any]


@dataclass(slots=True)
class TaskDefinition:
    id: str
    name: str
    agent_id: str
    runtime_adapter: str
    schedule: dict[str, Any]
    input: dict[str, Any]
    policy: dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = "active"
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())
    next_run_at: str | None = None
    last_run_id: str | None = None
    last_error: str | None = None
    run_count: int = 0


class Scheduler:
    def __init__(
        self,
        *,
        root: str | Path = "memory/runtime",
        store: JsonlStateStore | None = None,
        event_bus: EventBus | None = None,
        adapter_factory: AdapterFactory | None = None,
    ) -> None:
        self.root = Path(root)
        self.store = store or JsonlStateStore(self.root)
        self.event_bus = event_bus or EventBus(store=self.store)
        self.run_manager = RunManager(store=self.store, event_bus=self.event_bus)
        self.session_manager = SessionManager(store=self.store)
        self.runtime_control = RuntimeControl(store=self.store)
        self.worker_registry = WorkerRegistry(store=self.store)
        self.budget_manager = BudgetManager(store=self.store, event_bus=self.event_bus)
        self.adapter_factory = adapter_factory or _default_adapter_factory

    def add_task(self, task: TaskDefinition | dict[str, Any]) -> TaskDefinition:
        definition = task if isinstance(task, TaskDefinition) else task_from_mapping(task)
        if not definition.next_run_at:
            definition.next_run_at = next_run_after(definition.schedule)
        self.store.upsert("scheduled_tasks", definition)
        return definition

    def get_task(self, task_id: str) -> TaskDefinition | None:
        data = self.store.latest("scheduled_tasks", task_id)
        if not data or data.get("status") == "deleted":
            return None
        return TaskDefinition(**data)

    def list_tasks(self, *, include_deleted: bool = False) -> list[TaskDefinition]:
        tasks = [TaskDefinition(**item) for item in self.store.list_latest("scheduled_tasks")]
        if not include_deleted:
            tasks = [task for task in tasks if task.status != "deleted"]
        return tasks

    def pause_task(self, task_id: str) -> TaskDefinition:
        return self._update_task(task_id, status="paused")

    def resume_task(self, task_id: str) -> TaskDefinition:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        return self._update_task(task_id, status="active", next_run_at=task.next_run_at or next_run_after(task.schedule))

    def delete_task(self, task_id: str) -> TaskDefinition:
        return self._update_task(task_id, status="deleted")

    def due_tasks(self, *, now: datetime | None = None) -> list[TaskDefinition]:
        now = now or datetime.now(UTC)
        due: list[TaskDefinition] = []
        for task in self.list_tasks():
            if task.status != "active":
                continue
            if not task.next_run_at:
                task = self._update_task(task.id, next_run_at=next_run_after(task.schedule, after=now))
            if _parse_datetime(task.next_run_at) <= now:
                due.append(task)
        return due

    def tick(self, *, now: datetime | None = None, max_tasks: int = 10) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for task in self.due_tasks(now=now)[:max_tasks]:
            try:
                results.append(self.run_task(task.id, manual=False))
            except Exception as exc:  # noqa: BLE001
                self.event_bus.publish(
                    "schedule.failed",
                    agent_id=task.agent_id,
                    status="failed",
                    message=str(exc),
                    data={"task_id": task.id},
                )
                results.append({"task_id": task.id, "status": "failed", "error": str(exc)})
        return results

    def run_task(self, task_id: str, *, manual: bool = True) -> dict[str, Any]:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        if self.runtime_control.kill_switch_enabled():
            self.event_bus.publish(
                "schedule.skipped",
                agent_id=task.agent_id,
                status="blocked",
                message="runtime kill switch is active",
                data={"task_id": task.id, "manual": manual},
            )
            if not manual:
                # Adia p/ não re-disparar a cada tick enquanto o kill-switch durar.
                self._reschedule_skipped(task)
            return {"task_id": task.id, "status": "blocked", "reason": "kill_switch"}
        if task.status != "active" and not manual:
            self.event_bus.publish(
                "schedule.skipped",
                agent_id=task.agent_id,
                status=task.status,
                message="scheduled task is not active",
                data={"task_id": task.id},
            )
            return {"task_id": task.id, "status": "skipped", "reason": "not active"}

        estimated_cost = float(task.policy.get("estimated_cost_usd", task.policy.get("max_cost_usd", 0)) or 0)
        company_id = str(task.input.get("company_id") or task.policy.get("company_id") or "") or None
        try:
            self.budget_manager.ensure_can_start(
                agent_id=task.agent_id,
                company_id=company_id,
                estimated_cost_usd=estimated_cost,
            )
        except BudgetExceededError as exc:
            self.event_bus.publish(
                "schedule.skipped",
                agent_id=task.agent_id,
                status="blocked",
                message=str(exc),
                data={"task_id": task.id, "reason": "budget"},
            )
            if not manual:
                # Adia p/ não re-disparar a cada tick enquanto o budget estiver estourado.
                self._reschedule_skipped(task)
            return {"task_id": task.id, "status": "blocked", "reason": "budget", "error": str(exc)}

        session = self.session_manager.get_or_create_session(
            f"schedule-{task.id}",
            agent_id=task.agent_id,
            state={"task_id": task.id, "scheduler": "local"},
        )
        run = self.run_manager.create_run(
            session_id=session.id,
            agent_id=task.agent_id,
            runtime_adapter=task.runtime_adapter,
            input={"task_id": task.id, **task.input},
            status="running",
        )
        self.event_bus.publish(
            "schedule.triggered",
            run_id=run.id,
            session_id=session.id,
            agent_id=task.agent_id,
            status="triggered",
            data={"task_id": task.id, "manual": manual, "schedule": task.schedule},
        )

        retry_count = max(0, int(task.policy.get("retry_count", 0) or 0))
        retry_backoff = max(0.0, float(task.policy.get("retry_backoff", 0) or 0))
        profile = self.budget_manager.get_profile()
        max_runtime_s = max(
            0.0,
            float(task.policy.get("max_runtime_s", profile.max_runtime_s_per_run) or 0),
        )
        max_tool_calls = int(task.policy.get("max_tool_calls", profile.max_tool_calls_per_run) or 0)
        started = time.monotonic()
        last_error = ""
        for attempt in range(retry_count + 1):
            if attempt > 0 and retry_backoff:
                time.sleep(retry_backoff)
            adapter = self.adapter_factory(task.runtime_adapter)
            try:
                result = adapter.run_agent(
                    {
                        "run_id": run.id,
                        "session_id": session.id,
                        "agent_id": task.agent_id,
                        "task": task.input.get("message") or task.input,
                        "input": task.input,
                        "policy": task.policy,
                    }
                )
                if max_runtime_s and time.monotonic() - started > max_runtime_s:
                    raise TimeoutError(f"scheduled task exceeded max_runtime_s={max_runtime_s:g}")
                if result.get("status") == "failed":
                    raise RuntimeError(str(result.get("error") or "scheduled task failed"))
                output = dict(result)
                cost = float(output.get("cost_estimate") or output.get("cost_usd") or estimated_cost or 0)
                tool_calls = int(output.get("tool_calls_count") or 0)
                if max_tool_calls and tool_calls > max_tool_calls:
                    raise RuntimeError(f"scheduled task exceeded max_tool_calls={max_tool_calls}")
                self.run_manager.complete_run(
                    run.id,
                    output=output,
                    cost_estimate=cost,
                    tool_calls_count=tool_calls,
                )
                self.budget_manager.record_run_cost(
                    run_id=run.id,
                    agent_id=task.agent_id,
                    company_id=company_id,
                    cost_usd=cost,
                    metadata={"task_id": task.id},
                )
                self._after_run(task, run.id)
                return {
                    "task_id": task.id,
                    "run_id": run.id,
                    "status": "completed",
                    "attempts": attempt + 1,
                    "result": result,
                }
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                if attempt < retry_count:
                    continue
                self.run_manager.fail_run(run.id, last_error)
                self.event_bus.publish(
                    "schedule.failed",
                    run_id=run.id,
                    session_id=session.id,
                    agent_id=task.agent_id,
                    status="failed",
                    message=last_error,
                    data={"task_id": task.id, "attempts": attempt + 1},
                )
                self._after_run(task, run.id, error=last_error)
                return {
                    "task_id": task.id,
                    "run_id": run.id,
                    "status": "failed",
                    "attempts": attempt + 1,
                    "error": last_error,
                }

    def start_worker(
        self,
        *,
        worker_id: str = "local-worker",
        interval_s: float = 30.0,
        once: bool = False,
        max_tasks_per_tick: int = 10,
        stop_after_ticks: int | None = None,
    ) -> None:
        ticks = 0
        while True:
            self.worker_registry.heartbeat(
                worker_id,
                metadata={"interval_s": interval_s, "max_tasks_per_tick": max_tasks_per_tick},
            )
            self.tick(max_tasks=max_tasks_per_tick)
            ticks += 1
            if once or (stop_after_ticks is not None and ticks >= stop_after_ticks):
                return
            time.sleep(max(0.1, interval_s))

    #: Quanto adiar uma tarefa `once` bloqueada por budget/kill-switch antes de
    #: re-tentar (recorrentes avançam para o próximo slot do cron). Sem isso, uma
    #: tarefa bloqueada ficaria "due" e re-dispararia a CADA tick, gerando spam de
    #: eventos schedule.skipped e churn indefinido.
    _skip_backoff_s: int = 300

    def _reschedule_skipped(self, task: TaskDefinition, *, now: datetime | None = None) -> None:
        """Empurra `next_run_at` para frente após um skip (budget/kill-switch),
        para a tarefa não re-disparar a cada tick. NÃO conta como execução
        (run_count/last_run_id intocados) — só adia a próxima tentativa."""
        now = now or datetime.now(UTC)
        if task.schedule.get("type") == "once":
            next_run = (now + timedelta(seconds=self._skip_backoff_s)).isoformat()
        else:
            next_run = next_run_after(task.schedule, after=now)
        self._update_task(task.id, next_run_at=next_run)

    def _after_run(self, task: TaskDefinition, run_id: str, *, error: str | None = None) -> TaskDefinition:
        is_once = task.schedule.get("type") == "once"
        next_run = None if is_once else next_run_after(task.schedule)
        changes: dict[str, Any] = {
            "next_run_at": next_run,
            "last_run_id": run_id,
            "last_error": error,
            "run_count": task.run_count + 1,
        }
        if is_once:
            changes["status"] = "completed"
        return self._update_task(
            task.id,
            **changes,
        )

    def _update_task(self, task_id: str, **changes: Any) -> TaskDefinition:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        data = asdict(task)
        data.update(changes)
        data["updated_at"] = _now_iso()
        updated = TaskDefinition(**data)
        self.store.upsert("scheduled_tasks", updated)
        return updated


def task_from_mapping(raw: dict[str, Any]) -> TaskDefinition:
    schedule = raw.get("schedule")
    if not isinstance(schedule, dict):
        raise ValueError("task schedule must be a mapping")
    input_payload = raw.get("input") or {}
    if not isinstance(input_payload, dict):
        raise ValueError("task input must be a mapping")
    policy = raw.get("policy") or {}
    if not isinstance(policy, dict):
        raise ValueError("task policy must be a mapping")
    return TaskDefinition(
        id=str(raw.get("id") or f"task-{uuid4()}"),
        name=str(raw.get("name") or raw.get("id") or "Scheduled task"),
        agent_id=str(raw.get("agent_id") or "default"),
        runtime_adapter=str(raw.get("runtime_adapter") or "agno"),
        schedule=dict(schedule),
        input=dict(input_payload),
        policy=dict(policy),
        status=str(raw.get("status") or "active"),
        next_run_at=raw.get("next_run_at"),
    )


def next_run_after(schedule: dict[str, Any], *, after: str | datetime | None = None) -> str:
    base = _parse_datetime(after) if after is not None else datetime.now(UTC)
    kind = str(schedule.get("type") or "").lower()
    if kind == "cron":
        return _next_cron(str(schedule.get("expression") or ""), base).isoformat()
    if kind == "interval":
        seconds = int(schedule.get("seconds") or schedule.get("every_s") or 0)
        if seconds <= 0:
            raise ValueError("interval schedule requires positive seconds")
        return (base + timedelta(seconds=seconds)).isoformat()
    if kind == "once":
        at = schedule.get("at") or schedule.get("run_at")
        if not at:
            raise ValueError("once schedule requires at")
        return _parse_datetime(str(at)).isoformat()
    raise ValueError(f"unsupported schedule type: {kind}")


def _next_cron(expression: str, base: datetime) -> datetime:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError("cron expression must have five fields")
    minute, hour, day, month, dow = parts
    if day != "*" or month != "*" or dow != "*":
        raise ValueError("cron MVP supports '*' for day, month and weekday")
    candidate = base.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = candidate + timedelta(days=366)
    while candidate <= limit:
        if _cron_matches(candidate.minute, minute, 0, 59) and _cron_matches(candidate.hour, hour, 0, 23):
            return candidate
        candidate += timedelta(minutes=1)
    raise ValueError("could not compute next cron run within one year")


def _cron_matches(value: int, field: str, minimum: int, maximum: int) -> bool:
    if field == "*":
        return True
    if field.startswith("*/"):
        step = int(field[2:])
        return step > 0 and (value - minimum) % step == 0
    if "," in field:
        return any(_cron_matches(value, part.strip(), minimum, maximum) for part in field.split(","))
    parsed = int(field)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"cron field out of range: {field}")
    return value == parsed


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _default_adapter_factory(name: str) -> Any:
    from .adapters import get_runtime_adapter

    return get_runtime_adapter(name)
