"""Unwired server-side attestation of an authenticated PAUSED requirement."""
from __future__ import annotations

import json
from typing import Any

from .agentos_approval_coordinator import RemoteApprovalCoordinator
from .agentos_external_requirement import ExternalRequirementReader
from .tool_gateway_protocol import requirement_proof


class RequirementAttestor:
    """Derive a tool call from the persisted Agno run, never from request args.

    The caller must supply the verified JWT subject and enforce its agent scope.
    This class is not registered as an HTTP endpoint and does not grant policy
    approval or execute a tool.
    """

    def __init__(self, *, coordinator: RemoteApprovalCoordinator | ExternalRequirementReader, key: str):
        if not isinstance(key, str) or len(key.encode("utf-8")) < 32:
            raise ValueError("invalid requirement key")
        self.coordinator, self._key = coordinator, key

    async def attest(self, *, subject: str, agent_id: str, remote_run_id: str,
                     remote_session_id: str, challenge: str) -> dict[str, Any]:
        paused = await self.coordinator._paused(owner_id=subject, agent_id=agent_id,
                                                run_id=remote_run_id, session_id=remote_session_id)
        tool = paused.requirement.tool_execution
        # Round-trip to a detached JSON object; do not sign a mutable reference.
        args = json.loads(json.dumps(tool.tool_args, ensure_ascii=True, allow_nan=False))
        statement = {"version": 1, "subject": subject, "entity_type": "agent", "entity_id": agent_id,
                     "remote_run_id": remote_run_id, "remote_session_id": remote_session_id,
                     "status": "PAUSED", "tool_call_id": tool.tool_call_id,
                     "action": tool.tool_name, "args": args}
        return {"statement": statement, "proof": requirement_proof(self._key, challenge, statement)}
