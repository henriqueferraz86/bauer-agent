"""The isolated issuer derives run/project context, never takes paths from a caller."""
import jwt
import pytest

from bauer.tool_gateway_authorization import ToolGatewayDenied
from bauer.tool_gateway_issuer import ToolGatewayIssuer
from bauer.tool_gateway_protocol import requirement_proof
from tests.test_tool_gateway_authorization import env  # noqa: F401


def test_issuer_derives_claims_and_rejects_cross_user(env):
    auth, token, claims, runs, run, remote, *_ = env
    private = token.__kwdefaults__["signing_key"]
    requirement_key = "test-requirement-key-with-over-32-bytes"
    signer = ToolGatewayIssuer(private_key=private, identity_key="identity-test-secret-with-over-32-bytes",
                               requirement_key=requirement_key, issuer="gateway", audience="tool-gateway", runs=runs,
                               remote_runs=remote, authorizer=auth)
    statement = {"version": 1, "subject": claims["sub"], "entity_type": "agent", "entity_id": "bauer.dev",
                 "remote_run_id": "remote-run", "remote_session_id": "remote-session", "status": "PAUSED",
                 "tool_call_id": "call-1", "action": "read_file", "args": {"path": "example.txt"}}
    challenge = "a" * 64
    proof = requirement_proof(requirement_key, challenge, statement)
    arguments = {"challenge": challenge, "statement": statement, "proof": proof}
    issued = signer.issue_for_requirement(authenticated_user_id="123", gateway_run_id=run.id, **arguments)
    payload = jwt.decode(issued, options={"verify_signature": False})
    assert payload["project_id"] == claims["project_id"]
    assert payload["remote_run_id"] == "remote-run"
    assert "workspace" not in payload and payload["exp"] - payload["iat"] == 60
    assert auth.authorize("Bearer " + issued).gateway_run_id == run.id
    with pytest.raises(ToolGatewayDenied):
        signer.issue_for_requirement(authenticated_user_id="124", gateway_run_id=run.id, **arguments)
    with pytest.raises(ToolGatewayDenied):
        signer.issue_for_requirement(authenticated_user_id="123", gateway_run_id=run.id,
                                     challenge=challenge, statement={**statement, "tool_call_id": "forged"}, proof=proof)
    with pytest.raises(ToolGatewayDenied):
        signer.issue_for_requirement(authenticated_user_id="123", gateway_run_id=run.id,
                                     challenge="b" * 64, statement=statement, proof=proof)
    issued2 = signer.issue_for_requirement(authenticated_user_id="123", gateway_run_id=run.id, **arguments)
    assert jwt.decode(issued2, options={"verify_signature": False})["jti"] != payload["jti"]
    assert jwt.decode(issued2, options={"verify_signature": False})["tool_call_id"] == payload["tool_call_id"]
    runs.complete_run(run.id, output={})
    with pytest.raises(ToolGatewayDenied):
        signer.issue_for_requirement(authenticated_user_id="123", gateway_run_id=run.id, **arguments)


def test_reissued_signed_requirement_cannot_repeat_tool_call(env, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from bauer.tool_gateway_endpoint import ToolCallLedger, build_tool_gateway_router

    auth, token, claims, runs, run, remote, _, workspace, _ = env
    (workspace / "example.txt").write_text("only once", encoding="utf-8")
    requirement_key = "test-requirement-key-with-over-32-bytes"
    signer = ToolGatewayIssuer(private_key=token.__kwdefaults__["signing_key"],
                               identity_key="identity-test-secret-with-over-32-bytes",
                               requirement_key=requirement_key, issuer="gateway", audience="tool-gateway",
                               runs=runs, remote_runs=remote, authorizer=auth)
    statement = {"version": 1, "subject": claims["sub"], "entity_type": "agent", "entity_id": "bauer.dev",
                 "remote_run_id": "remote-run", "remote_session_id": "remote-session", "status": "PAUSED",
                 "tool_call_id": "stable-call", "action": "read_file", "args": {"path": "example.txt"}}
    challenge = "a" * 64
    proof = requirement_proof(requirement_key, challenge, statement)
    tokens = [signer.issue_for_requirement(authenticated_user_id="123", gateway_run_id=run.id,
                                           challenge=challenge, statement=statement, proof=proof) for _ in range(2)]
    key = "isolated-service-key-at-least-32-bytes"
    app = FastAPI()
    app.include_router(build_tool_gateway_router(authorizer=auth, ledger=ToolCallLedger(runs.store.root / "runtime_state.sqlite3"),
                                                service_key=key, policy_root=runs.store.root))
    with TestClient(app) as client:
        for index, token_value in enumerate(tokens):
            response = client.post("/bauer/tool-gateway/call", headers={"Authorization": f"Bearer {token_value}",
                                   "X-Bauer-Service-Key": key}, json={"action": "read_file",
                                                                       "args": {"path": "example.txt"}})
            assert response.status_code == (200 if index == 0 else 409), response.text
