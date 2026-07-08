"""Runtime primitives for executing Bauer agents."""

from __future__ import annotations

__all__ = [
    "AgentRegistry",
    "AgentRegistryError",
    "AgentSpec",
    "DelegationManager",
    "DelegationRecord",
    "MemoryRecord",
    "Run",
    "RunManager",
    "RuntimeMemoryManager",
    "Session",
    "SessionManager",
    "TeamRegistry",
    "TeamRegistryError",
    "TeamSpec",
]


def __getattr__(name: str):
    if name in {"AgentRegistry", "AgentRegistryError"}:
        from .agent_registry import AgentRegistry, AgentRegistryError

        return {"AgentRegistry": AgentRegistry, "AgentRegistryError": AgentRegistryError}[name]
    if name == "AgentSpec":
        from .agent_spec import AgentSpec

        return AgentSpec
    if name in {"Run", "RunManager"}:
        from .run_manager import Run, RunManager

        return {"Run": Run, "RunManager": RunManager}[name]
    if name in {"MemoryRecord", "RuntimeMemoryManager"}:
        from .memory import MemoryRecord, RuntimeMemoryManager

        return {"MemoryRecord": MemoryRecord, "RuntimeMemoryManager": RuntimeMemoryManager}[name]
    if name in {"Session", "SessionManager"}:
        from .session_manager import Session, SessionManager

        return {"Session": Session, "SessionManager": SessionManager}[name]
    if name in {"DelegationManager", "DelegationRecord", "TeamRegistry", "TeamRegistryError", "TeamSpec"}:
        from .team_registry import DelegationManager, DelegationRecord, TeamRegistry, TeamRegistryError, TeamSpec

        return {
            "DelegationManager": DelegationManager,
            "DelegationRecord": DelegationRecord,
            "TeamRegistry": TeamRegistry,
            "TeamRegistryError": TeamRegistryError,
            "TeamSpec": TeamSpec,
        }[name]
    raise AttributeError(name)
