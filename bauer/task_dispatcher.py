"""Durable hybrid dispatcher for Bauer TASKS.md boards.

This module intentionally keeps the existing synchronous DAG orchestrator as the
worker engine. The dispatcher is only a durable queue/claim layer around
WorkspaceManager/TASKS.md.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .agent_lanes import AgentLaneSelection, resolve_agent_lane
from .kanban_store import KanbanStore
from .workspace_manager import Task, WorkspaceError, WorkspaceManager


TERMINAL_STATUSES = {"DONE", "BLOCKED", "FAILED"}


@dataclass
class WorkerResult:
    success: bool
    summary: str = ""
    error: str = ""


@dataclass
class DispatchResult:
    reclaimed: list[str] = field(default_factory=list)
    crashed: list[str] = field(default_factory=list)
    claimed: list[str] = field(default_factory=list)
    spawned: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dry_run: list[str] = field(default_factory=list)


WorkerFn = Callable[[Task], WorkerResult | str | bool | None]


class TaskDispatcherError(Exception):
    """Dispatcher-level failure."""


class _TaskFileLock:
    """Small cross-process lock using O_EXCL; works on Windows and POSIX."""

    def __init__(self, path: Path, timeout_s: float = 10.0, stale_s: float = 120.0):
        self.path = path
        self.timeout_s = timeout_s
        self.stale_s = stale_s
        self._fd: int | None = None

    def __enter__(self) -> "_TaskFileLock":
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
                        pass
                if time.time() >= deadline:
                    raise TaskDispatcherError(f"Timeout aguardando lock: {self.path}")
                time.sleep(0.05)

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _is_stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return False
        return age > self.stale_s


class TaskDispatcher:
    """Durable READY -> IN_PROGRESS -> DONE/READY/FAILED dispatcher."""

    def __init__(
        self,
        workspace: str | Path = "workspace",
        *,
        claim_ttl_seconds: int = 900,
        stale_seconds: int = 1800,
        max_retries: int = 2,
        runner_name: str = "",
    ):
        self.workspace = Path(workspace).resolve()
        self.wm = WorkspaceManager(self.workspace)
        self.claim_ttl_seconds = max(30, int(claim_ttl_seconds))
        self.stale_seconds = max(30, int(stale_seconds))
        self.max_retries = max(1, int(max_retries))
        host = socket.gethostname() or "localhost"
        self.runner_name = runner_name or f"{host}:{os.getpid()}"
        self.dispatch_dir = self.workspace / ".bauer_dispatch"
        self.runs_dir = self.dispatch_dir / "runs"
        self.lock_path = self.workspace / "TASKS.md.lock"
        self.store = KanbanStore(self.workspace)

    def mark_ready(
        self,
        task_id: str,
        *,
        assignee: str = "",
        max_retries: int | None = None,
        max_runtime_seconds: int | None = None,
    ) -> Task:
        """Opt a task into durable dispatch."""
        with self._lock():
            task = self.wm.update_task_status(task_id, "READY")
            metadata: dict[str, str | int | None] = {
                "dispatch": "true",
                "claim_id": None,
                "claim_expires": None,
                "claimed_by": None,
                "worker_pid": None,
                "heartbeat_at": None,
                "attempts": None,
                "run_id": None,
                "log": None,
                "last_error": None,
            }
            if max_retries is not None:
                metadata["max_retries"] = max(1, int(max_retries))
            elif not task.metadata.get("max_retries"):
                metadata["max_retries"] = self.max_retries
            if max_runtime_seconds is not None:
                metadata["max_runtime_seconds"] = max(1, int(max_runtime_seconds))
            task = self.wm.update_task_metadata(task.id, assignee=assignee or None, metadata=metadata)
            self.wm.add_task_comment(task.id, "Task marcada como READY para dispatcher.", "dispatcher")
            self.store.append_event(
                task.id,
                "dispatcher.ready",
                actor="dispatcher",
                status_to="READY",
                message="Task marked READY for durable dispatch.",
                metadata={
                    "assignee": assignee,
                    "max_retries": max_retries if max_retries is not None else self.max_retries,
                    "max_runtime_seconds": max_runtime_seconds,
                },
            )
            return self.wm.get_task(task.id)

    def heartbeat(self, task_id: str, *, claim_id: str = "", note: str = "") -> bool:
        with self._lock():
            task = self.wm.get_task(task_id)
            if task.status != "IN_PROGRESS":
                return False
            if claim_id and task.metadata.get("claim_id") != claim_id:
                return False
            expires = int(time.time()) + self.claim_ttl_seconds
            self.wm.update_task_metadata(
                task.id,
                metadata={
                    "claim_expires": expires,
                    "heartbeat_at": _now_iso(),
                },
            )
            run_id = task.metadata.get("run_id", "")
            if run_id:
                self.store.update_run(run_id, status="running", metadata={"heartbeat_note": note})
            self.store.append_event(
                task.id,
                "dispatcher.heartbeat",
                actor="dispatcher",
                run_id=run_id,
                message=note or "heartbeat",
                metadata={"claim_expires": expires},
            )
            if note:
                self.wm.add_task_comment(task.id, f"Heartbeat: {note}", "dispatcher")
            return True

    def reclaim_stale(self) -> list[str]:
        reclaimed: list[str] = []
        now = int(time.time())
        with self._lock():
            for task in self.wm.list_tasks():
                if task.status != "IN_PROGRESS" or task.metadata.get("dispatch") != "true":
                    continue
                expires = _to_int(task.metadata.get("claim_expires"))
                if expires and expires > now:
                    continue
                heartbeat_at = _parse_iso_epoch(task.metadata.get("heartbeat_at", ""))
                if heartbeat_at and now - heartbeat_at < self.stale_seconds:
                    continue
                self._release_to_ready(task, reason="claim expirado/stale")
                reclaimed.append(_public_id(task.id))
        return reclaimed

    def detect_crashed_workers(self) -> list[str]:
        """Return IN_PROGRESS dispatch tasks to READY when their worker PID is gone."""
        crashed: list[str] = []
        with self._lock():
            for task in self.wm.list_tasks():
                if task.status != "IN_PROGRESS" or task.metadata.get("dispatch") != "true":
                    continue
                pid = _to_int(task.metadata.get("worker_pid"))
                if not pid or _pid_alive(pid):
                    continue
                self._release_to_ready(
                    task,
                    reason=f"worker crashed pid={pid}",
                    event_type="worker.crashed",
                )
                crashed.append(_public_id(task.id))
        return crashed

    def cancel_task(
        self,
        task_id: str,
        *,
        reason: str = "cancelled by operator",
        terminate_worker: bool = False,
    ) -> Task:
        """Cancel active dispatch work by blocking the task and closing its run."""
        with self._lock():
            before = self.wm.get_task(task_id)
            run_id = before.metadata.get("run_id", "")
            worker_pid = _to_int(before.metadata.get("worker_pid"))
            termination: dict[str, str | int | bool] = {
                "terminate_worker": bool(terminate_worker),
                "worker_pid": worker_pid,
            }
            if terminate_worker and worker_pid:
                termination.update(_terminate_pid(worker_pid))
            self.store.append_event(
                before.id,
                "worker.cancel_requested",
                actor="dispatcher",
                status_from=before.status,
                run_id=run_id,
                message=reason,
                metadata=termination,
            )
            task = self.wm.update_task_status(before.id, "BLOCKED")
            task = self.wm.update_task_metadata(task.id, metadata=_clear_claim_metadata(last_error=False))
            self.wm.add_task_comment(task.id, f"Cancelado: {reason[:1000]}", "dispatcher")
            self.store.update_run(run_id, status="cancelled", error=reason)
            self.store.append_event(
                task.id,
                "dispatcher.cancelled",
                actor="dispatcher",
                status_from=before.status,
                status_to="BLOCKED",
                run_id=run_id,
                message=reason,
                metadata=termination,
            )
            return self.wm.get_task(task.id)

    def retry_failed(self, task_id: str, *, reason: str = "manual retry") -> Task:
        """Return a FAILED/BLOCKED task to READY without resetting its attempt history."""
        with self._lock():
            before = self.wm.get_task(task_id)
            if before.status not in {"FAILED", "BLOCKED"}:
                raise TaskDispatcherError("Apenas tasks FAILED ou BLOCKED podem voltar para READY.")
            task = self.wm.update_task_status(before.id, "READY")
            task = self.wm.update_task_metadata(
                task.id,
                metadata={
                    "dispatch": "true",
                    "claim_id": None,
                    "claim_expires": None,
                    "claimed_by": None,
                    "worker_pid": None,
                    "heartbeat_at": None,
                    "run_id": None,
                    "log": None,
                    "last_error": None,
                },
            )
            self.wm.add_task_comment(task.id, f"Retry manual: {reason[:1000]}", "dispatcher")
            self.store.append_event(
                task.id,
                "dispatcher.retry_requested",
                actor="dispatcher",
                status_from=before.status,
                status_to="READY",
                message=reason,
            )
            return self.wm.get_task(task.id)

    def record_daemon_started(self, *, interval: int, max_spawn: int, max_in_progress: int | None) -> None:
        self.store.append_event(
            "000",
            "dispatcher.daemon_started",
            actor=self.runner_name,
            message="Dispatcher daemon started.",
            metadata={
                "interval": max(1, int(interval)),
                "max_spawn": max(0, int(max_spawn)),
                "max_in_progress": max_in_progress,
            },
        )

    def record_daemon_stopped(self, *, reason: str = "stopped") -> None:
        self.store.append_event(
            "000",
            "dispatcher.daemon_stopped",
            actor=self.runner_name,
            message=reason,
        )

    def watchdog_tick(
        self,
        *,
        max_spawn: int = 1,
        max_in_progress: int | None = None,
        config: str | Path = "config.yaml",
        models: str | Path = "models.yaml",
        dry_run: bool = False,
    ) -> DispatchResult:
        result = self.dispatch_once(
            dry_run=dry_run,
            max_spawn=max_spawn,
            max_in_progress=max_in_progress,
            spawn_background=True,
            config=config,
            models=models,
        )
        self.store.append_event(
            "000",
            "dispatcher.daemon_tick",
            actor=self.runner_name,
            message="watchdog tick",
            metadata={
                "crashed": result.crashed,
                "reclaimed": result.reclaimed,
                "claimed": result.claimed,
                "spawned": result.spawned,
                "failed": result.failed,
                "skipped": result.skipped,
                "dry_run": result.dry_run,
            },
        )
        return result

    def dispatch_once(
        self,
        *,
        worker_fn: WorkerFn | None = None,
        dry_run: bool = False,
        max_spawn: int = 1,
        max_in_progress: int | None = None,
        spawn_background: bool = False,
        config: str | Path = "config.yaml",
        models: str | Path = "models.yaml",
        only_task_ids: Iterable[str] | None = None,
    ) -> DispatchResult:
        result = DispatchResult()
        result.crashed.extend(self.detect_crashed_workers())
        result.reclaimed.extend(self.reclaim_stale())
        scoped_task_ids = _normalize_task_ids(only_task_ids)

        claimed: list[Task] = []
        with self._lock():
            running = [
                t for t in self.wm.list_tasks()
                if t.status == "IN_PROGRESS" and t.metadata.get("dispatch") == "true"
            ]
            if max_in_progress is not None and len(running) >= max_in_progress:
                return result

            spawn_budget = max(0, int(max_spawn))
            if max_in_progress is not None:
                spawn_budget = min(spawn_budget, max_in_progress - len(running))
            lane_counts = _lane_counts(running)

            ready_tasks = [t for t in self.wm.list_tasks() if t.status == "READY"]
            if scoped_task_ids:
                ready_tasks = [t for t in ready_tasks if t.id in scoped_task_ids]
            ready_tasks.sort(key=lambda t: (_priority_order(t.priority), t.id))

            for task in ready_tasks:
                selected = len(claimed) + len(result.dry_run)
                if selected >= spawn_budget:
                    break
                blocked_parent = self._blocked_parent(task)
                if blocked_parent:
                    result.skipped.append(f"{_public_id(task.id)}: parent {blocked_parent} not done")
                    continue
                lane_selection = resolve_agent_lane(task, workspace=self.workspace)
                if _lane_at_capacity(lane_selection, lane_counts):
                    result.skipped.append(
                        f"{_public_id(task.id)}: lane {lane_selection.lane} capacity "
                        f"{lane_counts.get(lane_selection.lane, 0)}/{lane_selection.max_concurrent}"
                    )
                    continue
                if dry_run:
                    result.dry_run.append(_public_id(task.id))
                    continue
                claimed_task = self._claim_locked(task, lane_selection=lane_selection)
                claimed.append(claimed_task)
                result.claimed.append(_public_id(claimed_task.id))
                lane_counts[lane_selection.lane] = lane_counts.get(lane_selection.lane, 0) + 1

        for task in claimed:
            if spawn_background and worker_fn is None:
                try:
                    pid = self._spawn_worker_process(task, config=config, models=models)
                    with self._lock():
                        self.wm.update_task_metadata(task.id, metadata={"worker_pid": pid})
                    self.store.update_run(task.metadata.get("run_id", ""), status="running", worker_pid=pid)
                    self.store.append_event(
                        task.id,
                        "dispatcher.spawned",
                        actor="dispatcher",
                        run_id=task.metadata.get("run_id", ""),
                        message=f"Worker spawned pid={pid}",
                        metadata={"worker_pid": pid},
                    )
                    result.spawned.append(_public_id(task.id))
                except Exception as exc:
                    failed_task = self._fail_task(task.id, str(exc))
                    result.failed.append(_public_id(failed_task.id))
                continue

            worker_result = self._run_worker(task, worker_fn=worker_fn, config=config, models=models)
            if worker_result.success:
                completed = self._complete_task(task.id, worker_result.summary)
                result.completed.append(_public_id(completed.id))
            else:
                failed_task = self._fail_task(task.id, worker_result.error or worker_result.summary)
                result.failed.append(_public_id(failed_task.id))

        return result

    def run_claimed_worker(
        self,
        task_id: str,
        *,
        claim_id: str = "",
        config: str | Path = "config.yaml",
        models: str | Path = "models.yaml",
    ) -> WorkerResult:
        task = self.wm.get_task(task_id)
        if task.status != "IN_PROGRESS":
            raise TaskDispatcherError(f"Task {_public_id(task.id)} nao esta IN_PROGRESS.")
        if claim_id and task.metadata.get("claim_id") != claim_id:
            raise TaskDispatcherError("Claim id nao confere; worker recusado.")
        result = self._run_worker(task, worker_fn=None, config=config, models=models)
        if result.success:
            self._complete_task(task.id, result.summary)
        else:
            self._fail_task(task.id, result.error or result.summary)
        return result

    def _claim_locked(self, task: Task, *, lane_selection: AgentLaneSelection | None = None) -> Task:
        attempts = _to_int(task.metadata.get("attempts")) + 1
        run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        claim_id = f"{self.runner_name}:{uuid.uuid4().hex[:8]}"
        log_path = self.runs_dir / f"{task.id}-{run_id}.log"
        lane_selection = lane_selection or resolve_agent_lane(task, workspace=self.workspace)
        claimed = self.wm.update_task_status(task.id, "IN_PROGRESS")
        claimed = self.wm.update_task_metadata(
            task.id,
            metadata={
                "dispatch": "true",
                "claim_id": claim_id,
                "claim_expires": int(time.time()) + self.claim_ttl_seconds,
                "claimed_by": self.runner_name,
                "run_id": run_id,
                "heartbeat_at": _now_iso(),
                "attempts": attempts,
                "max_retries": task.metadata.get("max_retries") or self.max_retries,
                "last_error": None,
                "worker_pid": None,
                "log": str(log_path.relative_to(self.workspace)).replace("\\", "/"),
                "lane": lane_selection.lane,
                "agent": lane_selection.agent or None,
                "capability": lane_selection.capability or None,
            },
        )
        self.wm.add_task_comment(claimed.id, f"Claimed run_id={run_id}", "dispatcher")
        log_rel = str(log_path.relative_to(self.workspace)).replace("\\", "/")
        lane_metadata = {
            "priority": claimed.priority,
            "assignee": claimed.assignee,
            "lane": lane_selection.lane,
            "agent": lane_selection.agent,
            "capability": lane_selection.capability,
            "max_concurrent": lane_selection.max_concurrent,
            "priority_weight": lane_selection.priority_weight,
            "configured_lane": lane_selection.configured,
        }
        self.store.start_run(
            run_id=run_id,
            task_id=claimed.id,
            claim_id=claim_id,
            runner=self.runner_name,
            attempt=attempts,
            log_path=log_rel,
            status="claimed",
            metadata=lane_metadata,
        )
        self.store.append_event(
            claimed.id,
            "dispatcher.claimed",
            actor="dispatcher",
            status_from=task.status,
            status_to="IN_PROGRESS",
            run_id=run_id,
            message=f"Claimed by {self.runner_name}",
            metadata={"claim_id": claim_id, "attempt": attempts, "log": log_rel, **lane_metadata},
        )
        return self.wm.get_task(claimed.id)

    def _complete_task(self, task_id: str, summary: str) -> Task:
        with self._lock():
            before = self.wm.get_task(task_id)
            run_id = before.metadata.get("run_id", "")
            task = self.wm.update_task_status(task_id, "DONE")
            task = self.wm.update_task_metadata(task.id, metadata=_clear_claim_metadata(last_error=False))
            if summary:
                self.wm.add_task_comment(task.id, f"Resultado: {summary[:1000]}", "dispatcher")
            self.store.update_run(run_id, status="succeeded", summary=summary)
            self.store.append_event(
                task.id,
                "dispatcher.completed",
                actor="dispatcher",
                status_from=before.status,
                status_to="DONE",
                run_id=run_id,
                message=summary[:1000] if summary else "Task completed.",
            )
            return self.wm.get_task(task.id)

    def _fail_task(self, task_id: str, error: str) -> Task:
        with self._lock():
            task = self.wm.get_task(task_id)
            run_id = task.metadata.get("run_id", "")
            attempts = _to_int(task.metadata.get("attempts"))
            max_retries = _to_int(task.metadata.get("max_retries")) or self.max_retries
            final_status = "FAILED" if attempts >= max_retries else "READY"
            task = self.wm.update_task_status(task.id, final_status)
            metadata = _clear_claim_metadata(last_error=True)
            metadata["last_error"] = error[:500]
            task = self.wm.update_task_metadata(task.id, metadata=metadata)
            verb = "FAILED" if final_status == "FAILED" else "READY para retry"
            self.wm.add_task_comment(task.id, f"Falha ({verb}): {error[:1000]}", "dispatcher")
            run_status = "failed" if final_status == "FAILED" else "retrying"
            self.store.update_run(run_id, status=run_status, error=error)
            self.store.append_event(
                task.id,
                "dispatcher.failed" if final_status == "FAILED" else "dispatcher.retrying",
                actor="dispatcher",
                status_from="IN_PROGRESS",
                status_to=final_status,
                run_id=run_id,
                message=error[:1000],
                metadata={"attempts": attempts, "max_retries": max_retries},
            )
            return self.wm.get_task(task.id)

    def _release_to_ready(self, task: Task, *, reason: str, event_type: str = "dispatcher.reclaimed") -> Task:
        run_id = task.metadata.get("run_id", "")
        released = self.wm.update_task_status(task.id, "READY")
        released = self.wm.update_task_metadata(released.id, metadata=_clear_claim_metadata(last_error=False))
        self.wm.add_task_comment(released.id, f"Reclaimed: {reason}", "dispatcher")
        self.store.update_run(run_id, status="stale", error=reason)
        self.store.append_event(
            released.id,
            event_type,
            actor="dispatcher",
            status_from="IN_PROGRESS",
            status_to="READY",
            run_id=run_id,
            message=reason,
        )
        return self.wm.get_task(released.id)

    def _run_worker(
        self,
        task: Task,
        *,
        worker_fn: WorkerFn | None,
        config: str | Path,
        models: str | Path,
    ) -> WorkerResult:
        self.heartbeat(task.id, claim_id=task.metadata.get("claim_id", ""), note="worker started")
        run_id = task.metadata.get("run_id", "")
        self.store.update_run(run_id, status="running")
        self.store.append_event(
            task.id,
            "worker.started",
            actor=self.runner_name,
            run_id=run_id,
            message="Worker execution started.",
            metadata={"claim_id": task.metadata.get("claim_id", "")},
        )
        result = WorkerResult(False, error="worker did not run")
        try:
            if worker_fn is not None:
                raw = worker_fn(task)
                if isinstance(raw, WorkerResult):
                    result = raw
                elif raw is False:
                    result = WorkerResult(False, error="worker_fn returned False")
                else:
                    result = WorkerResult(True, summary="" if raw is None else str(raw))
            else:
                result = self._run_orchestrator_subprocess(task, config=config, models=models)
            return result
        except Exception as exc:
            result = WorkerResult(False, error=str(exc))
            return result
        finally:
            self.store.append_event(
                task.id,
                "worker.stopped",
                actor=self.runner_name,
                run_id=run_id,
                message=result.summary[:1000] if result.success else result.error[:1000],
                metadata={"success": result.success},
            )

    def _run_orchestrator_subprocess(
        self,
        task: Task,
        *,
        config: str | Path,
        models: str | Path,
    ) -> WorkerResult:
        project_root = Path(__file__).resolve().parent.parent

        # Worktree por task: se o workspace for um repo git (e não desabilitado),
        # o worker roda num worktree isolado e o resultado vira um diff commitado.
        worktree = self._maybe_setup_worktree(task)
        effective_ws = worktree.path if worktree else self.workspace

        cmd = _worker_command(task, effective_ws, config=config, models=models)
        timeout = _to_int(task.metadata.get("max_runtime_seconds")) or None
        log_path = self._task_log_path(task)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(
                cmd,
                input="y\n",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=str(project_root),
                env=_worker_env(task, effective_ws),
            )
        except subprocess.TimeoutExpired as exc:
            _write_log(log_path, cmd, exc.stdout or "", exc.stderr or "", returncode=-1)
            self.store.append_event(
                task.id,
                "worker.timeout",
                actor=self.runner_name,
                run_id=task.metadata.get("run_id", ""),
                message=f"timeout apos {timeout}s",
                metadata={"timeout": timeout},
            )
            return WorkerResult(False, error=f"timeout apos {timeout}s")
        _write_log(log_path, cmd, proc.stdout, proc.stderr, proc.returncode)
        if proc.returncode == 0:
            summary = (proc.stdout or "").strip()[-2000:]
            artifact = self._finalize_worktree(task, worktree)
            if artifact:
                summary = (summary + "\n\n" + artifact).strip()
            return WorkerResult(True, summary=summary or "orchestrate run concluido")
        err = (proc.stderr or proc.stdout or "").strip()[-2000:]
        return WorkerResult(False, error=err or f"worker exit {proc.returncode}")

    def _maybe_setup_worktree(self, task: Task):
        """Cria um git worktree para a task se o workspace for repo git.

        Desabilitável via env BAUER_TASK_WORKTREE=0. Retorna None (no-op) se
        não for repo git ou se o git falhar — preserva o comportamento atual.
        """
        if os.environ.get("BAUER_TASK_WORKTREE", "1") == "0":
            return None
        try:
            from . import task_worktree as _wt
            if not _wt.is_git_repo(self.workspace):
                return None
            info = _wt.create_worktree(self.workspace, task.id)
            if info:
                self.store.append_event(
                    task.id, "worker.worktree_created", actor=self.runner_name,
                    run_id=task.metadata.get("run_id", ""),
                    message=f"worktree {info.branch}",
                    metadata={"branch": info.branch, "path": str(info.path)},
                )
            return info
        except Exception:
            return None

    def _finalize_worktree(self, task: Task, worktree) -> str:
        """Commita o trabalho do worker no worktree e devolve a linha de handoff."""
        if worktree is None:
            return ""
        try:
            from . import task_worktree as _wt
            commit = _wt.commit_worktree(
                worktree, f"bauer task {_public_id(task.id)}: {task.title}"[:200]
            )
            line = _wt.summarize_artifact(commit)
            self.store.append_event(
                task.id, "worker.worktree_committed", actor=self.runner_name,
                run_id=task.metadata.get("run_id", ""),
                message=line,
                metadata={
                    "branch": commit.branch,
                    "commit": commit.commit,
                    "changed_files": commit.changed_files[:50],
                    "committed": commit.committed,
                },
            )
            return line
        except Exception:
            return ""

    def _spawn_worker_process(
        self,
        task: Task,
        *,
        config: str | Path,
        models: str | Path,
    ) -> int:
        claim_id = task.metadata.get("claim_id", "")
        project_root = Path(__file__).resolve().parent.parent
        log_path = self._task_log_path(task)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            "-m",
            "bauer.cli",
            "dispatch",
            "worker",
            task.id,
            "--claim-id",
            claim_id,
            "--workspace",
            str(self.workspace),
            "--config",
            str(Path(config).resolve()),
            "--models",
            str(Path(models).resolve()),
        ]
        handle = log_path.open("a", encoding="utf-8", errors="replace")
        handle.write(f"$ {' '.join(cmd)}\n")
        handle.flush()
        proc = subprocess.Popen(
            cmd,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(project_root),
            text=True,
            env=_worker_env(task, self.workspace),
        )
        handle.close()
        return int(proc.pid)

    def _blocked_parent(self, task: Task) -> str:
        if not task.parent_id:
            return ""
        try:
            parent = self.wm.get_task(task.parent_id)
        except WorkspaceError:
            return _public_id(task.parent_id)
        return "" if parent.status == "DONE" else _public_id(parent.id)

    def _task_log_path(self, task: Task) -> Path:
        raw = task.metadata.get("log")
        if raw:
            return (self.workspace / raw).resolve()
        run_id = task.metadata.get("run_id") or f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        return self.runs_dir / f"{task.id}-{run_id}.log"

    def _lock(self) -> _TaskFileLock:
        return _TaskFileLock(self.lock_path)


def _clear_claim_metadata(*, last_error: bool) -> dict[str, str | None]:
    data: dict[str, str | None] = {
        "claim_id": None,
        "claim_expires": None,
        "claimed_by": None,
        "worker_pid": None,
        "heartbeat_at": None,
    }
    if not last_error:
        data["last_error"] = None
    return data


def _priority_order(priority: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(priority, 9)


def _lane_counts(tasks: list[Task]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for task in tasks:
        lane = task.metadata.get("lane") or task.assignee or "default"
        counts[lane] = counts.get(lane, 0) + 1
    return counts


def _lane_at_capacity(selection: AgentLaneSelection, counts: dict[str, int]) -> bool:
    return bool(selection.configured and counts.get(selection.lane, 0) >= selection.max_concurrent)


def _public_id(task_id: str) -> str:
    raw = str(task_id).strip()
    if raw.upper().startswith("T"):
        raw = raw[1:]
    return f"T{int(raw):04d}" if raw.isdigit() else raw


def _normalize_task_ids(values: Iterable[str] | None) -> set[str]:
    ids: set[str] = set()
    for value in values or []:
        raw = str(value).strip()
        if raw.upper().startswith("T") and raw[1:].isdigit():
            raw = raw[1:]
        if raw.isdigit():
            raw = str(int(raw)).zfill(3)
        if raw:
            ids.add(raw)
    return ids


def _to_int(value: object) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso_epoch(value: str) -> int:
    if not value:
        return 0
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore

        return bool(psutil.pid_exists(pid))
    except Exception:
        return True


def _terminate_pid(pid: int, *, timeout_s: float = 5.0) -> dict[str, str | int | bool]:
    if pid <= 0:
        return {"termination_requested": False, "termination_status": "invalid_pid"}
    try:
        import psutil  # type: ignore

        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=timeout_s)
            return {"termination_requested": True, "termination_status": "terminated"}
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=timeout_s)
            return {"termination_requested": True, "termination_status": "killed"}
    except Exception as exc:
        return {
            "termination_requested": True,
            "termination_status": "error",
            "termination_error": str(exc)[:300],
        }


def _task_prompt(task: Task) -> str:
    parts = [
        f"Executar task Kanban {task.id}: {task.title}",
        "",
        task.description.strip(),
        "",
        "Ao concluir, produza um resumo objetivo do que foi feito.",
    ]
    return "\n".join(p for p in parts if p is not None).strip()


def _worker_command(
    task: Task,
    workspace: Path,
    *,
    config: str | Path,
    models: str | Path,
) -> list[str]:
    orchestration_run = task.metadata.get("orchestration_run", "")
    orchestration_step = task.metadata.get("orchestration_step", "")
    base = [
        sys.executable,
        "-m",
        "bauer.cli",
        "orchestrate",
    ]
    common = [
        "--workspace",
        str(workspace),
        "--config",
        str(Path(config).resolve()),
        "--models",
        str(Path(models).resolve()),
    ]
    if orchestration_run and orchestration_step:
        return [
            *base,
            "node-worker",
            orchestration_run,
            str(orchestration_step),
            "--task-id",
            task.id,
            "--claim-id",
            task.metadata.get("claim_id", ""),
            *common,
        ]
    return [
        *base,
        "run",
        _task_prompt(task),
        *common,
    ]


def _worker_env(task: Task, workspace: Path) -> dict[str, str]:
    from .secret_policy import safe_worker_env

    return safe_worker_env(
        {
            "BAUER_KANBAN_TASK": task.id,
            "BAUER_KANBAN_PUBLIC_TASK": _public_id(task.id),
            "BAUER_KANBAN_CLAIM_ID": task.metadata.get("claim_id", ""),
            "BAUER_KANBAN_RUN_ID": task.metadata.get("run_id", ""),
            "BAUER_KANBAN_WORKSPACE": str(workspace),
            "BAUER_TOOL_CONTEXT": "worker",
        }
    )


def _write_log(log_path: Path, cmd: list[str], stdout: str, stderr: str, returncode: int) -> None:
    body = [
        f"$ {' '.join(cmd)}",
        f"returncode={returncode}",
        "",
        "## stdout",
        stdout or "",
        "",
        "## stderr",
        stderr or "",
    ]
    log_path.write_text("\n".join(body), encoding="utf-8", errors="replace")
