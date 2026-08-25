"""Endpoint validation limits for ``bauer serve`` request bodies.

These tests exercise FastAPI's 422 response at the HTTP boundary. They cover
Pydantic validation limits only; the raw request body is still materialized by
the ASGI stack before validation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi", reason="FastAPI nao instalado")
from fastapi.testclient import TestClient  # noqa: E402

from bauer.server import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """App hermetic with both execution paths replaced by fixed responses."""
    from bauer.tool_router import ToolRouter

    model_client = MagicMock()
    model_client.chat_stream.return_value = iter(["ok"])
    model_client.list_models.return_value = ["test-model"]
    model_client.has_model.return_value = True
    with (
        patch("bauer.agent.run_one_turn", return_value=("ok", [])),
        patch("bauer.agent.run_one_turn_with_fallback", return_value=("ok", [])),
    ):
        app = create_app(
            model_name="test-model",
            applied_context=4096,
            router=ToolRouter(workspace=tmp_path),
            client=model_client,
            system_prompt="test",
            sessions_dir=tmp_path / "sessions",
            api_key="",
            rate_limit_requests=0,
        )
    return TestClient(app, raise_server_exceptions=True)


def test_chat_rejects_message_above_limit(client: TestClient):
    response = client.post("/chat", json={"message": "x" * 100_001})
    assert response.status_code == 422


def test_chat_accepts_message_at_limit(client: TestClient):
    response = client.post("/chat", json={"message": "x" * 100_000})
    assert response.status_code == 200


def test_oai_rejects_empty_messages(client: TestClient):
    response = client.post("/v1/chat/completions", json={"messages": []})
    assert response.status_code == 422


def test_oai_rejects_too_many_messages(client: TestClient):
    messages = [{"role": "user", "content": "ok"}] * 201
    response = client.post("/v1/chat/completions", json={"messages": messages})
    assert response.status_code == 422


def test_oai_rejects_content_above_limit(client: TestClient):
    response = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "x" * 200_001}],
    })
    assert response.status_code == 422


def test_oai_accepts_content_at_limit(client: TestClient):
    response = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "x" * 200_000}],
    })
    assert response.status_code == 200
