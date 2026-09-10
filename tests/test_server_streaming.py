"""Contratos das primitivas SSE isoladas do servidor HTTP."""

from __future__ import annotations

from bauer.server_streaming import StreamGate, sse_frame, strip_action_blocks


def test_sse_frame_prefixes_every_line_and_event() -> None:
    assert sse_frame("linha 1\nlinha 2", event="done") == (
        "event: done\ndata: linha 1\ndata: linha 2\n\n"
    )


def test_stream_gate_holds_action_candidate_and_releases_regular_text() -> None:
    gate = StreamGate()

    assert gate.feed("Vou verificar. {") == ""
    assert gate.pending == "Vou verificar. {"
    assert gate.feed('"action": "read_file"}') == ""
    assert gate.flush() == 'Vou verificar. {"action": "read_file"}'

    regular = StreamGate()
    text = "Use {{.ServerVersion}} para conferir." + (" texto" * 20)
    assert regular.feed(text) == ""
    assert regular.flush() == text


def test_stream_gate_holds_short_web_search_shorthand() -> None:
    gate = StreamGate()

    assert gate.feed('{"query":"clima em Guarulhos"}') == ""
    assert gate.pending == '{"query":"clima em Guarulhos"}'


def test_strip_action_blocks_removes_web_search_shorthand() -> None:
    assert strip_action_blocks(
        'Antes {"query":"clima em Guarulhos"} Depois',
        {"web_search"},
    ) == "Antes  Depois"


def test_stream_gate_discards_pre_tool_narration() -> None:
    gate = StreamGate()
    gate.feed('Não consegui consultar ainda. {"query":"clima em Guarulhos"}')
    gate.discard()
    assert gate.pending == ""


def test_strip_action_blocks_preserves_narration_and_removes_all_actions() -> None:
    source = (
        "Antes\n```json\n{\"action\": \"read_file\", \"args\": {}}\n```\n\n"
        "Depois {\"action\": \"unknown\", \"args\": {}}\nFim"
    )

    assert strip_action_blocks(source, {"read_file"}) == "Antes\n\nDepois \nFim"
