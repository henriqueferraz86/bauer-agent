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
from typing import Callable

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
    ) -> DispatchResult:
        result = DispatchResult()
        result.reclaimed.extend(self.reclaim_stale())

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

            ready_tasks = [t for t in self.wm.list_tasks() if t.status == "READY"]
            ready_tasks.sort(key=lambda t: (_priority_order(t.priority), t.id))

            for task in ready_tasks:
                selected = len(claimed) + len(result.dry_run)
                if selected >= spawn_budget:
                    break
                blocked_parent = self._blocked_parent(task)
                if blocked_parent:
                    result.skipped.append(f"{_public_id(task.id)}: parent {blocked_parent} not done")
                    continue
                if dry_run:
                    result.dry_run.append(_public_id(task.id))
                    continue
                claimed_task = self._claim_locked(task)
                claimed.append(claimed_task)
                result.claimed.append(_public_id(claimed_task.id))

        for task in claimed:
            if spawn_background and worker_fn is None:
                try:
                    pid = self._spawn_worker_process(task, config=config, models=models)
                    with self._lock():
                        self.wm.update_task_metadata(task.id, metadata={"worker_pid": pid})
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

    def _claim_locked(self, task: Task) -> Task:
        attempts = _to_int(task.metadata.get("attempts")) + 1
        run_id = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
        claim_id = f"{self.runner_name}:{uuid.uuid4().hex[:8]}"
        log_path = self.runs_dir / f"{task.id}-{run_id}.log"
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
            },
        )
        self.wm.add_task_comment(claimed.id, f"Claimed run_id={run_id}", "dispatcher")
        return self.wm.get_task(claimed.id)

    def _complete_task(self, task_id: str, summary: str) -> Task:
        with self._lock():
            task = self.wm.update_task_status(task_id, "DONE")
            task = self.wm.update_task_metadata(task.id, metadata=_clear_claim_metadata(last_error=False))
            if summary:
                self.wm.add_task_comment(task.id, f"Resultado: {summary[:1000]}", "dispatcher")
            return self.wm.get_task(task.id)

    def _fail_task(self, task_id: str, error: str) -> Task:
        with self._lock():
            task = self.wm.get_task(task_id)
            attempts = _to_int(task.metadata.get("attempts"))
            max_retries = _to_int(task.metadata.get("max_retries")) or self.max_retries
            final_status = "FAILED" if attempts >= max_retries else "READY"
            task = self.wm.update_task_status(task.id, final_status)
            metadata = _clear_claim_metadata(last_error=True)
            metadata["last_error"] = error[:500]
            task = self.wm.update_task_metadata(task.id, metadata=metadata)
            verb = "FAILED" if final_status == "FAILED" else "READY para retry"
            self.wm.add_task_comment(task.id, f"Falha ({verb}): {error[:1000]}", "dispatcher")
            return self.wm.get_task(task.id)

    def _release_to_ready(self, task: Task, *, reason: str) -> Task:
        released = self.wm.update_task_status(task.id, "READY")
        released = self.wm.update_task_metadata(released.id, metadata=_clear_claim_metadata(last_error=False))
        self.wm.add_task_comment(released.id, f"Reclaimed: {reason}", "dispatcher")
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
        if worker_fn is not None:
            try:
                raw = worker_fn(task)
                if isinstance(raw, WorkerResult):
                    return raw
                if raw is False:
                    return WorkerResult(False, error="worker_fn returned False")
                return WorkerResult(True, summary="" if raw is None else str(raw))
            except Exception as exc:
                return WorkerResult(False, error=str(exc))
        return self._run_orchestrator_subprocess(task, config=config, models=models)

    def _run_orchestrator_subprocess(
        self,
        task: Task,
        *,
        config: str | Path,
        models: str | Path,
    ) -> WorkerResult:
        prompt = _task_prompt(task)
        project_root = Path(__file__).resolve().parent.parent
        cmd = [
            sys.executable,
            "-m",
            "bauer.cli",
            "orchestrate",
            "run",
            prompt,
            "--workspace",
            str(self.workspace),
            "--config",
            str(Path(config).resolve()),
            "--models",
            str(Path(models).resolve()),
        ]
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
            )
        except subprocess.TimeoutExpired as exc:
            _write_log(log_path, cmd, exc.stdout or "", exc.stderr or "", returncode=-1)
            return WorkerResult(False, error=f"timeout apos {timeout}s")
        _write_log(log_path, cmd, proc.stdout, proc.stderr, proc.returncode)
        if proc.returncode == 0:
            summary = (proc.stdout or "").strip()[-2000:]
            return WorkerResult(True, summary=summary or "orchestrate run concluido")
        err = (proc.stderr or proc.stdout or "").strip()[-2000:]
        return WorkerResult(False, error=err or f"worker exit {proc.returncode}")

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


def _public_id(task_id: str) -> str:
    raw = str(task_id).strip()
    if raw.upper().startswith("T"):
        raw = raw[1:]
    return f"T{int(raw):04d}" if raw.isdigit() else raw


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


def _task_prompt(task: Task) -> str:
    parts = [
        f"Executar task Kanban {task.id}: {task.title}",
        "",
        task.description.strip(),
        "",
        "Ao concluir, produza um resumo objetivo do que foi feito.",
    ]
    return "\n".join(p for p in parts if p is not None).strip()


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
