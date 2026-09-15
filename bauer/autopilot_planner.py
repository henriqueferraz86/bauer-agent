"""Planning-only adapter used by :mod:`bauer.autopilot`.

This module intentionally exposes no execute callback.  The adapter may call
the existing planner's decomposer, but its output is consumed only as task
definitions; execution remains with ``TaskDispatcher``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .autonomous_planner import AutonomousPlanner, PlanStep


class AutopilotPlanner:
    """Synchronous boundary between the controller and async planner."""

    def __init__(self, planner: AutonomousPlanner | None = None) -> None:
        self.planner = planner or AutonomousPlanner()

    def decompose(self, goal: Any) -> list[PlanStep]:
        """Decompose one goal and return steps; never execute a step."""
        return asyncio.run(
            self.planner.decompose_goal(
                str(goal.title),
                str(getattr(goal, "description", "") or ""),
            )
        )


__all__ = ["AutopilotPlanner"]
