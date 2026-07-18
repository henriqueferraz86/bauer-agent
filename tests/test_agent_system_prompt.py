"""Testes do system prompt mode-aware (`_build_system_prompt`).

Em modo `native` o prompt NÃO deve ensinar o formato de tool-call-como-JSON
(`{"action": ...}`) — isso fazia modelos fracos emitir a tool call como texto,
que o caminho nativo do Ollama não executa ("0 tools"). Em modo `bridge`
(default) o protocolo JSON histórico é preservado byte-a-byte. Ver plans/023.
"""

from __future__ import annotations


class _FakeRouter:
    def available_tools(self):
        return ["write_file", "run_command", "list_dir"]

    def tool_info(self, name):
        return {"args": ["path"], "description": f"tool {name}"}


def _prompt(tool_mode=None):
    from bauer.agent import _build_system_prompt

    r = _FakeRouter()
    return _build_system_prompt(r) if tool_mode is None else _build_system_prompt(r, tool_mode=tool_mode)


def test_native_omits_json_tool_format():
    p = _prompt("native")
    assert "responda SOMENTE com o JSON" not in p
    assert '{"action": "NOME_DA_TOOL"' not in p
    assert "function calling nativo" in p  # instrução nativa presente


def test_bridge_keeps_json_tool_format():
    p = _prompt("bridge")
    assert "responda SOMENTE com o JSON" in p
    assert '{"action": "NOME_DA_TOOL", "args": {"parametro": "valor"}}' in p


def test_default_equals_bridge():
    # o default preserva o comportamento histórico (todos os call sites que não
    # passam tool_mode continuam idênticos).
    assert _prompt() == _prompt("bridge")


def test_both_modes_list_tool_names():
    for mode in ("native", "bridge"):
        p = _prompt(mode)
        assert "write_file" in p and "run_command" in p and "list_dir" in p
