"""Real Agno SDK + real subprocess tools + HTTP decisions, without a provider."""
from __future__ import annotations

import json
import threading
import time
from unittest.mock import MagicMock

import pytest
import yaml

pytest.importorskip("agno")
from agno.models.base import Model
from agno.models.response import ModelResponse
from fastapi.testclient import TestClient

from bauer.core.policy import ApprovalManager
from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
from bauer.core.runtime.run_manager import RunManager
from bauer.server import create_app
from bauer.shell_runner import ShellRunner
from bauer.tool_router import ToolRouter


class TwoCommands(Model):
    """Only model output is scripted; approval, SDK and command execution are real."""

    def invoke(self, *args, **kwargs):
        messages = kwargs.get("messages", [])
        results = [m for m in messages if m.role == "tool"]
        if len(results) >= 2:
            return ModelResponse(role="assistant", content="duas etapas verificadas")
        step = len(results) + 1
        command = (
            'python -c "from pathlib import Path; '
            f"p=Path('effects.txt'); p.open('a').write('{step}\\n'); print('step-{step}')\""
        )
        return ModelResponse(role="assistant", tool_calls=[{
            "id": f"step-{step}", "type": "function", "function": {
                "name": "run_command", "arguments": json.dumps({"command": command}),
            },
        }])

    async def ainvoke(self, *args, **kwargs):
        return self.invoke(*args, **kwargs)

    def invoke_stream(self, *args, **kwargs):
        yield self.invoke(*args, **kwargs)

    async def ainvoke_stream(self, *args, **kwargs):
        yield self.invoke(*args, **kwargs)

    def _parse_provider_response(self, response, **kwargs):
        return response

    def _parse_provider_response_delta(self, response):
        return response


@pytest.fixture
def environment(tmp_path, monkeypatch, request):
    import bauer.server as server
    import bauer.projects_registry as registry

    mode = getattr(request, "param", "enforce")
    monkeypatch.setattr(server, "_SERVE_POLICY_MODE", mode if isinstance(mode, str) else "enforce")
    monkeypatch.setenv("BAUER_APPROVAL_WAIT_SECONDS", "8")
    monkeypatch.setattr(registry, "_DEFAULT_REGISTRY", tmp_path / "projects.json")
    monkeypatch.setattr(AgnoRuntimeAdapter, "_build_model", lambda *a, **k: TwoCommands(id="hermetic"))
    workspace = tmp_path / "selected-project"
    workspace.mkdir()
    if getattr(request, "param", False) is True:
        policy_dir = workspace / ".bauer"
        policy_dir.mkdir()
        (policy_dir / "policy.yaml").write_text(yaml.safe_dump({"rules": [
            {"id": "admission.ask", "operation": "runtime.execute", "action": "ask"},
            {"id": "shell.ask", "operation": "shell.execute", "action": "ask"},
        ]}), encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({
        "model": {"provider": "openrouter", "name": "test-model"},
        "kernel": {"enabled": True},
        "runtime": {"adapters": {"agno": {
            "db_file": str(tmp_path / "agno.db"),
            "workspace": str(tmp_path / "wrong-default"),
        }}},
    }), encoding="utf-8")
    llm = MagicMock()
    llm._provider = "openrouter"
    app = create_app(
        model_name="test-model", applied_context=4096,
        router=ToolRouter(workspace=workspace, shell_runner=ShellRunner(workspace=workspace)),
        client=llm, system_prompt="Execute the two test tools.",
        sessions_dir=tmp_path / "sessions", config_path=cfg,
        api_key="", rate_limit_requests=0,
    )
    control = TestClient(app)
    assert control.post("/api/runtime/mode", json={"runtime_mode": "agno"}).status_code == 200
    return app, control, workspace, ApprovalManager(root=tmp_path / "runtime"), RunManager(root=tmp_path / "runtime")


def start_stream(app, session):
    result = {}

    def consume():
        try:
            response = TestClient(app).get("/stream", params={"message": "execute test", "session_id": session})
            result["response"] = response
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    return thread, result


def pending(approvals, *, exclude=(), timeout=6):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = [a for a in approvals.list("pending") if a.id not in exclude]
        if records:
            return records[0]
        time.sleep(0.025)
    pytest.fail("No approval reached the persisted queue")


