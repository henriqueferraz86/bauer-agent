"""Narração de fase (Fase 12 / Sprint 37) — tool_phase + emit no /stream."""

from __future__ import annotations

import json
from pathlib import Path

from bauer.core.ux import tool_phase


class TestToolPhase:
    def test_exact_match(self):
        assert tool_phase("run_command").label == "Executando comando"
        assert tool_phase("read_file").label == "Lendo arquivos"
        assert tool_phase("write_file").label == "Escrevendo arquivo"
        # lsp_hover/definitions/etc. viraram a tool unica "lsp" (2026-08-18) —
        # precisa de match exato, "lsp_" nao e mais prefixo de tool real.
        assert tool_phase("lsp").label == "Analisando o código"

    def test_prefix_match(self):
        assert tool_phase("browser_navigate").label == "Navegando na web"
        assert tool_phase("kanban_create").label == "Atualizando o Kanban"
        # lsp_format/lsp_rename continuam tools separadas, casam pelo prefixo.
        assert tool_phase("lsp_format").label == "Analisando o código"

    def test_default_for_unknown(self):
        assert tool_phase("alguma_tool_nova").label == "Trabalhando"
        assert tool_phase("").label == "Trabalhando"

    def test_has_icon(self):
        assert tool_phase("run_command").icon == "terminal-2"
        assert tool_phase("qualquer").icon  # default tem ícone


def test_stream_tool_event_carries_friendly_phase(tmp_path: Path):
    """O evento SSE `tool` do /stream carrega o passo humano (label + icon),
    não só o nome cru — base da narração anti-silêncio no chat."""
    from unittest.mock import MagicMock, patch
    from fastapi.testclient import TestClient

    from bauer.server import create_app
    from bauer.tool_router import ToolRouter

    ws = tmp_path / "workspace"
    ws.mkdir()
    turns = [
        iter(['{"action": "write_file", "args": {"path": "a.txt", "content": "x"}}']),
        iter(["Pronto."]),
    ]
    mock_client = MagicMock()
    mock_client.chat_stream.side_effect = lambda *a, **k: turns.pop(0)

    with patch("bauer.projects_registry._DEFAULT_REGISTRY", tmp_path / "projects.json"):
        app = create_app(
            model_name="m", applied_context=4096, router=ToolRouter(workspace=ws),
            client=mock_client, system_prompt="s", sessions_dir=tmp_path / "sessions",
            api_key="", rate_limit_requests=0,
        )
        resp = TestClient(app).get("/stream?message=cria a.txt")

    assert resp.status_code == 200
    # Frame do evento tool: procura a linha `data: {...}` após `event: tool`.
    tool_payloads = []
    lines = resp.text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == "event: tool":
            for nxt in lines[i + 1:]:
                if nxt.startswith("data: "):
                    tool_payloads.append(nxt[len("data: "):])
                    break
    assert tool_payloads, "esperava um evento SSE 'tool'"
    parsed = json.loads(tool_payloads[0])
    assert parsed["name"] == "write_file"
    assert parsed["label"] == "Escrevendo arquivo"
    assert parsed.get("icon")


def test_agno_team_stream_exposes_safe_agent_and_tool_activity(tmp_path: Path, monkeypatch):
    """Pedido explícito usa Team e projeta somente telemetria segura no SSE."""
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from bauer.core.runtime.adapters.agno_adapter import AgnoRuntimeAdapter
    from bauer.server import create_app
    from bauer.tool_router import ToolRouter

    config = tmp_path / "config.yaml"
    config.write_text(
        "model:\n  provider: ollama\n  name: test-model\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "serve-runtime-mode.json").write_text(
        json.dumps({"mode": "agno"}),
        encoding="utf-8",
    )
    captured_request: dict = {}

    def fake_stream(_adapter, request):
        captured_request.update(request)
        common = {
            "run_id": request["run_id"],
            "session_id": request["session_id"],
        }
        yield {"event": "run.started", "status": "running", **common}
        yield {
            "event": "agent.started",
            "status": "running",
            "agent_id": "bauer.research",
            "data": {
                "agent_name": "Bauer Research",
                "team_id": "bauer.software_team",
                "prompt": "não pode sair",
            },
            **common,
        }
        yield {
            "event": "tool.call.requested",
            "status": "running",
            "agent_id": "bauer.research",
            "tool_name": "read_file",
            "args": {"api_key": "não pode sair"},
            **common,
        }
        yield {"event": "message.delta", "status": "running", "content": "Consolidado.", **common}
        yield {
            "event": "agent.completed",
            "status": "completed",
            "agent_id": "bauer.research",
            "data": {"agent_name": "Bauer Research", "team_id": "bauer.software_team"},
            **common,
        }
        yield {
            "event": "run.completed",
            "status": "completed",
            "output": "Consolidado.",
            "metadata": {"tools": [{"tool_name": "read_file"}]},
            **common,
        }

    monkeypatch.setattr(AgnoRuntimeAdapter, "stream_agent", fake_stream)
    mock_client = MagicMock()
    app = create_app(
        model_name="test-model",
        applied_context=4096,
        router=ToolRouter(workspace=tmp_path),
        client=mock_client,
        system_prompt="system",
        sessions_dir=tmp_path / "sessions",
        config_path=config,
        rate_limit_requests=0,
    )

    response = TestClient(app).get(
        "/stream",
        params={"message": "investigue e use 3 agentes", "session_id": "team-session"},
    )

    assert response.status_code == 200
    assert captured_request["decision"] == {
        "team_id": "bauer.software_team",
        "requested_agents": 3,
    }
    assert "event: agent" in response.text
    assert "event: tool" in response.text
    assert "Bauer Research" in response.text
    assert "Consolidado." in response.text
    assert "não pode sair" not in response.text
