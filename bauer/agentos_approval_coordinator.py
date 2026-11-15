"""Experimental, unwired coordinator for one confirmed Agno tool call.

This module never accepts tool names, arguments, or confirmation payloads from
the caller. The agent registry and trusted server-side policy/allowlist must be
provided by the embedding application. It is not a production tool gateway.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .agentos_approval_store import ApprovalConflict, RemoteApproval, RemoteApprovalStore


@dataclass(frozen=True)
class _PausedTool:
    agent: Any
    requirement: Any
    digest: str
    tool_call_id: str


class RemoteApprovalCoordinator:
    def __init__(self, store: RemoteApprovalStore, *, agents: Mapping[str, Any],
                 allowed_tools: Mapping[str, set[str]]):
        self.store = store
        self.agents = dict(agents)
        self.allowed_tools = {key: frozenset(value) for key, value in allowed_tools.items()}

    @staticmethod
    def _validate_implicit_sources(agent: Any) -> None:
        implicit_sources = (
            "read_chat_history", "read_tool_call_history", "search_past_sessions",
            "enable_agentic_memory", "enable_agentic_state", "_result_store", "_learning",
            "knowledge", "knowledge_retriever", "skills", "save_response_to_file",
            "pre_hooks", "post_hooks", "tool_hooks", "reasoning_agent",
        )
        if any(getattr(agent, name, None) for name in implicit_sources):
            raise ApprovalConflict("automatic tools or hooks unsupported")

    def _validate_toolset(self, agent_id: str, agent: Any) -> None:
        """Fail closed on automatic or unconfirmed tools before any resume.

        This checks only Agno's currently understood tool sources. It is not a
        substitute for a policy-enforcing callable or a production boundary.
        """
        from agno.tools.function import Function

        self._validate_implicit_sources(agent)
        tools = getattr(agent, "tools", None)
        if not isinstance(tools, (list, tuple)) or not tools:
            raise ApprovalConflict("toolset unavailable")
        names: set[str] = set()
        for tool in tools:
            if (not isinstance(tool, Function) or tool.requires_confirmation is not True
                    or not tool.name or tool.name in names
                    or tool.name not in self.allowed_tools.get(agent_id, frozenset())):
                raise ApprovalConflict("toolset contains an unapproved callable")
            names.add(tool.name)

    async def _paused(self, *, owner_id: str, agent_id: str, run_id: str,
                      session_id: str) -> _PausedTool:
        agent = self.agents.get(agent_id)
        if agent is None:
            raise ApprovalConflict("run unavailable")
        self._validate_toolset(agent_id, agent)
        run = await agent.aget_run_output(run_id, session_id=session_id, user_id=owner_id)
        status = getattr(getattr(run, "status", None), "value", getattr(run, "status", None))
        if (run is None or str(status).upper() != "PAUSED" or getattr(run, "run_id", None) != run_id
                or getattr(run, "session_id", None) != session_id or getattr(run, "user_id", None) != owner_id
                or getattr(run, "agent_id", None) != agent_id):
            raise ApprovalConflict("run unavailable")
        requirements = getattr(run, "requirements", None) or []
        if len(requirements) != 1:
            raise ApprovalConflict("run requirements unsupported")
        requirement = requirements[0]
        tool = getattr(requirement, "tool_execution", None)
        if (tool is None or not getattr(requirement, "needs_confirmation", False)
                or getattr(requirement, "needs_user_input", False)
                or getattr(requirement, "needs_user_feedback", False)
                or getattr(requirement, "needs_external_execution", False)):
            raise ApprovalConflict("run requirements unsupported")
        name, call_id, args = tool.tool_name, tool.tool_call_id, tool.tool_args
        if (not isinstance(name, str) or name not in self.allowed_tools.get(agent_id, frozenset())
                or not isinstance(call_id, str) or not call_id or not isinstance(args, dict)):
            raise ApprovalConflict("tool unavailable")
        try:
            canonical = json.dumps({"agent_id": agent_id, "run_id": run_id, "session_id": session_id,
                                    "tool_call_id": call_id, "tool_name": name, "tool_args": args},
                                   sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                   allow_nan=False).encode("ascii")
        except (TypeError, ValueError) as exc:
            raise ApprovalConflict("tool arguments unsupported") from exc
        return _PausedTool(agent, requirement, hashlib.sha256(canonical).hexdigest(), call_id)

    async def prepare(self, *, owner_id: str, agent_id: str, run_id: str,
                      session_id: str) -> RemoteApproval:
        paused = await self._paused(owner_id=owner_id, agent_id=agent_id, run_id=run_id,
                                    session_id=session_id)
        return self.store.issue(owner_id=owner_id, entity_type="agents", entity_id=agent_id,
                                run_id=run_id, session_id=session_id, tool_call_id=paused.tool_call_id,
                                payload_digest=paused.digest, expires_at=time.time() + 300)

    async def continue_approved(self, approval_id: str, *, owner_id: str) -> Any:
        record = self.store.get(approval_id, owner_id=owner_id)
        if record.entity_type != "agents":
            raise ApprovalConflict("entity type unsupported")
        paused = await self._paused(owner_id=owner_id, agent_id=record.entity_id, run_id=record.run_id,
                                    session_id=record.session_id)
        if paused.tool_call_id != record.tool_call_id:
            raise ApprovalConflict("tool call changed")
        # Atomic, irreversible claim happens before executing any external tool.
        self.store.claim(approval_id, owner_id=owner_id, payload_digest=paused.digest)
        try:
            paused.requirement.confirm()
            result = await paused.agent.acontinue_run(
                run_id=record.run_id, session_id=record.session_id, user_id=owner_id,
                requirements=[paused.requirement], stream=False,
            )
        except BaseException:
            self.store.settle(approval_id, owner_id=owner_id, completed=False)
            raise
        # This means continuation returned, not that the overall run succeeded.
        self.store.settle(approval_id, owner_id=owner_id, completed=True)
        return result
