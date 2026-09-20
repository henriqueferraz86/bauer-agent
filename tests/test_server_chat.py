"""Testes do gate de autorização da allowlist no transporte HTTP/voz."""

from __future__ import annotations

from bauer.server_chat import _allowlist_authorization_prompt


def test_server_chat_turns_allowlist_block_into_explicit_question():
    result = _allowlist_authorization_prompt([
        {
            "tool": "run_command",
            "result": (
                "Erro: Comando 'lshw' nao esta na allowlist. "
                "Antes de continuar, pergunte ao usuario se ele autoriza "
                "adicionar 'lshw' permanentemente a allowlist."
            ),
        }
    ])

    assert result == (
        "O comando 'lshw' está fora da allowlist. "
        "Você autoriza adicioná-lo permanentemente à allowlist para eu "
        "executá-lo?"
    )


def test_server_chat_leaves_other_tool_results_unchanged():
    assert _allowlist_authorization_prompt([
        {"tool": "run_command", "result": "Comando executado."}
    ]) is None
