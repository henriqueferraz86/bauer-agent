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
