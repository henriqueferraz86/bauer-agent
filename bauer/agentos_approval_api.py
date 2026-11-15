"""Unwired API for recording a remote approval decision, not executing tools.

The router must remain unavailable until the gateway can securely bind a
verified paused tool call and provide authenticated, single-use callbacks.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from .agentos_approval_store import ApprovalConflict, ApprovalNotFound, RemoteApprovalStore
from .agentos_approval_coordinator import RemoteApprovalCoordinator


class _Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: StrictBool


class _Prepare(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    agent_id: str
    run_id: str
    session_id: str


def build_approval_router(store: RemoteApprovalStore, *, coordinator: RemoteApprovalCoordinator | None = None) -> APIRouter:
    router = APIRouter()

    @router.post("/bauer/approvals/{approval_id}/decision")
    def decide(approval_id: str, body: _Decision, request: Request) -> dict[str, str]:
        identity = getattr(request.state, "bauer_jwt_identity", None)
        if not isinstance(identity, dict) or not isinstance(identity.get("subject"), str):
            raise HTTPException(status_code=401, detail="authentication required")
        owner = identity["subject"]
        try:
            approval = store.get(approval_id, owner_id=owner)
            scopes = identity.get("scopes")
            required = f"{approval.entity_type}:{approval.entity_id}:run"
            if not isinstance(scopes, list) or required not in scopes:
                raise ApprovalNotFound("approval not found")
            approval = store.decide(approval_id, owner_id=owner, approve=body.approve)
        except ApprovalNotFound as exc:
            raise HTTPException(status_code=404, detail="approval not found") from exc
        except ApprovalConflict as exc:
            raise HTTPException(status_code=409, detail="approval unavailable") from exc
        return {"approval_id": approval.approval_id, "state": approval.state}

    if coordinator is not None:
        @router.post("/bauer/approvals")
        async def prepare(body: _Prepare, request: Request) -> dict[str, str]:
            identity = getattr(request.state, "bauer_jwt_identity", None)
            if not isinstance(identity, dict) or not isinstance(identity.get("subject"), str):
                raise HTTPException(status_code=401, detail="authentication required")
            if f"agents:{body.agent_id}:run" not in identity.get("scopes", []):
                raise HTTPException(status_code=404, detail="run unavailable")
            try:
                approval = await coordinator.prepare(owner_id=identity["subject"], agent_id=body.agent_id,
                                                     run_id=body.run_id, session_id=body.session_id)
            except ApprovalConflict as exc:
                raise HTTPException(status_code=409, detail="run unavailable") from exc
            return {"approval_id": approval.approval_id, "state": approval.state}

        @router.post("/bauer/approvals/{approval_id}/continue")
        async def continue_approved(approval_id: str, request: Request) -> dict[str, str]:
            identity = getattr(request.state, "bauer_jwt_identity", None)
            if not isinstance(identity, dict) or not isinstance(identity.get("subject"), str):
                raise HTTPException(status_code=401, detail="authentication required")
            try:
                record = store.get(approval_id, owner_id=identity["subject"])
                if f"{record.entity_type}:{record.entity_id}:run" not in identity.get("scopes", []):
                    raise ApprovalNotFound("approval not found")
                result = await coordinator.continue_approved(approval_id, owner_id=identity["subject"])
            except ApprovalNotFound as exc:
                raise HTTPException(status_code=404, detail="approval not found") from exc
            except ApprovalConflict as exc:
                raise HTTPException(status_code=409, detail="approval unavailable") from exc
            status = getattr(getattr(result, "status", None), "value", getattr(result, "status", None))
            return {"approval_id": approval_id, "state": "CONSUMED", "run_status": str(status or "UNKNOWN")}

    return router
