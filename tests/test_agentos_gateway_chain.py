"""Isolated Agno -> attestation -> ToolRouter -> controlled continuation."""
from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytest.importorskip("agno")
jwt = pytest.importorskip("jwt")

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.os import AgentOS
from agno.os.config import AuthorizationConfig
from agno.tools.function import Function

from bauer.agentos_approval_store import RemoteApprovalStore
from bauer.agentos_external_continuation import ExternalContinuationCoordinator
from bauer.agentos_external_requirement import ExternalRequirementReader
from bauer.agentos_jwt_claims import AgentOSJWTClaimsMiddleware
from bauer.agentos_requirement_api import build_requirement_router
from bauer.agentos_requirement_attestation import RequirementAttestor
from bauer.remote_run_store import RemoteRunStore
from bauer.tool_gateway_authorization import ToolGatewayAuthorizer
from bauer.tool_gateway_endpoint import ToolCallLedger, build_tool_gateway_router
from bauer.tool_gateway_issuer import ToolGatewayIssuer
from bauer.tool_gateway_bridge import ToolGatewayBridge
from tests.test_agentos_hitl_proof import _ToolModel
from tests.test_tool_gateway_authorization import env  # noqa: F401


class _ReadModel(_ToolModel):
    def invoke(self, *args, **kwargs):
        response = super().invoke(*args, **kwargs)
        tool_message = next((message for message in kwargs.get("messages", []) if message.role == "tool"), None)
        if tool_message is not None:
            response.content = f"Received tool result: {tool_message.content}"
        if response.tool_calls:
            response.tool_calls[0]["function"] = {
                "name": "read_file", "arguments": json.dumps({"path": "example.txt"}),
            }
        return response


def test_isolated_read_round_trip(env, tmp_path, monkeypatch):
    monkeypatch.delenv("OS_SECURITY_KEY", raising=False)
    original_auth, token, claims, runs, run, _, bindings, workspace, registry = env
    (workspace / "example.txt").write_text("private project content", encoding="utf-8")
    subject = claims["sub"]
    identity_key = "identity-test-secret-with-over-32-bytes"
    key = "isolated-requirement-key-over-32-bytes"
    service_key = "isolated-service-key-with-over-32-bytes"
    effects = []

    def read_file(path: str) -> str:
        effects.append(path)
        raise AssertionError("Agno must not execute tools locally")

    tool = Function.from_callable(read_file)
    tool.external_execution = True
    db = SqliteDb(db_file=str(tmp_path / "agno-chain.db"))
    agent = Agent(id="bauer.dev", model=_ReadModel(id="stub"), tools=[tool], db=db)
    os_app = AgentOS(id="test", agents=[agent], db=db, telemetry=False, tracing=False,
                     authorization=True, authorization_config=AuthorizationConfig(
                         verification_keys=[identity_key], algorithm="HS256", verify_audience=True,
                         audience="test", user_isolation=True)).get_app()
    bearer = "Bearer " + jwt.encode({"sub": subject, "iss": "test-issuer", "aud": "test",
                                      "exp": time.time() + 60,
                                      "scopes": ["agents:bauer.dev:run", "agents:bauer.dev:read"]},
                                     identity_key, algorithm="HS256")
    headers = {"Authorization": bearer}
    remote = RemoteRunStore(tmp_path / "actual-remote.db")
    remote.claim(gateway_run_id=run.id, channel="telegram", update_id="42", generation=0,
                 owner_id=subject, session_id="remote-session", entity_type="agent", entity_id="bauer.dev")
    remote.transition(run.id, owner_id=subject, state="SUBMITTING")
    with TestClient(os_app) as os_client:
        submitted = os_client.post("/agents/bauer.dev/runs", headers=headers, data={
            "message": "read file", "session_id": "remote-session", "stream": "false",
        })
        assert submitted.status_code == 200, submitted.text
        paused = submitted.json()
        assert paused["status"].upper() == "PAUSED"
        remote.transition(run.id, owner_id=subject, state="RUNNING", remote_run_id=paused["run_id"])
        remote.transition(run.id, owner_id=subject, state="PAUSED")

        reader = ExternalRequirementReader(agents={"bauer.dev": agent}, allowed_tools={"bauer.dev": {"read_file"}})
        attestor = RequirementAttestor(coordinator=reader, key=key)
        coordinator = ExternalContinuationCoordinator(reader=reader, store=RemoteApprovalStore(tmp_path / "claims.db"))
        api = FastAPI()
        api.include_router(build_requirement_router(attestor, continuation=coordinator, continuation_key=key))
        api.add_middleware(AgentOSJWTClaimsMiddleware, key=identity_key, audience="test", issuer="test-issuer")
        authorizer = ToolGatewayAuthorizer(public_key=original_auth._public_key, identity_key=identity_key,
                                           issuer="gateway", audience="tool-gateway", runs=runs,
                                           remote_runs=remote, bindings=bindings, registry_path=registry)
        issuer = ToolGatewayIssuer(private_key=token.__kwdefaults__["signing_key"], identity_key=identity_key,
                                   requirement_key=key, issuer="gateway", audience="tool-gateway",
                                   runs=runs, remote_runs=remote, authorizer=authorizer)
        gateway = FastAPI()
        gateway.include_router(build_tool_gateway_router(authorizer=authorizer,
            ledger=ToolCallLedger(runs.store.root / "runtime_state.sqlite3"), service_key=service_key,
            policy_root=runs.store.root))
        with TestClient(api) as api_client, TestClient(gateway) as gateway_client:
            class AgentClient:
                def tool_requirement(self, **body):
                    reply = api_client.post("/bauer/tool-requirement", headers=headers, json=body)
                    assert reply.status_code == 200, reply.text
                    return reply.json()

                def tool_continue(self, **body):
                    reply = api_client.post("/bauer/tool-continue", headers=headers, json=body)
                    assert reply.status_code == 200, reply.text
                    return reply.json()

            def invoke(capability, action, args):
                call_headers = {"Authorization": f"Bearer {capability}", "X-Bauer-Service-Key": service_key}
                call_body = {"action": action, "args": args}
                result = gateway_client.post("/bauer/tool-gateway/call", headers=call_headers, json=call_body)
                assert result.status_code == 200, result.text
                assert "private project content" in result.json()["result"]
                assert gateway_client.post("/bauer/tool-gateway/call", headers=call_headers, json=call_body).status_code == 409
                return result.json()["result"]

            bridge = ToolGatewayBridge(client=AgentClient(), issuer=issuer, invoke=invoke, continuation_key=key)
            resumed = bridge.continue_paused(authenticated_user_id="123", gateway_run_id=run.id,
                agent_id="bauer.dev", remote_run_id=paused["run_id"], remote_session_id="remote-session")
            assert resumed["status"].upper() == "COMPLETED"
            assert "private project content" in resumed["content"]
    assert effects == []
