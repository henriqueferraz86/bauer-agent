"""Sprint 78: erro de sistema de arquivos de uma tool não derruba o turno.

Na Beelink, dois /loop pedidos pelo agente morreram em segundos com
"erro do provedor". A causa real: `glob_files` encontrou o link quebrado
`investimentos-os/.venv/bin/python` (aponta para um Python do host, que não
existe no container), `stat()` levantou FileNotFoundError, a exceção
atravessou o turno e o loop a rotulou como `provider_error`.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from bauer.agent import run_one_turn
from bauer.context_manager import ContextManager
from bauer.openai_client import OpenAIClient
from bauer.tool_router import ToolRouter


@pytest.mark.skipif(sys.platform == "win32", reason="symlink exige privilégio no Windows")
def test_glob_files_lists_a_broken_symlink_instead_of_crashing(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    os.symlink("/nao/existe/python3.12", venv_bin / "python")
    router = ToolRouter(workspace=tmp_path)
    out = router.execute_native_call("glob_files", {"pattern": "**/*"})
    assert "app.py" in out
    assert "python  (link quebrado)" in out


class _Client(OpenAIClient):
    """1ª chamada pede a tool; 2ª responde com texto."""

    def __init__(self):
        super().__init__(host="http://unused.invalid", model="controlled")
        self._provider = "openai"
        self.calls = 0
        self.seen_tool_results: list[str] = []

    def chat_with_tools(self, model, messages, tools, **_kwargs):
        self.calls += 1
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        if self.calls == 1:
            return {"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "fs_probe", "arguments": json.dumps({}),
            }}]}
        self.seen_tool_results = [m["content"] for m in messages if m.get("role") == "tool"]
        return {"content": "segui em frente", "tool_calls": []}


def test_tool_oserror_becomes_a_tool_result_and_the_turn_continues(tmp_path):
    router = ToolRouter(workspace=tmp_path)

    def probe(_args):
        raise FileNotFoundError(2, "No such file or directory", "/x/.venv/bin/python")

    router._tools["fs_probe"] = {"fn": probe, "description": "probe", "args": {}}
    ctx = ContextManager(applied_context=4096, system_prompt="controlled")
    ctx.add_user("explore")
    client = _Client()
    text, tool_log = run_one_turn(ctx, router, client, "controlled")
    assert text == "segui em frente"
    assert client.calls == 2
    assert any("FileNotFoundError" in r and ".venv/bin/python" in r for r in client.seen_tool_results)
    assert [entry["tool"] for entry in tool_log] == ["fs_probe"]


def test_other_unexpected_tool_exceptions_still_propagate(tmp_path):
    router = ToolRouter(workspace=tmp_path)

    def probe(_args):
        raise KeyError("bug de programação")

    router._tools["fs_probe"] = {"fn": probe, "description": "probe", "args": {}}
    ctx = ContextManager(applied_context=4096, system_prompt="controlled")
    ctx.add_user("explore")
    with pytest.raises(KeyError):
        run_one_turn(ctx, router, _Client(), "controlled")
