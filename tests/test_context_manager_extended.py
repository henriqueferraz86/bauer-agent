"""Testes adicionais para ContextManager — auto_summarize e _summarize_messages."""

from __future__ import annotations

from bauer.context_manager import (
    SUMMARY_THRESHOLD_TOKENS,
    ContextManager,
    _summarize_messages,
)

CHARS_PER_TOKEN = 4


# ─── _summarize_messages ─────────────────────────────────────────────────────


def test_summarize_messages_empty():
    result = _summarize_messages([])
    assert "0 perguntas" in result


def test_summarize_messages_counts_turns():
    msgs = [
        {"role": "user", "content": "primeira pergunta"},
        {"role": "assistant", "content": "primeira resposta"},
        {"role": "user", "content": "segunda pergunta"},
        {"role": "assistant", "content": "segunda resposta"},
    ]
    result = _summarize_messages(msgs)
    assert "2" in result  # 2 perguntas do usuário


def test_summarize_messages_extracts_keywords():
    msgs = [
        {"role": "user", "content": "autenticacao oauth tokens seguranca"},
        {"role": "assistant", "content": "ok"},
    ]
    result = _summarize_messages(msgs)
    # Palavras longas devem aparecer nos tópicos
    assert "autenticacao" in result or "seguranca" in result or "tokens" in result


def test_summarize_messages_detects_tools():
    msgs = [
        {"role": "user", "content": "liste os arquivos"},
        {"role": "assistant", "content": '{"action": "list_dir", "args": {"path": "."}}'},
        {"role": "user", "content": "leia o arquivo"},
        {"role": "assistant", "content": '{"action": "read_file", "args": {"path": "x.txt"}}'},
    ]
    result = _summarize_messages(msgs)
    assert "list_dir" in result or "read_file" in result


def test_summarize_messages_includes_last_assistant_response():
    msgs = [
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "Esta e a ultima resposta importante do assistente para o contexto."},
    ]
    result = _summarize_messages(msgs)
    assert "Esta e a ultima resposta" in result or "ultima" in result


def test_summarize_messages_only_user_messages():
    msgs = [
        {"role": "user", "content": "mensagem simples"},
    ]
    result = _summarize_messages(msgs)
    assert "1" in result  # 1 pergunta do usuário


# ─── _auto_summarize ─────────────────────────────────────────────────────────


def _fill_context(ctx: ContextManager, n_messages: int, chars_each: int) -> None:
    """Preenche o contexto com n_messages mensagens alternadas user/assistant."""
    for i in range(n_messages):
        if i % 2 == 0:
            ctx.messages.append({"role": "user", "content": "u" * chars_each})
        else:
            ctx.messages.append({"role": "assistant", "content": "a" * chars_each})


def test_auto_summarize_not_triggered_below_threshold():
    ctx = ContextManager(applied_context=100_000)
    # Adiciona mensagens que ficam bem abaixo do threshold
    ctx.add_user("pequena mensagem")
    assert len(ctx.messages) == 1
    # Não deve ter resumo
    assert not any(m.get("content", "").startswith("[Resumo") for m in ctx.messages)


def test_auto_summarize_triggered_above_threshold():
    """Quando tokens > 70% do budget, comprime mensagens antigas."""
    ctx = ContextManager(applied_context=200_000)  # budget=150K tokens, threshold=105K
    # Precisa de mais que 105K tokens = 420K chars
    chars_per_msg = 10_000  # cada mensagem tem 10K chars = 2500 tokens
    # 50 mensagens × 2500 tokens = 125K tokens > 105K → deve comprimir
    _fill_context(ctx, 50, chars_per_msg)
    # Força a auto_summarize manualmente (pois add_user já chama)
    ctx._auto_summarize()

    # Deve ter reduzido o número de mensagens (comprimiu as antigas)
    has_summary = any("[Resumo" in m.get("content", "") for m in ctx.messages)
    assert has_summary, "Esperava uma mensagem de resumo após auto_summarize"


def test_auto_summarize_preserves_last_4_messages():
    ctx = ContextManager(applied_context=200_000)
    chars_per_msg = 10_000
    _fill_context(ctx, 40, chars_per_msg)
    original_tail = ctx.messages[-4:]
    ctx._auto_summarize()
    current_tail = ctx.messages[-4:]
    assert current_tail == original_tail


def test_auto_summarize_skips_if_few_messages():
    """Com 4 ou menos mensagens não deve comprimir mesmo acima do threshold."""
    ctx = ContextManager(applied_context=200_000)
    # 4 mensagens, cada uma muito grande (forçando ultra-acima do threshold)
    ctx.messages = [
        {"role": "user", "content": "x" * 30_000},
        {"role": "assistant", "content": "y" * 30_000},
        {"role": "user", "content": "z" * 30_000},
        {"role": "assistant", "content": "w" * 30_000},
    ]
    ctx._auto_summarize()
    # Com ≤4 mensagens, deve manter como está (early return)
    assert len(ctx.messages) == 4
    assert not any("[Resumo" in m.get("content", "") for m in ctx.messages)


# ─── get_payload com system_prompt ───────────────────────────────────────────


def test_get_payload_system_prompt_always_first():
    ctx = ContextManager(applied_context=4096, system_prompt="SYSTEM")
    ctx.add_user("user1")
    ctx.add_assistant("assistant1")
    ctx.add_user("user2")
    payload = ctx.get_payload()
    assert payload[0]["role"] == "system"
    assert payload[0]["content"] == "SYSTEM"
    assert len(payload) == 4  # system + 3 msgs


def test_get_payload_returns_copy_of_messages():
    ctx = ContextManager(applied_context=4096)
    ctx.add_user("test")
    payload = ctx.get_payload()
    payload.append({"role": "injected", "content": "hack"})
    # Modificar payload não deve afetar ctx.messages
    assert len(ctx.messages) == 1


def test_used_tokens_grows_with_messages():
    ctx = ContextManager(applied_context=4096)
    assert ctx.used_tokens == 0
    ctx.messages.append({"role": "user", "content": "abcd"})  # 1 token
    assert ctx.used_tokens == 1
    ctx.messages.append({"role": "assistant", "content": "efgh"})  # +1 token
    assert ctx.used_tokens == 2
