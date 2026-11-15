"""Isolated Agno HITL contract experiment; no production routes are enabled."""
from __future__ import annotations

import json
import time
import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("agno")
jwt = pytest.importorskip("jwt")

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.base import Model
from agno.models.response import ModelResponse
from agno.os import AgentOS
from agno.os.config import AuthorizationConfig
from agno.tools.function import Function


class _ToolModel(Model):
    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        if any(m.role == "tool" for m in kwargs.get("messages", [])):
            return ModelResponse(role="assistant", content="done")
        return ModelResponse(role="assistant", tool_calls=[{
            "id": "call-1", "type": "function", "function": {
                "name": "write_marker", "arguments": json.dumps({"content": "hello"}),
            },
        }])

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self.invoke(*args, **kwargs)

    def invoke_stream(self, *args: Any, **kwargs: Any):
        yield self.invoke(*args, **kwargs)

    async def ainvoke_stream(self, *args: Any, **kwargs: Any):
        yield self.invoke(*args, **kwargs)

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response


def test_hitl_no_effect_before_confirmation_and_wrong_user_denied(tmp_path, monkeypatch):
    from bauer.agentos_approval_coordinator import RemoteApprovalCoordinator
    from bauer.agentos_approval_store import ApprovalConflict, RemoteApprovalStore
    monkeypatch.delenv("OS_SECURITY_KEY", raising=False)
    key = "isolated-hitl-proof-test-key-32-bytes"
    marker = tmp_path / "effects.txt"

    def write_marker(content: str) -> str:
        with marker.open("a", encoding="utf-8") as stream:
            stream.write(content)
        return "ok"

    tool = Function.from_callable(write_marker)
    tool.requires_confirmation = True
    db = SqliteDb(db_file=str(tmp_path / "agno.db"))
    agent = Agent(id="demo", model=_ToolModel(id="stub"), tools=[tool], db=db)
    app = AgentOS(id="test", agents=[agent], db=db, telemetry=False, tracing=False,
                  authorization=True, authorization_config=AuthorizationConfig(
                      verification_keys=[key], algorithm="HS256", verify_audience=True,
                      audience="test", user_isolation=True)).get_app()

    def auth(user):
        return {"Authorization": "Bearer " + jwt.encode({
            "sub": user, "iss": "test-issuer", "aud": "test", "exp": time.time() + 60,
            "scopes": ["agents:demo:run", "agents:demo:read"],
        }, key, algorithm="HS256")}

    with TestClient(app) as client:
        response = client.post("/agents/demo/runs", headers=auth("alice"), data={
            "message": "write", "session_id": "s1", "stream": "false",
        })
        assert response.status_code == 200, response.text
        paused = response.json()
        assert str(paused.get("status", "")).upper() == "PAUSED", paused
        assert not marker.exists()
        requirements = paused.get("requirements") or []
        assert len(requirements) == 1, paused
        execution = requirements[0].get("tool_execution")
        assert isinstance(execution, dict) and execution["tool_call_id"] == "call-1"
        approved = {**execution, "confirmed": True}
        path = f"/agents/demo/runs/{paused['run_id']}/continue"
        data = {"tools": json.dumps([approved]), "session_id": "s1", "stream": "false"}
        assert client.post(path, headers=auth("bob"), data=data).status_code in {403, 404}
        assert not marker.exists()
        store = RemoteApprovalStore(tmp_path / "approvals.db")
        denied = RemoteApprovalCoordinator(store, agents={"demo": agent}, allowed_tools={"demo": set()})
        with pytest.raises(ApprovalConflict):
            asyncio.run(denied.prepare(owner_id="alice", agent_id="demo", run_id=paused["run_id"], session_id="s1"))
        coordinator = RemoteApprovalCoordinator(store, agents={"demo": agent},
                                                allowed_tools={"demo": {"write_marker", "forbidden"}})
        with pytest.raises(ApprovalConflict):
            asyncio.run(coordinator.prepare(owner_id="bob", agent_id="demo", run_id=paused["run_id"], session_id="s1"))
        from bauer.agentos_requirement_attestation import RequirementAttestor
        from bauer.tool_gateway_protocol import verify_requirement_proof

        attestor = RequirementAttestor(coordinator=coordinator, key=key)
        challenge = "a" * 64
        with pytest.raises(ApprovalConflict):
            asyncio.run(attestor.attest(subject="bob", agent_id="demo", remote_run_id=paused["run_id"],
                                        remote_session_id="s1", challenge=challenge))
        attested = asyncio.run(attestor.attest(subject="alice", agent_id="demo", remote_run_id=paused["run_id"],
                                               remote_session_id="s1", challenge=challenge))
        statement = attested["statement"]
        assert statement["tool_call_id"] == "call-1" and statement["args"] == {"content": "hello"}
        assert verify_requirement_proof(key, challenge, statement, attested["proof"])
        assert not verify_requirement_proof(key, challenge, {**statement, "tool_call_id": "forged"}, attested["proof"])
        assert not marker.exists()
        from fastapi import FastAPI
        from bauer.agentos_approval_api import build_approval_router
        from bauer.agentos_jwt_claims import AgentOSJWTClaimsMiddleware

        approval_api = FastAPI()
        approval_api.include_router(build_approval_router(store, coordinator=coordinator))
        approval_api.add_middleware(AgentOSJWTClaimsMiddleware, key=key, audience="test", issuer="test-issuer")
        with TestClient(approval_api) as api:
            submitted = {"agent_id": "demo", "run_id": paused["run_id"], "session_id": "s1"}
            assert api.post("/bauer/approvals", headers=auth("alice"), json={**submitted, "tool_args": {}}).status_code == 422
            issued = api.post("/bauer/approvals", headers=auth("alice"), json=submitted)
            assert issued.status_code == 200, issued.text
            approval_id = issued.json()["approval_id"]
            decision = f"/bauer/approvals/{approval_id}/decision"
            followup = f"/bauer/approvals/{approval_id}/continue"
            assert api.post(decision, headers=auth("bob"), json={"approve": True}).status_code == 404
            assert api.post(followup, headers=auth("alice")).status_code == 409
            assert not marker.exists()
            assert api.post(decision, headers=auth("alice"), json={"approve": True}).status_code == 200
            forbidden_marker = tmp_path / "forbidden.txt"

            def forbidden() -> str:
                forbidden_marker.write_text("unsafe", encoding="utf-8")
                return "unsafe"

            # An unconfirmed second tool must block the entire continuation;
            # it cannot be made safe just by approving the first tool.
            agent.tools.append(Function.from_callable(forbidden))
            assert api.post(followup, headers=auth("alice")).status_code == 409
            assert not marker.exists() and not forbidden_marker.exists()
            agent.tools.pop()
            continued = api.post(followup, headers=auth("alice"))
            assert continued.status_code == 200, continued.text
            assert marker.read_text(encoding="utf-8") == "hello"
            assert api.post(followup, headers=auth("alice")).status_code == 409
            assert marker.read_text(encoding="utf-8") == "hello"

            # Simulate an effect followed by a lost continuation response. A
            # restarted coordinator must never execute that tool a second time.
            second = client.post("/agents/demo/runs", headers=auth("alice"), data={
                "message": "write again", "session_id": "s2", "stream": "false",
            }).json()
            issued2 = api.post("/bauer/approvals", headers=auth("alice"), json={
                "agent_id": "demo", "run_id": second["run_id"], "session_id": "s2",
            })
            assert issued2.status_code == 200, issued2.text
            approval2 = issued2.json()["approval_id"]
            assert api.post(f"/bauer/approvals/{approval2}/decision", headers=auth("alice"),
                            json={"approve": True}).status_code == 200
            crash_marker = tmp_path / "crash-effect.txt"

            async def effect_then_disconnect(**kwargs):
                with crash_marker.open("a", encoding="utf-8") as stream:
                    stream.write("once")
                raise ConnectionError("lost response")

            monkeypatch.setattr(agent, "acontinue_run", effect_then_disconnect)
            with pytest.raises(ConnectionError):
                api.post(f"/bauer/approvals/{approval2}/continue", headers=auth("alice"))
            restarted_store = RemoteApprovalStore(tmp_path / "approvals.db")
            assert restarted_store.get(approval2, owner_id="alice").state == "UNKNOWN"
            assert api.post(f"/bauer/approvals/{approval2}/continue", headers=auth("alice")).status_code == 409
            assert crash_marker.read_text(encoding="utf-8") == "once"
