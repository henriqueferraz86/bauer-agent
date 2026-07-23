"""Durable automation scheduler that queues work into Kanban."""

from __future__ import annotations

import os
import socket
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from .automation_store import AutomationJob, AutomationRun, AutomationStore, next_after_run, now_iso
from .task_dispatcher import TaskDispatcher
from .workspace_manager_factory import get_workspace_manager


@dataclass
class AutomationTickResult:
    due: list[str] = field(default_factory=list)
    queued: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    dry_run: bool = False


class AutomationScheduler:
    """Turns due automation jobs into READY Kanban tasks."""

    def __init__(self, workspace: str | Path = "workspace"):
        self.workspace = Path(workspace).resolve()
        self.store = AutomationStore(self.workspace)
        self.wm = get_workspace_manager(self.workspace)
        self.lock_path = self.workspace / ".bauer_automation" / "tick.lock"

    def tick(
        self,
        *,
        now: str | None = None,
        max_jobs: int = 10,
        dry_run: bool = False,
    ) -> AutomationTickResult:
        now = now or now_iso()
        result = AutomationTickResult(dry_run=dry_run)
        with _AutomationTickLock(self.lock_path):
            jobs = self.store.due_jobs(now=now, limit=max_jobs)
            result.due = [job.name for job in jobs]
            for job in jobs:
                if dry_run:
                    result.skipped.append(job.name)
                    continue
                try:
                    run = self.enqueue_job(job, due_at=now)
                    result.queued.append(run.task_id)
                except Exception:
                    result.failed.append(job.name)
                    self.store.update_job(job.job_id, fail_count_delta=1)
        return result

    def run_now(self, job_id_or_name: str, *, due_at: str | None = None, dry_run: bool = False) -> AutomationRun | None:
        job = self.store.get_job(job_id_or_name)
        if job is None:
            raise ValueError(f"automation job not found: {job_id_or_name}")
        if dry_run:
            return None
        return self.enqueue_job(job, due_at=due_at or now_iso(), manual=True)

    def enqueue_job(self, job: AutomationJob, *, due_at: str, manual: bool = False) -> AutomationRun:
        if job.status != "active" and not manual:
            raise ValueError(f"automation job is not active: {job.name}")
        if not self.wm.tasks_file.exists():
            self.wm.init_project("Bauer Automations", "Workspace initialized by automation scheduler.")

        run = self.store.create_run(
            job_id=job.job_id,
            due_at=due_at,
            metadata={"manual": manual, "schedule": job.schedule_str},
        )
        title = f"[Cron] {job.name}"
        description = (
            f"Automation job: {job.name}\n"
            f"Automation run: {run.run_id}\n"
            f"Schedule: {job.schedule_str}\n"
            f"Due at: {due_at}\n\n"
            f"{job.prompt}"
        )
        task = self.wm.add_task(
            title,
            description=description,
            status="TODO",
            priority=str(job.metadata.get("priority") or "medium"),
            assignee=str(job.metadata.get("assignee") or ""),
            metadata={
                "automation_job": job.job_id,
                "automation_run": run.run_id,
                "automation_schedule": job.schedule_str,
                "automation_name": job.name,
            },
        )
        ready = TaskDispatcher(self.workspace).mark_ready(
            task.id,
            assignee=str(job.metadata.get("assignee") or ""),
            max_retries=int(job.metadata.get("max_retries") or 2),
            max_runtime_seconds=_optional_int(job.metadata.get("max_runtime_seconds")),
        )
        run = self.store.update_run(
            run.run_id,
            task_id=ready.id,
            status="queued",
            metadata={"task_public_id": _public_id(ready.id)},
        ) or run
        self._enqueue_delivery(job, run, ready.id, due_at)

        next_run_at = next_after_run(job.schedule, due_at=due_at)
        next_status = "completed" if not next_run_at and not manual else job.status
        self.store.update_job(
            job.job_id,
            status=next_status,
            next_run_at=next_run_at,
            last_run_at=due_at,
            run_count_delta=1,
        )
        return run

    def _enqueue_delivery(self, job: AutomationJob, run: AutomationRun, task_id: str, due_at: str) -> None:
        delivery = str(job.metadata.get("delivery") or "").strip()
        if not delivery:
            return
        from .gateway_channels import GatewayChannelRegistry, resolve_delivery_spec
        from .gateway_outbox import GatewayOutbox

        resolved = resolve_delivery_spec(delivery, GatewayChannelRegistry(self.workspace))
        GatewayOutbox(self.workspace).enqueue(
            channel=resolved.channel,
            target=resolved.target,
            payload={
                "type": "automation.queued",
                "job_id": job.job_id,
                "job_name": job.name,
                "run_id": run.run_id,
                "task_id": task_id,
                "due_at": due_at,
                "prompt": job.prompt,
            },
            metadata={
                "automation_job": job.job_id,
                "automation_run": run.run_id,
                **resolved.metadata,
            },
        )


class _AutomationTickLock:
    """Tiny O_EXCL file lock to avoid overlapping scheduler ticks."""

    def __init__(self, path: Path, timeout_s: float = 10.0, stale_s: float = 300.0):
        self.path = path
        self.timeout_s = timeout_s
        self.stale_s = stale_s
        self._fd: int | None = None

    def __enter__(self) -> "_AutomationTickLock":
        deadline = time.time() + self.timeout_s
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"pid={os.getpid()} host={socket.gethostname()} at={int(time.time())}\n"
        while True:
            try:
                self._fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, payload.encode("utf-8", errors="replace"))
                return self
            except FileExistsError:
                if self._is_stale():
                    try:
                        self.path.unlink()
                        continue
                    except OSError:
                        # Another scheduler may have replaced the lock.
                        time.sleep(0.01)
                if time.time() >= deadline:
                    raise TimeoutError(f"Timeout aguardando lock: {self.path}")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        with suppress(FileNotFoundError):
            self.path.unlink()

    def _is_stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return False
        return age > self.stale_s


def _optional_int(value: object) -> int | None:
    try:
        parsed = int(str(value).strip())
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _public_id(task_id: str) -> str:
    raw = str(task_id).strip()
    if raw.upper().startswith("T"):
        raw = raw[1:]
    return f"T{int(raw):04d}" if raw.isdigit() else raw
