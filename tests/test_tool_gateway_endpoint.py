"""Experimental endpoint: real policy and ToolRouter; no side effects on denial."""
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bauer.tool_gateway_authorization import ToolGatewayDenied
from bauer.tool_gateway_endpoint import ToolCallLedger, build_tool_gateway_router
from bauer.tool_gateway_protocol import call_digest
from bauer.tool_router import ToolRouter
from tests.test_tool_gateway_authorization import env  # noqa: F401


def test_endpoint_requires_service_and_subject_denies_write_and_replay(env, tmp_path):
    auth, token, _, runs, _, _, _, workspace, _ = env
    (workspace / "example.txt").write_text("safe contents", encoding="utf-8")
    key = "isolated-service-key-at-least-32-bytes"
    app = FastAPI()
    app.include_router(build_tool_gateway_router(authorizer=auth, ledger=ToolCallLedger(runs.store.root / "runtime_state.sqlite3"),
                                                service_key=key, policy_root=runs.store.root))
    path = "/bauer/tool-gateway/call"
    headers = {"Authorization": token(), "X-Bauer-Service-Key": key}
    with TestClient(app) as client:
        assert client.post(path, json={"action": "read_file", "args": {"path": "example.txt"}}).status_code == 401
        assert client.post(path, headers={**headers, "X-Bauer-Service-Key": "wrong"},
                           json={"action": "read_file", "args": {"path": "example.txt"}}).status_code == 401
        assert client.post(path, headers={**headers, "Authorization": token({"sub": "tg-other"})},
                           json={"action": "read_file", "args": {"path": "example.txt"}}).status_code == 403
        assert client.post(path, headers=headers, json={"action": "write_file", "args": {
            "path": "example.txt", "content": "modified"}}).status_code == 403
        assert (workspace / "example.txt").read_text(encoding="utf-8") == "safe contents"
        assert client.post(path, headers=headers, json={"action": "read_file", "args": {},
                                                       "workspace": str(tmp_path)}).status_code == 422
        response = client.post(path, headers=headers, json={"action": "read_file", "args": {"path": "example.txt"}})
        assert response.status_code == 200, response.text
        assert "safe contents" in response.json()["result"]
        assert client.post(path, headers=headers, json={"action": "read_file", "args": {"path": "example.txt"}}).status_code == 409
        # A newly signed JWT does not permit replaying the same remote call.
        new_headers = {**headers, "Authorization": token({"jti": "new-token"})}
        assert client.post(path, headers=new_headers, json={"action": "read_file", "args": {"path": "example.txt"}}).status_code == 409
        altered = {**headers, "Authorization": token({"jti": "third-token",
                  "call_digest": call_digest("read_file", {"path": "other.txt"})})}
        assert client.post(path, headers=altered, json={"action": "read_file", "args": {"path": "other.txt"}}).status_code == 409
        assert not (workspace.parent / "runtime").exists()
        assert not (workspace.parent / "logs").exists()
        assert (runs.store.root / "tool-gateway-audit").is_dir()


@pytest.mark.parametrize("policy_action", ["deny", "ask"])
def test_policy_denial_precedes_real_tool_execution(env, tmp_path, monkeypatch, policy_action):
    auth, token, _, runs, run, _, _, workspace, _ = env
    (workspace / "example.txt").write_text("secret", encoding="utf-8")
    rules = tmp_path / "policy.yaml"
    rules.write_text("rules:\n  - id: deny-read\n    operation: filesystem.read\n"
                     f"    action: {policy_action}\n    reason: denied\n", encoding="utf-8")
    calls = []
    original = ToolRouter._read_file

    def spy(self, args):
        calls.append(args)
        return original(self, args)

    monkeypatch.setattr(ToolRouter, "_read_file", spy)
    key = "isolated-service-key-at-least-32-bytes"
    app = FastAPI()
    app.include_router(build_tool_gateway_router(authorizer=auth, ledger=ToolCallLedger(runs.store.root / "runtime_state.sqlite3"),
                                                service_key=key, policy_root=runs.store.root,
                                                policy_rules_path=rules))
    with TestClient(app) as client:
        response = client.post("/bauer/tool-gateway/call",
                               headers={"Authorization": token(), "X-Bauer-Service-Key": key},
                               json={"action": "read_file", "args": {"path": "example.txt"}})
        assert response.status_code == 403, response.text
        assert client.post("/bauer/tool-gateway/call",
                           headers={"Authorization": token({"jti": "new-token"}), "X-Bauer-Service-Key": key},
                           json={"action": "read_file", "args": {"path": "example.txt"}}).status_code == 409
    assert calls == []
    assert runs.store.list("approvals") == []
    assert runs.get_run(run.id).status == "running"
    assert runs.complete_run(run.id, output={}).status == "completed"


