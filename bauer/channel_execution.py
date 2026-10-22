"""Live channel workers and their authenticated cancellation ownership.

This registry records worker liveness; the Kernel alone records run outcomes.
It deliberately cannot resume a Python worker after process restart.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from .execution_control import ExecutionControl


@dataclass
class SessionWorker:
    done: threading.Event = field(default_factory=threading.Event)
    stop_queue: threading.Event = field(default_factory=threading.Event)
    response: str = ""
    error: BaseException | None = None
    current: ChannelExecution | None = None
    discarded_pending: int = 0
    timed_out: bool = False


@dataclass
class ChannelExecution:
    owner: str
    channel: str
    chat_id: str
    project_id: str | None
    session_key: str
    workspace: Path
    batch: SessionWorker
    job_id: str = field(default_factory=lambda: f"job-{uuid4().hex[:16]}")
    started_at: float = field(default_factory=time.monotonic)
    done: threading.Event = field(default_factory=threading.Event)
    control: ExecutionControl = field(init=False)
    requested_reason: str | None = None
    phase: str = "admitting"

    def __post_init__(self) -> None:
        self.control = ExecutionControl(session_id=self.session_key, workspace=self.workspace)

    @property
    def id(self) -> str:
        return self.control.run_id or self.job_id


class ChannelExecutionRegistry:
    """Own live worker references, bounded history and no terminal mutations."""

    def __init__(self, history_limit: int = 100) -> None:
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, ChannelExecution] = OrderedDict()
        self._history_limit = history_limit

    def add(self, execution: ChannelExecution) -> None:
        with self._lock:
            self._entries[execution.job_id] = execution
            finished = [key for key, value in self._entries.items() if value.done.is_set()]
            for key in finished[:-self._history_limit] if self._history_limit else finished:
                self._entries.pop(key, None)

    def owned(self, *, owner: str, channel: str, chat_id: str,
              project_id: str | None, run_id: str | None = None,
              active_only: bool = True) -> list[ChannelExecution]:
        with self._lock:
            return [job for job in self._entries.values()
                    if job.owner == owner and job.channel == channel
                    and job.chat_id == chat_id and job.project_id == project_id
                    and (not active_only or not job.done.is_set())
                    and (run_id is None or run_id in {job.id, job.job_id})]

    def active_session(self, session_key: str) -> bool:
        with self._lock:
            return any(job.session_key == session_key and not job.done.is_set()
                       for job in self._entries.values())

    def active_for_session(self, session_key: str) -> ChannelExecution | None:
        with self._lock:
            return next((job for job in reversed(self._entries.values())
                         if job.session_key == session_key and not job.done.is_set()), None)


def governance_stop_reason(kernel: Any, metrics: Any, execution: ChannelExecution,
                           *, max_runtime_s: float = 0) -> str | None:
    """Read kill-switch and live spending without changing the budget profile.

    Persisted spend includes previous executor attempts; this attempt's recorder
    is added only until the Kernel settles it on return.
    """
    if kernel is None:
        return None
    if kernel.control is not None and kernel.control.kill_switch_enabled():
        return "kill_switch"
    run = kernel.runs.get_run(execution.control.run_id) if execution.control.run_id else None
    if run is not None and run.status in {"cancelled", "failed"}:
        return "cancelled"
    if max_runtime_s > 0 and time.monotonic() - execution.started_at >= max_runtime_s:
        return "budget_exhausted"
    if kernel.budget is None:
        return None
    status = kernel.budget.status()
    profile = status["profile"]
    if profile["mode"] == "locked":
        return "budget_exhausted"
    cost = metrics.result()["cost_estimate"]
    for scope in ("daily", "weekly", "monthly"):
        period = status[scope]
        limit = period["limit_usd"]
        if limit is not None and period["used_usd"] + cost >= limit:
            return "budget_exhausted"
    run_limit = profile.get("max_cost_usd_per_run")
    if run_limit is not None and cost + float(getattr(run, "cost_estimate", 0) or 0) >= run_limit:
        return "budget_exhausted"
    if run is not None:
        agent = status["by_agent"].get(run.agent_id)
        if agent and agent["used_usd"] + cost >= agent["limit_usd"]:
            return "budget_exhausted"
    return None
