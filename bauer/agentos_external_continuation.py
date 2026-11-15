"""Single-use continuation of a previously inspected external-tool call.

The caller must independently authenticate the Gateway and bind the supplied
result to the identified call. This component does not expose an HTTP route.
"""
from __future__ import annotations

import hmac
import time
from typing import Any

from .agentos_approval_store import ApprovalConflict, RemoteApprovalStore
from .agentos_external_requirement import ExternalRequirementReader
from .tool_gateway_protocol import call_digest


class ExternalContinuationCoordinator:
    def __init__(self, *, reader: ExternalRequirementReader, store: RemoteApprovalStore):
        self.reader = reader
        self.store = store

    async def continue_with_result(self, *, owner_id: str, agent_id: str, run_id: str,
                                   session_id: str, tool_call_id: str,
                                   expected_call_digest: str, result: str) -> Any:
        if not isinstance(result, str) or len(result.encode("utf-8")) > 64 * 1024:
            raise ApprovalConflict("external result unsupported")
        paused = await self.reader._paused(owner_id=owner_id, agent_id=agent_id,
                                           run_id=run_id, session_id=session_id)
        tool = paused.requirement.tool_execution
        if (paused.tool_call_id != tool_call_id
                or not hmac.compare_digest(call_digest(tool.tool_name, tool.tool_args), expected_call_digest)):
            raise ApprovalConflict("tool call changed")
        # This record is a single-use continuation claim, not human approval.
        # Its unique constraint blocks replay even if the response is lost.
        record = self.store.issue(owner_id=owner_id, entity_type="agents", entity_id=agent_id,
                                  run_id=run_id, session_id=session_id, tool_call_id=tool_call_id,
                                  payload_digest=paused.digest, expires_at=time.time() + 300)
        self.store.decide(record.approval_id, owner_id=owner_id, approve=True)
        self.store.claim(record.approval_id, owner_id=owner_id, payload_digest=paused.digest)
        try:
            paused.requirement.set_external_execution_result(result)
            response = await paused.agent.acontinue_run(
                run_id=run_id, session_id=session_id, user_id=owner_id,
                requirements=[paused.requirement], stream=False,
            )
        except BaseException:
            self.store.settle(record.approval_id, owner_id=owner_id, completed=False)
            raise
        self.store.settle(record.approval_id, owner_id=owner_id, completed=True)
        return response
