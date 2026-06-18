"""G13/G17.3: testes paramétricos do ToolRouter + run_command background.

- Cobre invariantes de TODAS as tools registradas (schema, segurança).
- Cobre o modo background do run_command (G17.3) e o gate de segurança.
"""
from __future__ import annotations

import json
import re
import time

import pytest

from bauer.tool_router import ToolRouter, ToolError, _TOOL_SECURITY
from bauer.shell_runner import ShellRunner


@pytest.fixture
def router(tmp_path):
    return ToolRouter(workspace=tmp_path, web_enabled=True)


@pytest.fixture
def router_shell(tmp_path):
    sr = ShellRunner(workspace=tmp_path)
    return ToolRouter(workspace=tmp_path, shell_runner=sr, web_enabled=True)


# ---------------------------------------------------------------------------
# Invariantes de todas as tools registradas
# ---------------------------------------------------------------------------

def _all_tool_names(router):
    return sorted(router._tools.keys())


def test_router_has_tools(router):
    assert len(router._tools) >= 50


def test_every_tool_has_callable_fn(router):
    for name, info in router._tools.items():
        assert callable(info.get("fn")), f"{name} sem fn callable"


def test_every_tool_has_description(router):
    for name, info in router._tools.items():
        assert info.get("description", "").strip(), f"{name} sem description"


def test_every_tool_has_args_dict(router):
    for name, info in router._tools.items():
        assert isinstance(info.get("args", {}), dict), f"{name} args nao e dict"


def test_unknown_tool_raises(router):
    with pytest.raises(ToolError, match="desconhecida"):
        router.execute({"action": "ferramenta_que_nao_existe", "args": {}})


def test_missing_action_raises(router):
    with pytest.raises(ToolError, match="action"):
        router.execute({"args": {}})


# ---------------------------------------------------------------------------
# Schemas OpenAI function-calling para todas as tools
# ---------------------------------------------------------------------------

def test_get_tool_schemas_valid_shape(router):
    schemas = router.get_tool_schemas()
    assert isinstance(schemas, list) and schemas
    for s in schemas:
        assert s["type"] == "function"
        fn = s["function"]
        assert fn["name"]
        assert isinstance(fn["description"], str)
        params = fn["parameters"]
        assert params["type"] == "object"
        assert isinstance(params["properties"], dict)
        assert isinstance(params["required"], list)


def test_schema_names_unique(router):
    names = [s["function"]["name"] for s in router.get_tool_schemas()]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# Metadados de segurança (_TOOL_SECURITY)
# ---------------------------------------------------------------------------

READ_TOOLS = ["list_dir", "read_file", "search_text", "glob_files", "regex_search"]
WRITE_TOOLS = ["write_file", "append_file", "patch", "create_dir", "move_file"]


@pytest.mark.parametrize("tool", READ_TOOLS)
def test_read_tools_are_low_risk(tool):
    sec = _TOOL_SECURITY.get(tool)
    assert sec is not None, f"{tool} ausente em _TOOL_SECURITY"
    assert sec["permission"] == "read"
    assert sec["approval"] is False


@pytest.mark.parametrize("tool", WRITE_TOOLS)
def test_write_tools_have_security_entry(tool):
    sec = _TOOL_SECURITY.get(tool)
    assert sec is not None
    assert sec["permission"] in ("write", "execute")


def test_run_command_requires_approval():
    sec = _TOOL_SECURITY.get("run_command")
    assert sec is not None
    assert sec["risk"] == "high"


# ---------------------------------------------------------------------------
# G17.3 — run_command background
# ---------------------------------------------------------------------------

def test_run_command_background_returns_pid(router_shell):
    out = router_shell.execute({
        "action": "run_command",
        "args": {"command": 'python -c "print(1)"', "background": True},
    })
    assert "background" in out.lower()
    assert re.search(r"PID \d+", out)


def test_run_command_background_registers_process(router_shell):
    out = router_shell.execute({
        "action": "run_command",
        "args": {"command": 'python -c "print(1)"', "background": True},
    })
    pid = re.search(r"PID (\d+)", out).group(1)
    assert pid in router_shell._processes
    # poll deve relatar status (running ou finalizado)
    time.sleep(0.8)
    poll = router_shell.execute({"action": "process", "args": {"action": "poll", "pid": pid}})
    assert pid in poll


def test_run_command_background_respects_denylist(router_shell):
    # Comando perigoso é bloqueado mesmo em background (HARDLINE guard + denylist).
    with pytest.raises(ToolError, match=r"BLOCKED|[Bb]loquead|denylist|perigos|HARDLINE"):
        router_shell.execute({
            "action": "run_command",
            "args": {"command": "rm -rf /", "background": True},
        })