@pytest.mark.parametrize("environment", ["audit", "enforce"], indirect=True)
@pytest.mark.parametrize("scope", ["once", "run", "always", "deny"])
def test_playwright_outside_allowlist_emits_card_and_obeys_decision(environment, monkeypatch, tmp_path, scope):
    from bauer.shell_runner import BlockedCommandError, CommandResult, load_learned_commands

    app, control, workspace, approvals, runs = environment
    monkeypatch.setenv("BAUER_HOME", str(tmp_path / "home"))

    class PlaywrightCommands(TwoCommands):
        def invoke(self, *args, **kwargs):
            response = super().invoke(*args, **kwargs)
            for call in response.tool_calls or []:
                step = call["id"].split("-")[-1]
                call["function"]["arguments"] = json.dumps({"command": f"playwright step-{step}"})
            return response

    monkeypatch.setattr(AgnoRuntimeAdapter, "_build_model", lambda *a, **k: PlaywrightCommands(id="hermetic"))
    executed = []

    def execute(runner, args):
        executed.append(list(args))
        return CommandResult(command=args, returncode=0, stdout="ok", stderr="", elapsed_ms=1)

    monkeypatch.setattr(ShellRunner, "_execute", execute)
    thread, result = start_stream(app, "playwright-session")
    try:
        first = pending(approvals, timeout=20)
        assert not executed
        assert "playwright" in first.reason
        endpoint = "deny" if scope == "deny" else "approve"
        body = {} if scope == "deny" else {"scope": scope}
        response = control.post(f"/api/approvals/{first.id}/{endpoint}", json=body)
        assert response.status_code == 200, response.text
        if scope == "once":
            second = pending(approvals, exclude={first.id})
            assert executed == [["playwright", "step-1"]]
            assert control.post(f"/api/approvals/{second.id}/approve").status_code == 200
        thread.join(10)
        assert not thread.is_alive(), result
        assert "error" not in result
        assert "event: approval" in result["response"].text
        if scope == "deny":
            assert executed == []
            assert runs.get_run(first.run_id).status == "failed"
        else:
            assert executed == [["playwright", "step-1"], ["playwright", "step-2"]]
            assert runs.get_run(first.run_id).status == "completed"
        assert ("playwright" in load_learned_commands()) == (scope == "always")
        if scope != "always":
            with pytest.raises(BlockedCommandError):
                ShellRunner(workspace=workspace).validate("playwright step-1")
    finally:
        for record in approvals.list("pending"):
            control.post(f"/api/approvals/{record.id}/deny")
        thread.join(10)


@pytest.mark.parametrize("scope,expected_decisions", [("once", 2), ("run", 1), ("text", 1)])
def test_real_agno_chat_approval_runs_each_command_once(environment, scope, expected_decisions):
    app, control, workspace, approvals, runs = environment
    thread, result = start_stream(app, "first-session")
    first = pending(approvals)
    assert not (workspace / "effects.txt").exists()
    try:
        if scope == "text":
            response = control.get("/stream", params={"message": "confirmo", "session_id": "first-session"})
            assert "Aprovação registrada" in response.text
            assert len([r for r in runs.list_runs() if r.session_id == "first-session"]) == 1
        else:
            response = control.post(f"/api/approvals/{first.id}/approve", json={"scope": scope})
        assert response.status_code == 200, response.text
        repeated = control.post(f"/api/approvals/{first.id}/approve", json={"scope": "run" if scope == "text" else scope})
        assert repeated.status_code == 200, repeated.text
        if scope == "once":
            second = pending(approvals, exclude={first.id})
            assert control.post(f"/api/approvals/{second.id}/approve").status_code == 200
        thread.join(10)
        assert not thread.is_alive(), result
        assert "error" not in result, result
        response = result["response"]
        assert response.status_code == 200
        assert "event: approval" in response.text
        assert "event: done" in response.text
        assert "duas etapas verificadas" in response.text
        assert (workspace / "effects.txt").read_text().splitlines() == ["1", "2"]
        assert len(approvals.list()) == expected_decisions
        assert runs.get_run(first.run_id).status == "completed"
        history = control.get("/sessions/first-session/messages").json()["messages"]
        assert any("duas etapas verificadas" in str(message) for message in history)
    finally:
        for record in approvals.list("pending"):
            control.post(f"/api/approvals/{record.id}/deny")
        thread.join(10)

    # The next task cannot inherit a run grant, even in the same browser.
    next_thread, next_result = start_stream(app, "next-session")
    next_approval = pending(approvals)
    assert next_approval.run_id != first.run_id
    assert (workspace / "effects.txt").read_text().splitlines() == ["1", "2"]
    assert control.post(f"/api/approvals/{next_approval.id}/deny").status_code == 200
    next_thread.join(10)
    assert not next_thread.is_alive(), next_result
    assert runs.get_run(next_approval.run_id).status == "failed"
    assert (workspace / "effects.txt").read_text().splitlines() == ["1", "2"]
    assert "duas etapas verificadas" not in next_result["response"].text


