"""Shared Kernel wiring and per-attempt accounting for channel workers.

Transport code supplies the workspace captured for a conversation. This module
never changes policy, budgets or the process working directory.
"""

from __future__ import annotations

import math
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .core.events import EventBus
from .cost_meter import cost_sink
from .runtime_ids import reset_runtime_ids, set_runtime_ids
from .usage_pricing import _PROVIDERS_SEM_CUSTO


def runtime_root(sessions_dir: str | Path) -> Path:
    """Use the same runtime store as the HTTP server for these sessions."""
    if not isinstance(sessions_dir, (str, Path)):
        raise TypeError("sessions_dir must be a path")
    return Path(sessions_dir).resolve().parent / "runtime"


def _runtime_bus(sessions_dir: str | Path, event_bus: EventBus | None) -> EventBus:
    root = runtime_root(sessions_dir)
    if event_bus is not None:
        if Path(event_bus.store.root).resolve() != root:
            raise ValueError("channel EventBus does not match its runtime root")
        return event_bus
    return EventBus(root=root)


def configure_router(router: Any, *, sessions_dir: str | Path,
                     event_bus: EventBus | None = None) -> EventBus:
    """Bind tool policy and events to the shared store without changing policy."""
    bus = _runtime_bus(sessions_dir, event_bus)
    router._policy_root = runtime_root(sessions_dir)
    router._event_bus = bus
    return bus


def build_channel_kernel(cfg: Any, *, workspace: str | Path,
                         sessions_dir: str | Path,
                         event_bus: EventBus | None = None) -> Any:
    """Compose fail-closed governance using this project's policy and gates."""
    from .core.kernel import build_kernel, require_kernel

    if not isinstance(workspace, (str, Path)):
        raise TypeError("workspace must be a path")
    effective_workspace = Path(workspace).resolve()

    def build():
        bus = _runtime_bus(sessions_dir, event_bus)
        return build_kernel(
            cfg, root=str(runtime_root(sessions_dir)),
            workspace=str(effective_workspace), bus=bus,
        )

    return require_kernel(cfg, build, label="channel worker")


class TurnMetrics:
    """One executor attempt's costs, including failed native requests.

    Create a new recorder for each Kernel executor invocation (including a
    replan), and copy the installed context when creating child threads. The
    Kernel persists the returned deltas and owns all budget/status transitions.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total_usd = 0.0
        self._calls = 0
        self._usage_known = True
        self._tool_calls = 0

    def __call__(self, provider: str, model: str, usage: dict[str, Any],
                 cost_usd: float) -> None:
        local = provider in _PROVIDERS_SEM_CUSTO
        cost = float(cost_usd or 0.0)
        valid_cost = math.isfinite(cost) and cost >= 0
        if not valid_cost or local:
            cost = 0.0
        with self._lock:
            self._total_usd += cost
            self._calls += 1
            self._usage_known = self._usage_known and (
                local or (valid_cost and (bool(usage) or cost > 0))
            )

    @contextmanager
    def context(self, session_id: str, run_id: str | None) -> Iterator[None]:
        """Install IDs/accounting in the current worker; always restore them."""
        ids_token = set_runtime_ids(session_id, run_id)
        cost_token = cost_sink.set(self)
        try:
            yield
        finally:
            cost_sink.reset(cost_token)
            reset_runtime_ids(ids_token)

    def record_tools(self, tool_log: list[dict]) -> None:
        """Record a completed turn's tools once, including a partial error log."""
        with self._lock:
            self._tool_calls += len(tool_log)

    def result(self, output: Any = None, tool_log: list[dict] | None = None,
               *, status: str | None = None, error: str | None = None) -> dict[str, Any]:
        """Return deltas for ``run_governed``; never write a terminal status."""
        with self._lock:
            result = {
                "output": output,
                "cost_estimate": round(self._total_usd, 8),
                "llm_calls_count": self._calls,
                "usage_known": self._calls > 0 and self._usage_known,
                "tool_calls_count": (self._tool_calls if tool_log is None else len(tool_log)),
            }
        if status is not None:
            result["status"] = status
        if error is not None:
            result["error"] = error
        return result