def test_run_command_background_respects_allowlist(router_shell):
    with pytest.raises(ToolError, match=r"allowlist|nao esta"):
        router_shell.execute({
            "action": "run_command",
            "args": {"command": "comando_inexistente_xyz --flag", "background": True},
        })


def test_run_command_background_invalid_flag(router_shell):
    with pytest.raises(ToolError, match="background"):
        router_shell.execute({
            "action": "run_command",
            "args": {"command": 'python -c "print(1)"', "background": "yes"},
        })


# ---------------------------------------------------------------------------
# G18 — browser tools rodam todas na MESMA thread persistente (afinidade Playwright)
# ---------------------------------------------------------------------------

def test_browser_executor_is_persistent(router):
    e1 = router._get_browser_executor()
    e2 = router._get_browser_executor()
    assert e1 is e2


def test_close_browser_executor_resets(router):
    e1 = router._get_browser_executor()
    router.close_browser_executor()
    e2 = router._get_browser_executor()
    assert e1 is not e2


def test_browser_tools_share_single_thread(router):
    import threading
    seen: list[str] = []

    def _record(args):
        seen.append(threading.current_thread().name)
        return "ok"

    # browser_snapshot TEM timeout; browser_scroll NAO tem timeout configurado.
    # Ambos devem rodar na MESMA thread dedicada — o caso browser_scroll era
    # exatamente o que caía inline (greenlet.error: Cannot switch thread).
    router._tools["browser_snapshot"]["fn"] = _record
    router._tools["browser_scroll"]["fn"] = _record

    router.execute({"action": "browser_snapshot", "args": {}})
    router.execute({"action": "browser_scroll", "args": {}})

    assert len(seen) == 2
    assert seen[0] == seen[1], "browser tools rodaram em threads diferentes"
    assert "bauer-browser" in seen[0]


def test_browser_tool_without_timeout_uses_dedicated_thread(router):
    # Regressao G18.1: browser_scroll (sem timeout) NAO pode rodar inline.
    import threading
    main_thread = threading.current_thread().name
    seen = {}

    def _record(args):
        seen["thread"] = threading.current_thread().name
        return "ok"

    router._tools["browser_scroll"]["fn"] = _record
    router.execute({"action": "browser_scroll", "args": {}})
    assert seen["thread"] != main_thread
    assert "bauer-browser" in seen["thread"]


# ---------------------------------------------------------------------------
# G18.4 — vision client resolution + capability check
# ---------------------------------------------------------------------------

def test_vision_no_client_raises_clear_error(router):
    with pytest.raises(ToolError, match="vision_model"):
        router._resolve_vision_client("browser_vision")


def test_vision_text_only_model_raises(tmp_path):
    from unittest.mock import MagicMock
    client = MagicMock()
    client.model = "deepseek-v4-flash-free"  # text-only
    r = ToolRouter(workspace=tmp_path, llm_client=client)
    with pytest.raises(ToolError, match="parece"):
        r._resolve_vision_client("vision_analyze")


@pytest.mark.parametrize("model", ["gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro", "llava:13b", "qwen2.5-vl"])
def test_vision_multimodal_model_passes(tmp_path, model):
    from unittest.mock import MagicMock
    client = MagicMock()
    client.model = model
    r = ToolRouter(workspace=tmp_path, llm_client=client)
    assert r._resolve_vision_client("browser_vision") is client


def test_vision_dedicated_client_overrides_text_main(tmp_path):
    from unittest.mock import MagicMock
    text_main = MagicMock(); text_main.model = "deepseek-v4-flash-free"
    vision = MagicMock(); vision.model = "llava"
    r = ToolRouter(workspace=tmp_path, llm_client=text_main, vision_client=vision)
    # client dedicado é usado mesmo com principal text-only (sem capability gate)
    assert r._resolve_vision_client("browser_vision") is vision


def test_looks_multimodal_heuristic():
    from bauer.tool_router import _looks_multimodal
    assert _looks_multimodal("gpt-4o")
    assert _looks_multimodal("ollama/llava:latest")
    assert not _looks_multimodal("qwen2.5-coder:3b")
    assert not _looks_multimodal("")


def test_vision_model_slot_registered():
    from bauer.auxiliary_client import VALID_SLOTS
    assert "vision_model" in VALID_SLOTS


def test_non_browser_tool_not_on_browser_thread(router_shell):
    # run_command (timeout) NAO deve usar a thread do browser.
    import threading
    out = router_shell.execute({"action": "run_command", "args": {"command": 'python -c "import threading; print(threading.current_thread().name)"'}})
    assert "bauer-browser" not in out
