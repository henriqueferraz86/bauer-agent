from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
import asyncio

import httpx
import pytest

pytest.importorskip("fastapi")

from bauer.core.runtime.run_manager import RunManager
from bauer.core.runtime.session_manager import SessionManager
from bauer.core.events import EventBus
from bauer.server import create_app


def test_chat_endpoint_records_run_and_session(tmp_path: Path):
    mock_router = MagicMock()
    mock_router.available_tools.return_value = []
    mock_router.tool_info.side_effect = lambda name: {"name": name}

    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter(["hello"])
    mock_client.list_models.return_value = []

    app = create_app(
        model_name="test-model",
        applied_context=4096,
        router=mock_router,
        client=mock_client,
        system_prompt="system",
        sessions_dir=tmp_path / "sessions",
        rate_limit_requests=0,
    )

    async def _post_chat():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/chat", json={"message": "hi", "session_id": "session-1"})

    response = asyncio.run(_post_chat())

    assert response.status_code == 200
    runtime_root = tmp_path / "runtime"
    sessions = SessionManager(root=runtime_root).list_sessions()
    runs = RunManager(root=runtime_root).list_runs()
    assert [session.id for session in sessions] == ["session-1"]
    assert len(runs) == 1
    assert runs[0].session_id == "session-1"
    assert runs[0].status == "completed"
    assert runs[0].output == {"response": "hello"}
    event_types = [event.event_type for event in EventBus(root=runtime_root).list_events(run_id=runs[0].id)]
    assert event_types == ["run.created", "run.started", "run.completed"]


def test_events_endpoints_return_runtime_events(tmp_path: Path):
    mock_router = MagicMock()
    mock_router.available_tools.return_value = []
    mock_router.tool_info.side_effect = lambda name: {"name": name}

    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter(["hello"])
    mock_client.list_models.return_value = []

    app = create_app(
        model_name="test-model",
        applied_context=4096,
        router=mock_router,
        client=mock_client,
        system_prompt="system",
        sessions_dir=tmp_path / "sessions",
        rate_limit_requests=0,
    )

    async def _exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.post("/chat", json={"message": "hi", "session_id": "session-1"})
            events_response = await client.get("/events")
            run_id = events_response.json()["events"][0]["run_id"]
            run_events_response = await client.get(f"/runs/{run_id}/events")
            return events_response, run_events_response

    events_response, run_events_response = asyncio.run(_exercise())

    assert events_response.status_code == 200
    assert run_events_response.status_code == 200
    assert len(events_response.json()["events"]) >= 3
    assert {event["event_type"] for event in run_events_response.json()["events"]} >= {
        "run.created",
        "run.completed",
    }


def test_observability_endpoints_return_runs_trace_audit_and_metrics(tmp_path: Path):
    mock_router = MagicMock()
    mock_router.available_tools.return_value = []
    mock_router.tool_info.side_effect = lambda name: {"name": name}

    mock_client = MagicMock()
    mock_client.chat_stream.return_value = iter(["hello"])
    mock_client.list_models.return_value = []

    app = create_app(
        model_name="test-model",
        applied_context=4096,
        router=mock_router,
        client=mock_client,
        system_prompt="system",
        sessions_dir=tmp_path / "sessions",
        rate_limit_requests=0,
    )

    async def _exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.post("/chat", json={"message": "hi", "session_id": "session-1"})
            runs_response = await client.get("/runs")
            run_id = runs_response.json()["runs"][0]["id"]
            return (
                runs_response,
                await client.get(f"/runs/{run_id}"),
                await client.get(f"/runs/{run_id}/trace"),
                await client.get("/audit"),
                await client.get("/metrics"),
            )

    runs_response, run_response, trace_response, audit_response, metrics_response = asyncio.run(_exercise())

    assert runs_response.status_code == 200
    assert run_response.status_code == 200
    assert trace_response.status_code == 200
    assert audit_response.status_code == 200
    assert metrics_response.status_code == 200
    assert runs_response.json()["runs"][0]["status"] == "completed"
    assert {span["name"] for span in trace_response.json()["spans"]} >= {"run.created", "run.completed"}
    assert {record["action"] for record in audit_response.json()["audit"]} >= {"run.created", "run.completed"}
    metrics_text = metrics_response.text
    assert "bauer_runs_total 1" in metrics_text
    assert "bauer_runs_active 0" in metrics_text
    assert "bauer_runs_failed_total 0" in metrics_text
    assert "bauer_approvals_pending 0" in metrics_text
    assert "bauer_policy_denied_total 0" in metrics_text
    assert "bauer_skill_executions_total 0" in metrics_text
    assert "bauer_agent_runtime_adapter_calls_total 1" in metrics_text
