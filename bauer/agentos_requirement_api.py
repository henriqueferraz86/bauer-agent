"""Authenticated inspection of a persisted external-tool requirement.

The router is opt-in and deliberately does not resume runs or execute tools.
"""
from __future__ import annotations

from typing import Any
import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from .agentos_approval_store import ApprovalConflict
from .agentos_requirement_attestation import RequirementAttestor
from .agentos_external_continuation import ExternalContinuationCoordinator
from .tool_gateway_protocol import continuation_proof


class _BoundedRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            lengths = request.headers.getlist("content-length")
            if len(lengths) > 1:
                raise HTTPException(status_code=400, detail="invalid content length")
            if lengths:
                try:
                    declared = int(lengths[0])
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="invalid content length") from exc
                if declared < 0 or declared > 160 * 1024:
                    raise HTTPException(status_code=413, detail="request too large")
            chunks: list[bytes] = []
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > 160 * 1024:
                    raise HTTPException(status_code=413, detail="request too large")
                chunks.append(chunk)
            request._body = b"".join(chunks)
            return await handler(request)

        return bounded


class _RequirementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    challenge: str = Field(pattern=r"^[0-9a-f]{64}$")


class _ContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    tool_call_id: str = Field(min_length=1, max_length=256)
    call_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: str = Field(max_length=65536)
    proof: str = Field(pattern=r"^[0-9a-f]{64}$")


def build_requirement_router(attestor: RequirementAttestor, *,
                             continuation: ExternalContinuationCoordinator | None = None,
                             continuation_key: str = "") -> APIRouter:
    router = APIRouter(route_class=_BoundedRoute)
    if continuation is not None and len(continuation_key.encode("utf-8")) < 32:
        raise ValueError("invalid continuation key")

    @router.post("/bauer/tool-requirement")
    async def inspect_requirement(body: _RequirementRequest, request: Request) -> dict[str, Any]:
        identity = getattr(request.state, "bauer_jwt_identity", None)
        if not isinstance(identity, dict) or not isinstance(identity.get("subject"), str):
            raise HTTPException(status_code=401, detail="authentication required")
        scopes = identity.get("scopes")
        if not isinstance(scopes, list) or f"agents:{body.agent_id}:run" not in scopes:
            raise HTTPException(status_code=404, detail="run unavailable")
        try:
            return await attestor.attest(subject=identity["subject"], agent_id=body.agent_id,
                                         remote_run_id=body.run_id, remote_session_id=body.session_id,
                                         challenge=body.challenge)
        except (ApprovalConflict, ValueError, TypeError) as exc:
            raise HTTPException(status_code=404, detail="run unavailable") from exc

    if continuation is not None:
        @router.post("/bauer/tool-continue")
        async def continue_tool(body: _ContinueRequest, request: Request) -> dict[str, Any]:
            identity = getattr(request.state, "bauer_jwt_identity", None)
            if not isinstance(identity, dict) or not isinstance(identity.get("subject"), str):
                raise HTTPException(status_code=401, detail="authentication required")
            scopes = identity.get("scopes")
            if not isinstance(scopes, list) or f"agents:{body.agent_id}:run" not in scopes:
                raise HTTPException(status_code=404, detail="run unavailable")
            owner = identity["subject"]
            try:
                expected = continuation_proof(continuation_key, subject=owner, agent_id=body.agent_id,
                                              run_id=body.run_id, session_id=body.session_id,
                                              tool_call_id=body.tool_call_id, call_digest=body.call_digest,
                                              result=body.result)
                if not hmac.compare_digest(expected, body.proof):
                    raise ApprovalConflict("invalid result proof")
                result = await continuation.continue_with_result(
                    owner_id=owner, agent_id=body.agent_id, run_id=body.run_id,
                    session_id=body.session_id, tool_call_id=body.tool_call_id,
                    expected_call_digest=body.call_digest, result=body.result,
                )
            except (ApprovalConflict, ValueError, TypeError) as exc:
                raise HTTPException(status_code=409, detail="continuation unavailable") from exc
            status = getattr(getattr(result, "status", None), "value", getattr(result, "status", None))
            return {"status": str(status or "UNKNOWN"), "content": getattr(result, "content", None)}

    return router
