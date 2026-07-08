"""Autonomous planner — decomposes goals into steps and executes them.

The planner is the *brain* of the autonomous agent tier.  It receives a
high-level :class:`Goal` description and breaks it into a list of
:class:`PlanStep` objects, each representing one atomic unit of work
(e.g. "run tests", "update config", "commit changes").

Design
------
* **Decomposition**: a pluggable ``decompose_fn`` turns a goal title +
  description into ``list[PlanStep]``.  The default implementation
  produces a single "execute" step (useful for tests / simple goals).
  In production, this would call an LLM.

* **Execution**: steps are executed in order by a pluggable
  ``execute_fn``.  The default stub sleeps briefly and marks the step
  done.  In production, this would run the actual agent task.

* **Retry**: each step has its own ``max_retries`` counter.  A failed
  step is retried up to that limit before the whole plan fails.

* **Persistence**: the :class:`GoalTracker` stores the current step
  list after every state transition.

* **Events**: progress events are emitted via an optional async
  ``on_event`` callback, allowing the caller (e.g. the daemon) to
  stream status updates.

Usage::

    from bauer.autonomous_planner import AutonomousPlanner, PlannerConfig

    planner = AutonomousPlanner(PlannerConfig())
    result = await planner.execute_goal(
        goal_id="goal_abc123",
        title="Bump all Python deps to latest minor",
        tracker=tracker,
    )
    print(result.status)  # PlanStatus.DONE
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PlanStep:
    """One atomic unit of work within a plan.

    Attributes
    ----------
    title:
        Short description of what this step does.
    description:
        Optional longer explanation / context for the executor.
    max_retries:
        How many times to retry this step on failure before giving up.
    timeout_seconds:
        Optional per-step timeout.  ``None`` means no timeout.
    metadata:
        Arbitrary key-value pairs for the executor (e.g. task_id).
    """

    title: str
    description: str = ""
    max_retries: int = 2
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # Mutable state — updated during execution
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    error: str | None = None
    started_at: float | None = None
    completed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "description": self.description,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "metadata": self.metadata,
            "status": self.status.value,
            "attempts": self.attempts,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PlanStep":
        step = cls(
            title=d["title"],
            description=d.get("description", ""),
            max_retries=d.get("max_retries", 2),
            timeout_seconds=d.get("timeout_seconds"),
            metadata=d.get("metadata", {}),
        )
        step.status = StepStatus(d.get("status", "pending"))
        step.attempts = d.get("attempts", 0)
        step.error = d.get("error")
        step.started_at = d.get("started_at")
        step.completed_at = d.get("completed_at")
        return step


@dataclass
class PlanEvent:
    """Progress event emitted during plan execution."""

    kind: str          # "step_started" | "step_done" | "step_failed" | "plan_done" | "plan_failed"
    goal_id: str
    step_index: int | None = None
    step_title: str | None = None
    message: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class PlanResult:
    """Returned by :meth:`AutonomousPlanner.execute_goal`."""

    goal_id: str
    status: PlanStatus
    steps_total: int = 0
    steps_done: int = 0
    steps_failed: int = 0
    elapsed_seconds: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# Type aliases for pluggable functions
# ---------------------------------------------------------------------------

DecomposeFn = Callable[[str, str], Awaitable[list[PlanStep]]]
"""``async (title: str, description: str) -> list[PlanStep]``"""

ExecuteFn = Callable[[PlanStep, str], Awaitable[None]]
"""``async (step: PlanStep, goal_id: str) -> None``

