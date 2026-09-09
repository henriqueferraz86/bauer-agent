"""Contratos públicos do loop compartilhado por CLI e servidor."""

from pathlib import Path

from bauer.agent import _build_system_prompt, _try_parse_tool, run_one_turn
from bauer.tool_router import ToolRouter


def _router(tmp_path: Path) -> ToolRouter:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return ToolRouter(workspace=workspace)


def test_prompt_keeps_bridge_and_native_protocols_distinct(tmp_path: Path):
    router = _router(tmp_path)
    bridge = _build_system_prompt(router, tool_mode="bridge")
    native = _build_system_prompt(router, tool_mode="native")

    assert '"action": "NOME_DA_TOOL"' in bridge
    assert "function calling nativo" in native
    assert '"action": "NOME_DA_TOOL"' not in native


def test_bridge_parser_accepts_known_tool_and_rejects_unknown(tmp_path: Path):
    router = _router(tmp_path)

    assert _try_parse_tool('{"action":"list_dir","args":{"path":"."}}', router) == {
        "action": "list_dir", "args": {"path": "."},
    }
    assert _try_parse_tool('{"action":"inventada","args":{}}', router) is None


def test_run_one_turn_remains_importable_shared_api():
    assert callable(run_one_turn)
