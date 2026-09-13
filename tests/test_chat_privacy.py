"""Public-chat privacy boundary regression tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from bauer.config_loader import UiSection
from bauer.privacy import public_messages, redact_data, redact_text
from bauer.session_store import SessionStore


def test_ui_defaults_are_safe():
    ui = UiSection()
    assert ui.show_tool_calls is False
    assert ui.show_tool_results is False
    assert ui.show_debug_events is False
    assert ui.redact_sensitive_data is True


def test_sensitive_values_are_redacted_recursively():
    value = {
        "DATABASE_URL": "postgresql://app:super-secret@db.internal/prod",
        "headers": {"Authorization": "Bearer top-secret-token"},
        "text": "PRIVATE_KEY=-----BEGIN PRIVATE KEY-----secret-----END PRIVATE KEY-----",
    }
    safe = redact_data(value)
    assert safe["DATABASE_URL"] == "[REDACTED]"
    assert safe["headers"]["Authorization"] == "[REDACTED]"
    assert "super-secret" not in json.dumps(safe)
    assert "PRIVATE KEY" not in safe["text"]
    assert "[REDACTED]" in redact_text("Authorization: Bearer abc")


def test_session_persistence_redacts_but_keeps_internal_context(tmp_path: Path):
    store = SessionStore(tmp_path / "sessions")
    messages = [
        {"role": "user", "content": "execute a consulta"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {
            "role": "tool",
            "visibility": "internal",
            "content": "DATABASE_URL=postgresql://u:senha@db/prod\nstdout bruto",
        },
        {"role": "assistant", "content": "Consulta concluída com sucesso."},
    ]
    store.save("s1", messages)

    raw = (tmp_path / "sessions" / "s1.jsonl").read_text(encoding="utf-8")
    assert "senha" not in raw
    assert "[REDACTED]" in raw
    assert len(store.load("s1")) == 4
    assert [m["role"] for m in store.load_public("s1")] == ["user", "assistant"]
    assert store.load_public("s1")[-1]["content"] == "Consulta concluída com sucesso."


def test_eventual_chat_contract_hides_tool_log_and_keeps_final(tmp_path: Path):
    from fastapi.testclient import TestClient

    from bauer.server import create_app
    from bauer.tool_router import ToolRouter

    router = ToolRouter(workspace=tmp_path)
    client_impl = type("Client", (), {
        "chat_stream": lambda self, *_args: iter(["unused"]),
        "list_models": lambda self: ["test"],
        "has_model": lambda self, _name: True,
    })()
    seen_internal_result = False

    def fake_turn(ctx, *_args):
        nonlocal seen_internal_result
        ctx.add_user("[Resultado de run_command]\nstdout bruto: 42")
        seen_internal_result = "stdout bruto: 42" in ctx.messages[-1]["content"]
        return "A resposta final é 42.", [{"tool": "run_command", "result": "stdout bruto: 42"}]

    with patch("bauer.agent.run_one_turn_with_fallback", side_effect=fake_turn):
        app = create_app(
            model_name="test",
            applied_context=4096,
            router=router,
            client=client_impl,
            system_prompt="test",
            sessions_dir=tmp_path / "sessions",
            rate_limit_requests=0,
        )
        response = TestClient(app).post("/chat", json={"message": "qual é o resultado?"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["response"] == "A resposta final é 42."
    assert payload["tool_calls"] == []
    assert "stdout bruto" not in response.text
    assert seen_internal_result is True


def test_sse_marks_tool_progress_internal_and_only_streams_safe_answer(tmp_path: Path):
    from fastapi.testclient import TestClient

    from bauer.server import create_app
    from bauer.tool_router import ToolRouter

    def fake_turn(ctx, *_args):
        from bauer.delta_stream import emit_tool

        emit_tool("run_command")
        ctx.add_user("[Resultado de run_command]\nstdout secreto")
        return "Tudo certo, a operação terminou.", []

    router = ToolRouter(workspace=tmp_path)
    client_impl = type("Client", (), {
        "chat_stream": lambda self, *_args: iter(["unused"]),
        "list_models": lambda self: ["test"],
        "has_model": lambda self, _name: True,
    })()
    with patch("bauer.agent.run_one_turn_with_fallback", side_effect=fake_turn):
        app = create_app(
            model_name="test",
            applied_context=4096,
            router=router,
            client=client_impl,
            system_prompt="test",
            sessions_dir=tmp_path / "sessions",
            rate_limit_requests=0,
        )
        response = TestClient(app).get("/stream?message=execute")

    assert response.status_code == 200
    assert '"internal": true' in response.text
    assert "stdout secreto" not in response.text
    assert "Tudo certo, a operação terminou." in response.text
