"""Testes adicionais para ToolRouter — cobrindo execute, tools e casos de borda."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bauer.tool_router import SandboxError, ToolError, ToolRouter


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "arquivo.txt").write_text("linha1\nlinha2\nlinha3", encoding="utf-8")
    (workspace / "codigo.py").write_text("def hello():\n    return 42", encoding="utf-8")
    (workspace / "subdir").mkdir()
    (workspace / "subdir" / "nested.md").write_text("# Titulo\nconteudo", encoding="utf-8")
    return workspace


@pytest.fixture
def router(ws: Path) -> ToolRouter:
    return ToolRouter(workspace=ws)


# ─── execute: list_dir ───────────────────────────────────────────────────────


def test_list_dir_root(router: ToolRouter):
    result = router.execute({"action": "list_dir", "args": {"path": "."}})
    assert "arquivo.txt" in result
    assert "codigo.py" in result


def test_list_dir_subdirectory(router: ToolRouter):
    result = router.execute({"action": "list_dir", "args": {"path": "subdir"}})
    assert "nested.md" in result


def test_list_dir_nonexistent(router: ToolRouter):
    # ToolRouter lança ToolError para diretório inexistente
    with pytest.raises(ToolError):
        router.execute({"action": "list_dir", "args": {"path": "nao_existe"}})


# ─── execute: read_file ──────────────────────────────────────────────────────


def test_read_file_returns_content(router: ToolRouter):
    result = router.execute({"action": "read_file", "args": {"path": "arquivo.txt"}})
    assert "linha1" in result
    assert "linha2" in result


def test_read_file_nested(router: ToolRouter):
    result = router.execute({"action": "read_file", "args": {"path": "subdir/nested.md"}})
    assert "Titulo" in result


def test_read_file_not_found(router: ToolRouter):
    with pytest.raises(ToolError):
        router.execute({"action": "read_file", "args": {"path": "inexistente.txt"}})


# ─── execute: write_file ─────────────────────────────────────────────────────


def test_write_file_creates_new(router: ToolRouter, ws: Path):
    router.execute({"action": "write_file", "args": {"path": "novo.txt", "content": "conteudo"}})
    assert (ws / "novo.txt").exists()
    assert (ws / "novo.txt").read_text(encoding="utf-8") == "conteudo"


def test_write_file_overwrite_allowed(router: ToolRouter, ws: Path):
    router.execute({"action": "write_file", "args": {"path": "editar.txt", "content": "v1"}})
    router.execute({"action": "write_file", "args": {"path": "editar.txt", "content": "v2", "overwrite": True}})
    assert (ws / "editar.txt").read_text(encoding="utf-8") == "v2"


def test_write_file_creates_subdirs(router: ToolRouter, ws: Path):
    router.execute({"action": "write_file", "args": {"path": "novo/dir/arquivo.txt", "content": "ok"}})
    assert (ws / "novo" / "dir" / "arquivo.txt").exists()


# ─── execute: search_text ────────────────────────────────────────────────────


def test_search_text_finds_match(router: ToolRouter):
    result = router.execute({"action": "search_text", "args": {"path": ".", "pattern": "linha"}})
    assert "arquivo.txt" in result
    assert "linha" in result


def test_search_text_finds_in_python(router: ToolRouter):
    result = router.execute({"action": "search_text", "args": {"path": ".", "pattern": "def hello"}})
    assert "codigo.py" in result


def test_search_text_no_match(router: ToolRouter):
    result = router.execute({"action": "search_text", "args": {"path": ".", "pattern": "zzzxxx_nada"}})
    assert "nenhum" in result.lower() or "0" in result or "não" in result.lower()


def test_search_text_specific_file(router: ToolRouter):
    result = router.execute({"action": "search_text", "args": {"path": "arquivo.txt", "pattern": "linha2"}})
    assert "linha2" in result


# ─── execute: input parsing ──────────────────────────────────────────────────


def test_execute_accepts_dict(router: ToolRouter):
    result = router.execute({"action": "list_dir", "args": {"path": "."}})
    assert isinstance(result, str)
    assert len(result) > 0


def test_execute_accepts_json_string(router: ToolRouter):
    payload = json.dumps({"action": "list_dir", "args": {"path": "."}})
    result = router.execute(payload)
    assert isinstance(result, str)


def test_execute_invalid_json_string(router: ToolRouter):
    with pytest.raises(ToolError, match="JSON invalido"):
        router.execute("isso nao e json")


def test_execute_missing_action(router: ToolRouter):
    with pytest.raises((ToolError, KeyError, TypeError)):
        router.execute({"args": {"path": "."}})


# ─── available_tools ─────────────────────────────────────────────────────────


def test_available_tools_includes_core(router: ToolRouter):
    tools = router.available_tools()
    assert "list_dir" in tools
    assert "read_file" in tools
    assert "write_file" in tools
    assert "search_text" in tools


def test_available_tools_excludes_shell_by_default(router: ToolRouter):
    tools = router.available_tools()
    assert "run_command" not in tools


def test_available_tools_includes_shell_when_enabled(ws: Path):
    shell = MagicMock()
    router = ToolRouter(workspace=ws, shell_runner=shell)
    tools = router.available_tools()
    assert "run_command" in tools


def test_web_tools_excluded_by_default(router: ToolRouter):
    tools = router.available_tools()
    assert "web_search" not in tools
    assert "web_fetch" not in tools


def test_web_tools_included_when_enabled(ws: Path):
    router = ToolRouter(workspace=ws, web_enabled=True)
    tools = router.available_tools()
    assert "web_search" in tools
    assert "web_fetch" in tools