@pytest.mark.parametrize("prefix", ["/api", ""])
def test_approval_api_validates_scope(environment, prefix):
    _, control, _, _, _ = environment
    for body in ({"scope": "forever"}, {"scope": []}, {"scope": "run", "command": "changed"}):
        assert control.post(f"{prefix}/approvals/missing/approve", json=body).status_code == 422


def test_cancel_while_waiting_never_executes_or_resurrects(environment):
    app, control, workspace, approvals, runs = environment
    thread, result = start_stream(app, "cancel-session")
    record = pending(approvals)
    runs.cancel_run(record.run_id)
    assert control.post(f"/api/approvals/{record.id}/approve").status_code == 409
    thread.join(10)
    assert not thread.is_alive(), result
    assert runs.get_run(record.run_id).status == "cancelled"
    assert not (workspace / "effects.txt").exists()
    assert len(approvals.list()) == 1


def test_expired_approval_ends_run_without_second_request(environment, monkeypatch):
    monkeypatch.setenv("BAUER_APPROVAL_WAIT_SECONDS", "1")
    app, control, workspace, approvals, runs = environment
    thread, result = start_stream(app, "expire-session")
    record = pending(approvals)
    thread.join(10)
    assert not thread.is_alive(), result
    assert runs.get_run(record.run_id).status == "failed"
    assert approvals.get(record.id).status == "expired"
    assert len(approvals.list()) == 1
    assert not (workspace / "effects.txt").exists()
    assert control.post(f"/api/approvals/{record.id}/approve").status_code == 409


def test_waiting_for_human_does_not_exhaust_stream_execution_timeout(environment, monkeypatch):
    import bauer.server as server

    app, control, workspace, approvals, runs = environment
    # Leave real SDK/subprocess startup a realistic execution budget; the wait
    # itself is deliberately longer than that budget.
    monkeypatch.setenv("BAUER_APPROVAL_WAIT_SECONDS", "30")
    monkeypatch.setattr(server, "_STREAM_TURN_TIMEOUT_SECONDS", 8)
    thread, result = start_stream(app, "patient-session")
    record = pending(approvals)
    try:
        time.sleep(8.3)
        assert runs.get_run(record.run_id).status == "waiting_approval"
        assert thread.is_alive()
        assert control.post(f"/api/approvals/{record.id}/approve", json={"scope": "run"}).status_code == 200
        thread.join(10)
        assert not thread.is_alive(), result
        completed = runs.get_run(record.run_id)
        assert completed.status == "completed", (completed.error, result)
        assert (workspace / "effects.txt").read_text().splitlines() == ["1", "2"]
    finally:
        for approval in approvals.list("pending"):
            control.post(f"/api/approvals/{approval.id}/deny")
        thread.join(10)


@pytest.mark.parametrize("environment", [False, True], indirect=True)
def test_navigation_disconnect_can_approve_and_recover_result(environment):
    """A real HTTP disconnect, unlike TestClient which buffers SSE until done."""
    import socket

    import httpx
    uvicorn = pytest.importorskip("uvicorn")
    from bauer.http_shared import shared_ssl_context

    app, control, workspace, approvals, runs = environment
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    serving = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    serving.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.025)
        assert server.started
        url = f"http://127.0.0.1:{sock.getsockname()[1]}/stream"
        with httpx.Client(verify=shared_ssl_context(), timeout=6) as browser:
            with browser.stream("GET", url, params={"message": "execute test", "session_id": "navigation"}) as response:
                for line in response.iter_lines():
                    if line == "event: approval":
                        break
        # Browser has navigated away; decide from a separate request.
        record = pending(approvals)
        time.sleep(0.35)
        assert runs.get_run(record.run_id).status == "waiting_approval"
        if record.operation == "runtime.execute":
            assert control.post(f"/api/approvals/{record.id}/approve").status_code == 200
            record = pending(approvals, exclude={record.id})
        assert control.post(f"/api/approvals/{record.id}/approve", json={"scope": "run"}).status_code == 200
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and runs.get_run(record.run_id).status not in {"completed", "failed", "cancelled"}:
            time.sleep(0.05)
        run = runs.get_run(record.run_id)
        assert run.status == "completed", run.error
        assert (workspace / "effects.txt").read_text().splitlines() == ["1", "2"]
        history = control.get("/sessions/navigation/messages").json()["messages"]
        assert any("duas etapas verificadas" in str(message) for message in history)
    finally:
        for record in approvals.list("pending"):
            control.post(f"/api/approvals/{record.id}/deny")
        server.should_exit = True
        serving.join(5)
        sock.close()