Raises any exception on failure; returns normally on success.
"""

OnEventFn = Callable[[PlanEvent], Awaitable[None]]
"""``async (event: PlanEvent) -> None``"""


# ---------------------------------------------------------------------------
# Default implementations
# ---------------------------------------------------------------------------


async def _default_decompose(title: str, description: str) -> list[PlanStep]:
    """Stub decomposer — yields a single 'execute' step.

    Replace with an LLM call in production.
    """
    return [PlanStep(title=f"Execute: {title}", description=description)]


async def _default_execute(step: PlanStep, goal_id: str) -> None:
    """Stub executor — sleeps briefly to simulate work.

    Replace with actual task dispatch in production.
    """
    await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# PlannerConfig
# ---------------------------------------------------------------------------


@dataclass
class PlannerConfig:
    """Configuration for :class:`AutonomousPlanner`.

    Attributes
    ----------
    max_steps:
        Maximum number of steps in a single plan.  Guards against
        runaway decomposition.  Default 20.
    default_step_retries:
        How many times to retry a failing step if the step itself
        doesn't override ``max_retries``.  Default 2.
    step_timeout_seconds:
        Default per-step timeout in seconds.  ``None`` = no timeout.
    inter_step_delay_seconds:
        Short sleep between steps (avoids busy-loop for CPU-intensive
        executors).  Default 0.0 (no delay).
    decompose_fn:
        Async callable that turns (title, description) → list[PlanStep].
    execute_fn:
        Async callable that runs one step.
    on_event:
        Async callable called on each progress event.
    """

    max_steps: int = 20
    default_step_retries: int = 2
    step_timeout_seconds: float | None = None
    inter_step_delay_seconds: float = 0.0

    decompose_fn: DecomposeFn = field(default=_default_decompose)
    execute_fn: ExecuteFn = field(default=_default_execute)
    on_event: OnEventFn | None = None


# ---------------------------------------------------------------------------
# AutonomousPlanner
# ---------------------------------------------------------------------------


class AutonomousPlanner:
    """Decompose a goal into steps and execute them sequentially with retry.

    Parameters
    ----------
    config:
        :class:`PlannerConfig` instance.  All settings are configurable.
    """

    def __init__(self, config: PlannerConfig | None = None) -> None:
        self._cfg = config or PlannerConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_goal(
        self,
        goal_id: str,
        title: str,
        *,
        description: str = "",
        tracker: Any = None,          # GoalTracker | None
        shutdown_event: asyncio.Event | None = None,
    ) -> PlanResult:
        """Decompose *title* into steps and execute them.

        Parameters
        ----------
        goal_id:
            The goal's persistent ID (from :class:`GoalTracker`).
        title:
            Human-readable goal title.
        description:
            Optional longer description passed to the decomposer.
        tracker:
            :class:`~bauer.goal_tracker.GoalTracker` instance.  When
            provided, step state is persisted after each transition.
        shutdown_event:
            If set, the planner will stop executing new steps and
            return ``CANCELLED`` when the event fires.
        """
        start = time.monotonic()
        result = PlanResult(goal_id=goal_id, status=PlanStatus.RUNNING)

        # ── Decompose ──────────────────────────────────────────────────
        try:
            steps = await self._cfg.decompose_fn(title, description)
        except Exception as exc:
            logger.error("goal[%s] decompose failed: %s", goal_id, exc)
            result.status = PlanStatus.FAILED
            result.error = f"decompose failed: {exc}"
            result.elapsed_seconds = time.monotonic() - start
            if tracker:
                tracker.mark_failed(goal_id, error=result.error)
            return result

        if not steps:
            logger.warning("goal[%s] decompose returned empty step list", goal_id)
            steps = [PlanStep(title=f"Execute: {title}")]

        # Enforce max_steps guard
        if len(steps) > self._cfg.max_steps:
            logger.warning(
                "goal[%s] decompose returned %d steps; truncating to %d",
                goal_id, len(steps), self._cfg.max_steps,
            )
            steps = steps[: self._cfg.max_steps]

        # Apply config-level defaults
        for step in steps:
            if step.max_retries == 2 and self._cfg.default_step_retries != 2:
                step.max_retries = self._cfg.default_step_retries
            if step.timeout_seconds is None and self._cfg.step_timeout_seconds is not None:
                step.timeout_seconds = self._cfg.step_timeout_seconds

        result.steps_total = len(steps)

        # Persist initial step list
        if tracker:
            tracker.update_status(goal_id, "running",
                                   steps=[s.to_dict() for s in steps])

        logger.info("goal[%s] plan has %d steps", goal_id, len(steps))

        # ── Execute steps ──────────────────────────────────────────────
        for i, step in enumerate(steps):
            if shutdown_event is not None and shutdown_event.is_set():
                logger.info("goal[%s] shutdown requested — cancelling after step %d", goal_id, i)
                result.status = PlanStatus.CANCELLED
                break

            step_done = await self._execute_step(step, i, goal_id, tracker)

            if step_done:
                result.steps_done += 1
            else:
                result.steps_failed += 1
                result.status = PlanStatus.FAILED
                result.error = step.error or f"step {i} failed"
                break

            # Persist updated steps after each completion
            if tracker:
                tracker.update_steps(goal_id, [s.to_dict() for s in steps])

            if self._cfg.inter_step_delay_seconds > 0:
                await asyncio.sleep(self._cfg.inter_step_delay_seconds)
        else:
            # All steps completed without break
            if result.status == PlanStatus.RUNNING:
                result.status = PlanStatus.DONE

        result.elapsed_seconds = time.monotonic() - start

        # Final tracker update
        if tracker:
            if result.status == PlanStatus.DONE:
                tracker.mark_complete(goal_id)
            elif result.status == PlanStatus.FAILED:
                tracker.mark_failed(goal_id, error=result.error or "")
            elif result.status == PlanStatus.CANCELLED:
                tracker.cancel(goal_id)

        # Emit final event
        await self._emit(PlanEvent(
            kind="plan_done" if result.status == PlanStatus.DONE else "plan_failed",
            goal_id=goal_id,
            message=f"status={result.status.value} steps={result.steps_done}/{result.steps_total}",
        ))

        logger.info(
            "goal[%s] finished: status=%s steps=%d/%d elapsed=%.1fs",
            goal_id, result.status.value, result.steps_done, result.steps_total,
            result.elapsed_seconds,
        )
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute_step(
        self,
        step: PlanStep,
        index: int,
        goal_id: str,
        tracker: Any,
    ) -> bool:
        """Execute one step with retry.  Returns True on success."""
        for attempt in range(step.max_retries + 1):
            step.status = StepStatus.RUNNING
            step.attempts = attempt + 1
            step.started_at = time.time()

            await self._emit(PlanEvent(
                kind="step_started",
                goal_id=goal_id,
                step_index=index,
                step_title=step.title,
                message=f"attempt {attempt + 1}/{step.max_retries + 1}",
            ))
            logger.debug(
                "goal[%s] step[%d] attempt %d/%d: %s",
                goal_id, index, attempt + 1, step.max_retries + 1, step.title,
            )

            try:
                if step.timeout_seconds is not None:
                    await asyncio.wait_for(
                        self._cfg.execute_fn(step, goal_id),
                        timeout=step.timeout_seconds,
                    )
                else:
                    await self._cfg.execute_fn(step, goal_id)

                step.status = StepStatus.DONE
                step.completed_at = time.time()
                step.error = None

                await self._emit(PlanEvent(
                    kind="step_done",
                    goal_id=goal_id,
                    step_index=index,
                    step_title=step.title,
                ))
                logger.debug("goal[%s] step[%d] done: %s", goal_id, index, step.title)
                return True

            except asyncio.CancelledError:
                step.status = StepStatus.FAILED
                step.error = "cancelled"
                raise

            except Exception as exc:
                step.error = str(exc)
                step.completed_at = time.time()

                if attempt < step.max_retries:
                    logger.warning(
                        "goal[%s] step[%d] attempt %d failed: %s — retrying",
                        goal_id, index, attempt + 1, exc,
                    )
                    # Persist retry state
                    if tracker:
                        tracker.update_steps(goal_id, [])  # dummy; real update below
                else:
                    step.status = StepStatus.FAILED
                    logger.error(
                        "goal[%s] step[%d] FAILED after %d attempts: %s",
                        goal_id, index, step.max_retries + 1, exc,
                    )
                    await self._emit(PlanEvent(
                        kind="step_failed",
                        goal_id=goal_id,
                        step_index=index,
                        step_title=step.title,
                        message=str(exc),
                    ))
                    return False

        # Should not reach here
        step.status = StepStatus.FAILED
        return False

    async def _emit(self, event: PlanEvent) -> None:
        if self._cfg.on_event is None:
            return
        try:
            await self._cfg.on_event(event)
        except Exception as exc:
            logger.debug("on_event callback raised: %s", exc)