@pytest.mark.parametrize(("action", "args"), [
    ("list_dir", {"path": "."}),
    ("search_text", {"path": ".", "pattern": "private"}),
    ("read_file", {"path": ".env"}),
    ("read_file", {"path": "shortcut.txt"}),
    ("read_file", {"path": "missing.txt"}),
])
def test_initial_profile_rejects_non_exportable_reads(env, action, args):
    auth, token, _, runs, run, _, _, workspace, _ = env
    (workspace / ".env").write_text("private test content", encoding="utf-8")
    if args.get("path") == "shortcut.txt":
        try:
            (workspace / "shortcut.txt").symlink_to(workspace / ".env")
        except OSError:
            pytest.skip("symlinks unavailable")
    key = "isolated-service-key-at-least-32-bytes"
    app = FastAPI()
    app.include_router(build_tool_gateway_router(authorizer=auth, ledger=ToolCallLedger(runs.store.path),
                                                service_key=key, policy_root=runs.store.root))
    with TestClient(app) as client:
        response = client.post("/bauer/tool-gateway/call",
                               headers={"Authorization": token({"call_digest": call_digest(action, args)}),
                                        "X-Bauer-Service-Key": key}, json={"action": action, "args": args})
    assert response.status_code == 403
    assert runs.complete_run(run.id, output={}).status == "completed"


def test_policy_root_must_match_kernel_store(env, tmp_path):
    auth, _, _, _, *_ = env
    with pytest.raises(ValueError, match="authoritative"):
        build_tool_gateway_router(authorizer=auth, ledger=ToolCallLedger(tmp_path / "ledger.db"),
                                  service_key="isolated-service-key-at-least-32-bytes",
                                  policy_root=tmp_path / "wrong-runtime")


def test_bounded_body_and_concurrent_token_replay(env, tmp_path):
    auth, token, _, runs, _, _, _, workspace, _ = env
    (workspace / "x.txt").write_text("ok", encoding="utf-8")
    key = "isolated-service-key-at-least-32-bytes"
    app = FastAPI()
    app.include_router(build_tool_gateway_router(authorizer=auth, ledger=ToolCallLedger(runs.store.root / "runtime_state.sqlite3"),
                                                service_key=key, policy_root=runs.store.root))
    headers = {"Authorization": token({"call_digest": call_digest("read_file", {"path": "x.txt"})}),
               "X-Bauer-Service-Key": key}
    path = "/bauer/tool-gateway/call"
    with TestClient(app) as client:
        assert client.post(path, headers=headers, content=b"x" * (16 * 1024 + 1)).status_code == 413
        assert client.post(path, headers=headers, content=iter([b"x" * 9000, b"x" * 9000])).status_code == 413

    def invoke(_):
        with TestClient(app) as client:
            return client.post(path, headers=headers, json={"action": "read_file", "args": {"path": "x.txt"}}).status_code

    with ThreadPoolExecutor(max_workers=6) as executor:
        statuses = list(executor.map(invoke, range(6)))
    assert statuses.count(200) == 1 and statuses.count(409) == 5


def test_revalidation_failure_after_claim_never_calls_tool(env, tmp_path, monkeypatch):
    auth, token, _, runs, _, _, _, workspace, _ = env
    (workspace / "x.txt").write_text("ok", encoding="utf-8")
    original = auth.authorize
    counter = []
    called = []

    def stale(bearer):
        counter.append(1)
        if len(counter) > 1:
            raise ToolGatewayDenied("terminated")
        return original(bearer)

    def spy(self, args):
        called.append(args)
        return "unexpected"

    monkeypatch.setattr(auth, "authorize", stale)
    monkeypatch.setattr(ToolRouter, "_read_file", spy)
    key = "isolated-service-key-at-least-32-bytes"
    app = FastAPI()
    app.include_router(build_tool_gateway_router(authorizer=auth, ledger=ToolCallLedger(runs.store.root / "runtime_state.sqlite3"),
                                                service_key=key, policy_root=runs.store.root))
    with TestClient(app) as client:
        response = client.post("/bauer/tool-gateway/call",
                               headers={"Authorization": token({"call_digest": call_digest("read_file", {"path": "x.txt"})}),
                                        "X-Bauer-Service-Key": key},
                               json={"action": "read_file", "args": {"path": "x.txt"}})
        assert response.status_code == 403
    assert not called


def test_run_barrier_serializes_tool_with_cancellation_across_instances(tmp_path):
    first = ToolCallLedger(tmp_path / "ledger.db")
    second = ToolCallLedger(tmp_path / "ledger.db")
    start = threading.Barrier(2)

    def invoke():
        start.wait()
        return first.claim("token", "digest", run_id="run", remote_run_id="remote", tool_call_id="call")

    def cancel():
        start.wait()
        return second.request_cancel(run_id="run")

    with ThreadPoolExecutor(max_workers=2) as executor:
        call_future = executor.submit(invoke)
        cancel_future = executor.submit(cancel)
        claimed, cancelled = call_future.result(), cancel_future.result()
    assert (claimed, cancelled) in {(True, False), (False, True)}
    if claimed:
        # An uncertain outcome must not permit a cancellation to be presented
        # as safely ordered, even after the service restarts.
        first.settle("token", completed=False)
        restarted = ToolCallLedger(tmp_path / "ledger.db")
        assert not restarted.request_cancel(run_id="run")
    else:
        assert not first.claim("new-token", "digest", run_id="run", remote_run_id="remote", tool_call_id="call")
