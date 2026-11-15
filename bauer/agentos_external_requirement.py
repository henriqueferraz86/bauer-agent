"""Read-only inspection of an Agno external-tool requirement.

This is a staging primitive: it neither exposes a route nor resumes a run.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .agentos_approval_coordinator import RemoteApprovalCoordinator, _PausedTool
from .agentos_approval_store import ApprovalConflict


class ExternalRequirementReader:
    """Inspect only an owned paused run and its explicitly external toolset."""

    def __init__(self, *, agents: Mapping[str, Any], allowed_tools: Mapping[str, set[str]]):
        self.agents = dict(agents)
        self.allowed_tools = {key: frozenset(value) for key, value in allowed_tools.items()}

    async def _paused(self, *, owner_id: str, agent_id: str, run_id: str,
                      session_id: str) -> _PausedTool:
        from agno.tools.function import Function

        agent = self.agents.get(agent_id)
        if agent is None:
            raise ApprovalConflict("run unavailable")
        RemoteApprovalCoordinator._validate_implicit_sources(agent)
        tools = getattr(agent, "tools", None)
        if not isinstance(tools, (list, tuple)) or not tools:
            raise ApprovalConflict("toolset unavailable")
        names: set[str] = set()
        allowed = self.allowed_tools.get(agent_id, frozenset())
        for tool in tools:
            if (not isinstance(tool, Function) or tool.external_execution is not True
                    or tool.requires_confirmation or not tool.name or tool.name in names
                    or tool.name not in allowed):
                raise ApprovalConflict("toolset contains an unsupported callable")
            names.add(tool.name)
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
        if (tool is None or not getattr(requirement, "needs_external_execution", False)
                or getattr(requirement, "needs_confirmation", False)
                or getattr(requirement, "needs_user_input", False)
                or getattr(requirement, "needs_user_feedback", False)):
            raise ApprovalConflict("run requirements unsupported")
        name, call_id, args = tool.tool_name, tool.tool_call_id, tool.tool_args
        if (not isinstance(name, str) or name not in names or not isinstance(call_id, str)
                or not call_id or not isinstance(args, dict)):
            raise ApprovalConflict("tool unavailable")
        try:
            canonical = json.dumps({"agent_id": agent_id, "run_id": run_id, "session_id": session_id,
                                    "tool_call_id": call_id, "tool_name": name, "tool_args": args},
                                   sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                                   allow_nan=False).encode("ascii")
        except (TypeError, ValueError) as exc:
            raise ApprovalConflict("tool arguments unsupported") from exc
        return _PausedTool(agent, requirement, hashlib.sha256(canonical).hexdigest(), call_id)
