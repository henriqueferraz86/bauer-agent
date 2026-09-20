"""Runtime primitives for executing Bauer agents."""

from __future__ import annotations

__all__ = [
    "RuntimeAgentRegistry",
    "RuntimeAgentRegistryError",
    "AgentSpec",
    "AgentManager",
    "AgentMailbox",
    "AgentMessage",
    "AgentProcess",
    "AgentSession",
    "AgentSupervisor",
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
    "TeamRun",
    "TeamRunManager",
    "TeamTask",
    "SkillExperience",
    "SkillLearningManager",
    "SkillProposal",
    "SkillValidation",
    "SkillWorkshopManager",
    "AudioFrame",
    "AudioStream",
    "InterruptionManager",
    "NexusRealtimeSession",
    "RealtimeState",
    "AdaptiveLatency",
    "DuplexMode",
    "JarvisDuplexSession",
    "SentenceBuffer",
]


def __getattr__(name: str):
    if name in {"RuntimeAgentRegistry", "RuntimeAgentRegistryError"}:
        from .agent_registry import RuntimeAgentRegistry, RuntimeAgentRegistryError

        return {"RuntimeAgentRegistry": RuntimeAgentRegistry, "RuntimeAgentRegistryError": RuntimeAgentRegistryError}[name]
    if name == "AgentSpec":
        from .agent_spec import AgentSpec

        return AgentSpec
    if name in {"AgentManager", "AgentMailbox", "AgentMessage", "AgentProcess", "AgentSession", "AgentSupervisor"}:
        from .agent_manager import (
            AgentMailbox,
            AgentManager,
            AgentMessage,
            AgentProcess,
            AgentSession,
            AgentSupervisor,
        )

        return {
            "AgentManager": AgentManager,
            "AgentMailbox": AgentMailbox,
            "AgentMessage": AgentMessage,
            "AgentProcess": AgentProcess,
            "AgentSession": AgentSession,
            "AgentSupervisor": AgentSupervisor,
        }[name]
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
    if name in {"TeamRun", "TeamRunManager", "TeamTask"}:
        from .team_orchestrator import TeamRun, TeamRunManager, TeamTask

        return {"TeamRun": TeamRun, "TeamRunManager": TeamRunManager, "TeamTask": TeamTask}[name]
    if name in {"SkillExperience", "SkillLearningManager", "SkillProposal"}:
        from .skill_learning import SkillExperience, SkillLearningManager, SkillProposal

        return {
            "SkillExperience": SkillExperience,
            "SkillLearningManager": SkillLearningManager,
            "SkillProposal": SkillProposal,
        }[name]
    if name in {"SkillValidation", "SkillWorkshopManager"}:
        from .skill_workshop import SkillValidation, SkillWorkshopManager

        return {"SkillValidation": SkillValidation, "SkillWorkshopManager": SkillWorkshopManager}[name]
    if name in {"AudioFrame", "AudioStream", "InterruptionManager", "NexusRealtimeSession", "RealtimeState"}:
        from .nexus_realtime import AudioFrame, AudioStream, InterruptionManager, NexusRealtimeSession, RealtimeState

        return {
            "AudioFrame": AudioFrame,
            "AudioStream": AudioStream,
            "InterruptionManager": InterruptionManager,
            "NexusRealtimeSession": NexusRealtimeSession,
            "RealtimeState": RealtimeState,
        }[name]
    if name in {"AdaptiveLatency", "DuplexMode", "JarvisDuplexSession", "SentenceBuffer"}:
        from .jarvis_duplex import AdaptiveLatency, DuplexMode, JarvisDuplexSession, SentenceBuffer

        return {
            "AdaptiveLatency": AdaptiveLatency,
            "DuplexMode": DuplexMode,
            "JarvisDuplexSession": JarvisDuplexSession,
            "SentenceBuffer": SentenceBuffer,
        }[name]
    raise AttributeError(name)
