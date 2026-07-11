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

    def test_prefix_match(self):
        assert tool_phase("browser_navigate").label == "Navegando na web"
        assert tool_phase("kanban_create").label == "Atualizando o Kanban"
        assert tool_phase("lsp_hover").label == "Analisando o código"

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
