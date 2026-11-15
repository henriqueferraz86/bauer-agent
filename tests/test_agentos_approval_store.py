"""Single-use remote approval records; no tool executes in these tests."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from bauer.agentos_approval_store import ApprovalConflict, ApprovalNotFound, RemoteApprovalStore


@pytest.fixture
def store(tmp_path):
    return RemoteApprovalStore(tmp_path / "approval.db")


def issue(store):
    return store.issue(owner_id="alice", entity_type="agents", entity_id="bauer.dev",
                       run_id="run-1", session_id="session-1", tool_call_id="call-1",
                       payload_digest="a" * 64, expires_at=time.time() + 60)


def test_owner_decision_digest_and_single_consumption(store):
    record = issue(store)
    with pytest.raises(ApprovalNotFound):
        store.decide(record.approval_id, owner_id="bob", approve=True)
    assert store.decide(record.approval_id, owner_id="alice", approve=True).state == "APPROVED"
    assert store.decide(record.approval_id, owner_id="alice", approve=True).state == "APPROVED"
    with pytest.raises(ApprovalConflict):
        store.claim(record.approval_id, owner_id="alice", payload_digest="b" * 64)
    assert store.claim(record.approval_id, owner_id="alice", payload_digest="a" * 64).state == "CONSUMING"
    with pytest.raises(ApprovalConflict):
        store.claim(record.approval_id, owner_id="alice", payload_digest="a" * 64)
    assert store.settle(record.approval_id, owner_id="alice", completed=False).state == "UNKNOWN"
    with pytest.raises(ApprovalConflict):
        store.claim(record.approval_id, owner_id="alice", payload_digest="a" * 64)


def test_concurrent_claim_exactly_one_wins(store):
    record = issue(store)
    store.decide(record.approval_id, owner_id="alice", approve=True)

    def claim(_):
        try:
            return store.claim(record.approval_id, owner_id="alice", payload_digest="a" * 64).state
        except ApprovalConflict:
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(claim, range(8)))
    assert results.count("CONSUMING") == 1
    assert results.count("CONFLICT") == 7


def test_cancellation_and_tool_claim_are_mutually_exclusive_across_store_instances(tmp_path):
    path = tmp_path / "race.db"
    store = RemoteApprovalStore(path)
    other = RemoteApprovalStore(path)
    record = issue(store)
    store.decide(record.approval_id, owner_id="alice", approve=True)
    start = threading.Barrier(2)

    def claim():
        start.wait()
        try:
            store.claim(record.approval_id, owner_id="alice", payload_digest="a" * 64)
            return "claimed"
        except ApprovalConflict:
            return "claim blocked"

    def cancel():
        start.wait()
        try:
            other.request_cancel(owner_id="alice", entity_type="agents", entity_id="bauer.dev",
                                 run_id="run-1", session_id="session-1")
            return "cancel intent"
        except ApprovalConflict:
            return "cancel blocked"

    with ThreadPoolExecutor(max_workers=2) as executor:
        a, b = executor.submit(claim), executor.submit(cancel)
        outcomes = {a.result(), b.result()}
    assert outcomes in ({"claimed", "cancel blocked"}, {"claim blocked", "cancel intent"})


def test_denied_expired_duplicate_and_crash_do_not_reexecute(store, monkeypatch):
    record = issue(store)
    with pytest.raises(ApprovalConflict):
        issue(store)
    assert store.decide(record.approval_id, owner_id="alice", approve=False).state == "DENIED"
    with pytest.raises(ApprovalConflict):
        store.decide(record.approval_id, owner_id="alice", approve=True)
    with pytest.raises(ApprovalConflict):
        store.claim(record.approval_id, owner_id="alice", payload_digest="a" * 64)
    other = store.issue(owner_id="alice", entity_type="agents", entity_id="bauer.dev",
                        run_id="run-2", session_id="session-2", tool_call_id="call-2",
                        payload_digest="b" * 64, expires_at=time.time() + 60)
    monkeypatch.setattr("bauer.agentos_approval_store.time.time", lambda: other.expires_at + 1)
    with pytest.raises(ApprovalConflict):
        store.decide(other.approval_id, owner_id="alice", approve=True)
    with pytest.raises(ApprovalConflict):
        store.decide(record.approval_id, owner_id="alice", approve=False)


def test_decision_api_authenticates_owner_and_scope(store):
    jwt = pytest.importorskip("jwt")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from bauer.agentos_approval_api import build_approval_router
    from bauer.agentos_jwt_claims import AgentOSJWTClaimsMiddleware

    key = "test-key-for-approval-with-32-bytes"
    record = issue(store)
    app = FastAPI()
    app.include_router(build_approval_router(store))
    app.add_middleware(AgentOSJWTClaimsMiddleware, key=key, audience="approval", issuer="test")

    def headers(user, scope):
        token = jwt.encode({"sub": user, "iss": "test", "aud": "approval", "exp": time.time() + 60,
                            "scopes": [scope]}, key, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    path = f"/bauer/approvals/{record.approval_id}/decision"
    with TestClient(app) as client:
        assert client.post(path, json={"approve": True}).status_code == 401
        assert client.post(path, json={"approve": True}, headers=headers("bob", "agents:bauer.dev:run")).status_code == 404
        assert client.post(path, json={"approve": True}, headers=headers("alice", "agents:bauer.qa:run")).status_code == 404
        assert client.post(path, json={"approve": "true"}, headers=headers("alice", "agents:bauer.dev:run")).status_code == 422
        approved = client.post(path, json={"approve": True}, headers=headers("alice", "agents:bauer.dev:run"))
        assert approved.status_code == 200 and approved.json()["state"] == "APPROVED"
        assert client.post(path, json={"approve": False}, headers=headers("alice", "agents:bauer.dev:run")).status_code == 409
