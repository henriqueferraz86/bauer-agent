"""Exercise the real Agno external-tool contract without production services."""
from __future__ import annotations

import json
import time
import asyncio
import sqlite3

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("agno")
jwt = pytest.importorskip("jwt")

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from agno.os.config import AuthorizationConfig
from agno.tools.function import Function

from tests.test_agentos_hitl_proof import _ToolModel


def test_external_tool_requires_external_result_and_owned_session(tmp_path, monkeypatch):
    monkeypatch.delenv("OS_SECURITY_KEY", raising=False)
    key = "isolated-external-proof-key-over-32-bytes"
    marker = tmp_path / "unexpected-effect"

    def write_marker(content: str) -> str:
        marker.write_text(content, encoding="utf-8")
        return "unsafe"

    tool = Function.from_callable(write_marker)
    tool.external_execution = True
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
            "message": "call tool", "session_id": "s1", "stream": "false",
        })
        assert response.status_code == 200, response.text
        paused = response.json()
        assert paused["status"].upper() == "PAUSED"
        execution = paused["requirements"][0]["tool_execution"]
        assert execution["tool_call_id"] == "call-1"
        assert execution["external_execution_required"] is True
        assert not marker.exists()
        from bauer.agentos_approval_store import ApprovalConflict
        from bauer.agentos_external_requirement import ExternalRequirementReader
        from bauer.agentos_requirement_attestation import RequirementAttestor
        from bauer.tool_gateway_protocol import verify_requirement_proof

        reader = ExternalRequirementReader(agents={"demo": agent}, allowed_tools={"demo": {"write_marker"}})
        attestor = RequirementAttestor(coordinator=reader, key=key)
        arguments = {"agent_id": "demo", "remote_run_id": paused["run_id"],
                     "remote_session_id": "s1", "challenge": "a" * 64}
        with pytest.raises(ApprovalConflict):
            asyncio.run(attestor.attest(subject="bob", **arguments))
        attested = asyncio.run(attestor.attest(subject="alice", **arguments))
        assert attested["statement"]["action"] == "write_marker"
        assert attested["statement"]["args"] == {"content": "hello"}
        assert verify_requirement_proof(key, arguments["challenge"], attested["statement"], attested["proof"])
        from fastapi import FastAPI
        from bauer.agentos_jwt_claims import AgentOSJWTClaimsMiddleware
        from bauer.agentos_requirement_api import build_requirement_router

        api = FastAPI()
        api.include_router(build_requirement_router(attestor))
        api.add_middleware(AgentOSJWTClaimsMiddleware, key=key, audience="test", issuer="test-issuer")
        payload = {"agent_id": "demo", "run_id": paused["run_id"],
                   "session_id": "s1", "challenge": arguments["challenge"]}
        with TestClient(api) as inspection:
            route = "/bauer/tool-requirement"
            assert inspection.post(route, json=payload).status_code == 401
            assert inspection.post(route, headers=auth("bob"), json=payload).status_code == 404
            assert inspection.post(route, headers=auth("alice"), json={**payload, "action": "forged"}).status_code == 422
            assert inspection.post(route, headers=auth("alice"), json={**payload, "challenge": "bad"}).status_code == 422
            approved = inspection.post(route, headers=auth("alice"), json=payload)
            assert approved.status_code == 200, approved.text
            assert verify_requirement_proof(key, payload["challenge"], approved.json()["statement"],
                                            approved.json()["proof"])
        agent.tools.append(Function.from_callable(write_marker))
        with pytest.raises(ApprovalConflict):
            asyncio.run(attestor.attest(subject="alice", **arguments))
        agent.tools.pop()
        path = f"/agents/demo/runs/{paused['run_id']}/continue"
        data = {"tools": json.dumps([{**execution, "result": "gateway result"}]),
                "session_id": "s1", "stream": "false"}
        assert client.post(path, headers=auth("bob"), data=data).status_code in {403, 404}
        from bauer.agentos_approval_store import RemoteApprovalStore
        from bauer.agentos_external_continuation import ExternalContinuationCoordinator
        from bauer.tool_gateway_protocol import call_digest, continuation_proof

        coordinator = ExternalContinuationCoordinator(reader=reader, store=RemoteApprovalStore(tmp_path / "claims.db"))
        digest = call_digest(attested["statement"]["action"], attested["statement"]["args"])
        args = {"owner_id": "alice", "agent_id": "demo", "run_id": paused["run_id"],
                "session_id": "s1", "tool_call_id": "call-1", "expected_call_digest": digest,
                "result": "gateway result"}
        with pytest.raises(ApprovalConflict):
            asyncio.run(coordinator.continue_with_result(**{**args, "tool_call_id": "wrong"}))
        continue_api = FastAPI()
        continue_api.include_router(build_requirement_router(attestor, continuation=coordinator,
                                                              continuation_key=key))
        continue_api.add_middleware(AgentOSJWTClaimsMiddleware, key=key, audience="test", issuer="test-issuer")
        body = {"agent_id": "demo", "run_id": paused["run_id"], "session_id": "s1",
                "tool_call_id": "call-1", "call_digest": digest, "result": "gateway result"}
        body["proof"] = continuation_proof(key, subject="alice", **body)

        load_run = agent.aget_run_output

        async def load_changed_arguments(*args, **kwargs):
            run = await load_run(*args, **kwargs)
            run.requirements[0].tool_execution.tool_args = {"content": "changed since attestation"}
            return run

        with TestClient(continue_api) as continuation_client:
            route = "/bauer/tool-continue"
            assert continuation_client.post(route, json=body).status_code == 401
            assert continuation_client.post(route, headers=auth("bob"), json=body).status_code == 409
            assert continuation_client.post(route, headers=auth("alice"),
                                            json={**body, "result": "forged"}).status_code == 409
            assert continuation_client.post(route, headers=auth("alice"),
                                            json={**body, "call_digest": "f" * 64}).status_code == 409
            with monkeypatch.context() as changed:
                changed.setattr(agent, "aget_run_output", load_changed_arguments)
                assert continuation_client.post(route, headers=auth("alice"), json=body).status_code == 409
            continued = continuation_client.post(route, headers=auth("alice"), json=body)
            assert continued.status_code == 200, continued.text
            assert continued.json()["status"].upper() == "COMPLETED"
            assert continuation_client.post(route, headers=auth("alice"), json=body).status_code == 409
        assert not marker.exists()

        cancelled = client.post("/agents/demo/runs", headers=auth("alice"), data={
            "message": "cancel tool", "session_id": "cancel-session", "stream": "false",
        }).json()
        coordinator.store.request_cancel(owner_id="alice", entity_type="agents", entity_id="demo",
                                         run_id=cancelled["run_id"], session_id="cancel-session")
        with pytest.raises(ApprovalConflict):
            asyncio.run(coordinator.continue_with_result(**{**args, "run_id": cancelled["run_id"],
                                                            "session_id": "cancel-session"}))
        assert not marker.exists()

        second = client.post("/agents/demo/runs", headers=auth("alice"), data={
            "message": "call again", "session_id": "s2", "stream": "false",
        }).json()
        calls = []

        async def lost_response(**kwargs):
            calls.append("started")
            raise ConnectionError("lost response")

        monkeypatch.setattr(agent, "acontinue_run", lost_response)
        retry = {**args, "run_id": second["run_id"], "session_id": "s2"}
        with pytest.raises(ConnectionError):
            asyncio.run(coordinator.continue_with_result(**retry))
        with pytest.raises(ApprovalConflict):
            asyncio.run(coordinator.continue_with_result(**retry))
        assert calls == ["started"]
        with sqlite3.connect(tmp_path / "claims.db") as connection:
            assert connection.execute("SELECT state FROM remote_approvals WHERE run_id=?",
                                      (second["run_id"],)).fetchone() == ("UNKNOWN",)
