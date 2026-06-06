"""Tests for Kanban dashboard operational APIs."""

from __future__ import annotations

import json
import threading
from http.server import HTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import bauer.task_dispatcher as task_dispatcher_module
from bauer.kanban_server import _KanbanHandler
from bauer.kanban_store import KanbanStore
from bauer.orchestration_store import OrchestrationStore
from bauer.task_dispatcher import TaskDispatcher
from bauer.workspace_manager import WorkspaceManager


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    WorkspaceManager(workspace).init_project("Kanban Server Test")
    return workspace


def _server(workspace: Path):
    server = HTTPServer(("127.0.0.1", 0), _KanbanHandler)
    server.RequestHandlerClass.workspace = workspace  # type: ignore[attr-defined]
    server.RequestHandlerClass.company_name = "TestCo"  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def test_dashboard_serves_events_runs_and_task_metadata(tmp_path: Path):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    task = wm.add_task("Visible task", metadata={"dispatch": "true"})
    store = KanbanStore(workspace)
    store.start_run(run_id="run-visible", task_id=task.id, status="running", attempt=1)
    store.append_event(task.id, "test.event", actor="test", message="hello")
    server, base = _server(workspace)

    try:
        tasks = _get_json(f"{base}/api/tasks")
        runs = _get_json(f"{base}/api/runs?task_id={task.id}")
        events = _get_json(f"{base}/api/events?task_id={task.id}")
        ops = _get_json(f"{base}/api/ops?limit=5")
    finally:
        server.shutdown()
        server.server_close()

    assert tasks["tasks"][0]["metadata"]["dispatch"] == "true"
    assert runs["runs"][0]["run_id"] == "run-visible"
    assert events["events"][0]["event_type"] == "test.event"
    assert ops["status_counts"]["TODO"] == 1
    assert ops["recent_runs"][0]["run_id"] == "run-visible"


def test_dashboard_serves_orchestration_drilldown(tmp_path: Path):
    workspace = _workspace(tmp_path)
    store = OrchestrationStore(workspace)
    steps = [{"id": 1, "goal": "node one", "tools": False, "depends_on": [], "agent": ""}]
    store.create_run(run_id="orch-visible", objective="visible orchestration", mode="durable", plan=steps)
    store.upsert_planned_nodes("orch-visible", steps)
    store.update_node("orch-visible", 1, status="queued", task_id="001", dispatch_run_id="dispatch-1")
    server, base = _server(workspace)

    try:
        ops = _get_json(f"{base}/api/ops?limit=5")
        detail = _get_json(f"{base}/api/orchestrations?run_id=orch-visible")
    finally:
        server.shutdown()
        server.server_close()

    assert ops["recent_orchestrations"][0]["run_id"] == "orch-visible"
    assert ops["recent_orchestrations"][0]["nodes"][0]["status"] == "queued"
    assert detail["orchestrations"][0]["nodes"][0]["dispatch_run_id"] == "dispatch-1"


def test_dashboard_dispatch_action_retry_and_reclaim(tmp_path: Path, monkeypatch):
    workspace = _workspace(tmp_path)
    wm = WorkspaceManager(workspace)
    failed = wm.add_task("Failed task", status="FAILED")
    running = wm.add_task("Running task")
    dispatcher = TaskDispatcher(workspace)
    dispatcher.mark_ready(running.id)
    with dispatcher._lock():
        claimed = dispatcher._claim_locked(wm.get_task(running.id))
        wm.update_task_metadata(claimed.id, metadata={"worker_pid": 424242})
    monkeypatch.setattr(task_dispatcher_module, "_pid_alive", lambda _pid: False)
    server, base = _server(workspace)

    try:
        retry = _post_json(f"{base}/api/dispatch/action", {"action": "retry", "task_id": failed.id})
        reclaim = _post_json(f"{base}/api/dispatch/action", {"action": "reclaim"})
    finally:
        server.shutdown()
        server.server_close()

    assert retry["ok"] is True
    assert wm.get_task(failed.id).status == "READY"
    assert reclaim["crashed"] == ["T0002"]
    assert wm.get_task(running.id).status == "READY"
