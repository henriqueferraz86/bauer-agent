"""Testes do ContextManager (Fase 2)."""

from __future__ import annotations

import pytest

from bauer.context_manager import ContextManager, _estimate_tokens, _strip_orphan_tool_messages


# --- _estimate_tokens -------------------------------------------------------


def test_estimate_tokens_empty():
    assert _estimate_tokens([]) == 0


def test_estimate_tokens_four_chars_one_token():
    msgs = [{"role": "user", "content": "abcd"}]
    assert _estimate_tokens(msgs) == 1


def test_estimate_tokens_accumulates():
    msgs = [
        {"role": "user", "content": "abcd"},       # 4 chars = 1
        {"role": "assistant", "content": "efgh"},  # 4 chars = 1
    ]
    assert _estimate_tokens(msgs) == 2


# --- budget -----------------------------------------------------------------


def test_budget_is_75_percent_of_context():
    ctx = ContextManager(applied_context=4096)
    assert ctx.budget == int(4096 * 0.75)


def test_budget_minimum_floor():
    # Contexto muito pequeno: floor em 512
    ctx = ContextManager(applied_context=100)
    assert ctx.budget == 512


# --- add_user / add_assistant -----------------------------------------------


def test_add_user_creates_message():
    ctx = ContextManager(applied_context=4096)
    ctx.add_user("hello")
    assert len(ctx.messages) == 1
    assert ctx.messages[0] == {"role": "user", "content": "hello"}


def test_add_assistant_creates_message():
    ctx = ContextManager(applied_context=4096)
    ctx.add_user("hello")
    ctx.add_assistant("world")
    assert len(ctx.messages) == 2
    assert ctx.messages[1] == {"role": "assistant", "content": "world"}


def test_used_tokens_reflects_messages():
    ctx = ContextManager(applied_context=4096)
    ctx.add_user("abcd")   # 4 chars = 1 token
    assert ctx.used_tokens == 1


# --- trim -------------------------------------------------------------------


def test_trim_keeps_last_message_even_if_over_budget():
    # budget = max(512, 75% de 512) = 512 tokens = 2048 chars
    ctx = ContextManager(applied_context=512)
    huge = "x" * 10_000  # muito acima do budget
    ctx.add_user(huge)
    # Única mensagem nunca é removida
    assert len(ctx.messages) == 1
    assert ctx.messages[0]["content"] == huge


def test_trim_removes_oldest_when_over_budget():
    ctx = ContextManager(applied_context=512)  # budget = 512 tokens = 2048 chars
    ctx.add_user("primeiro")
    ctx.add_assistant("resposta")
    # Nova mensagem enorme força o trim das anteriores
    ctx.add_user("x" * 10_000)
    assert not any(m["content"] == "primeiro" for m in ctx.messages)
    assert not any(m["content"] == "resposta" for m in ctx.messages)
    # A mensagem nova deve estar presente
    assert any("x" * 100 in m["content"] for m in ctx.messages)


def test_trim_removes_oldest_first():
    # budget = 75% de 4096 = 3072 tokens = 12288 chars
    ctx = ContextManager(applied_context=4096)
    ctx.add_user("a" * 5000)    # ~1250 tokens — cabe
    ctx.add_assistant("b" * 5000)  # ~1250 tokens — cabe (total ~2500)
    ctx.add_user("c" * 5000)    # ~1250 tokens — total ~3750 > 3072 → trim "a..."
    # "a..." deve ter sido removido
    assert not any("a" * 100 in m.get("content", "") for m in ctx.messages)
    # "c..." deve estar presente (é a mensagem mais recente)
    assert any("c" * 100 in m.get("content", "") for m in ctx.messages)


# --- get_payload ------------------------------------------------------------


def test_get_payload_no_system():
    ctx = ContextManager(applied_context=4096)
    ctx.add_user("hello")
    payload = ctx.get_payload()
    assert len(payload) == 1
    assert payload[0]["role"] == "user"


def test_get_payload_with_system_first():
    ctx = ContextManager(applied_context=4096, system_prompt="Voce e Bauer.")
    ctx.add_user("hello")
    payload = ctx.get_payload()
    assert payload[0] == {"role": "system", "content": "Voce e Bauer."}
    assert payload[1]["role"] == "user"


def test_get_payload_order_preserved():
    ctx = ContextManager(applied_context=4096)
    ctx.add_user("um")
    ctx.add_assistant("dois")
    ctx.add_user("tres")
    payload = ctx.get_payload()
    assert [m["content"] for m in payload] == ["um", "dois", "tres"]


# --- clear ------------------------------------------------------------------


def test_clear_empties_messages():
    ctx = ContextManager(applied_context=4096)
    ctx.add_user("hello")
    ctx.add_assistant("world")
    ctx.clear()
    assert ctx.messages == []
    assert ctx.used_tokens == 0


def test_clear_allows_new_messages_after():
    ctx = ContextManager(applied_context=4096)
    ctx.add_user("old")
    ctx.clear()
    ctx.add_user("new")
    assert len(ctx.messages) == 1
    assert ctx.messages[0]["content"] == "new"


# --- _strip_orphan_tool_messages ---------------------------------------------


def test_strip_tool_result_sem_assistant():
    """role:tool sem assistant:tool_calls correspondente é removido."""
    msgs = [
        {"role": "user", "content": "pergunta"},
        {"role": "tool", "tool_call_id": "call_abc", "content": "resultado"},
    ]
    clean = _strip_orphan_tool_messages(msgs)
    assert len(clean) == 1
    assert clean[0]["role"] == "user"


def test_strip_assistant_tool_calls_sem_resultado():
    """role:assistant com tool_calls mas sem nenhum result correspondente é removido."""
    msgs = [
        {"role": "user", "content": "pergunta"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "function": {}}]},
    ]
    clean = _strip_orphan_tool_messages(msgs)
    assert all(m.get("role") != "assistant" or not m.get("tool_calls") for m in clean)


def test_strip_par_valido_permanece():
    """Par assistant+tool_calls com resultado correspondente não é alterado."""
    msgs = [
        {"role": "user", "content": "pergunta"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "function": {}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "resultado"},
        {"role": "assistant", "content": "resposta final"},
    ]
    clean = _strip_orphan_tool_messages(msgs)
    assert len(clean) == 4


def test_strip_batch_parcial_filtra_calls_orfas():
    """Batch com 3 tool_calls mas só 2 respondidas: mantém as 2, descarta a órfã.

    Regressão: truncamento parcial de batch native deixava calls penduradas →
    provider rejeitava o próximo request (400 'tool_call_id sem resposta').
    """
    msgs = [
        {"role": "user", "content": "faz tudo"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "function": {"name": "x"}},
            {"id": "b", "function": {"name": "y"}},
            {"id": "c", "function": {"name": "z"}},  # sem resposta
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "content": "rb"},
    ]
    clean = _strip_orphan_tool_messages(msgs)
    asst = [m for m in clean if m["role"] == "assistant"][0]
    kept = [tc["id"] for tc in asst["tool_calls"]]
    assert kept == ["a", "b"], "deve filtrar a call órfã 'c'"
    # Toda tool restante tem assistant call correspondente
    tool_ids = {m["tool_call_id"] for m in clean if m["role"] == "tool"}
    assert tool_ids == {"a", "b"}


def test_get_payload_strip_automatico():
    """get_payload() remove tool results órfãos transparentemente (fix do 400)."""
    ctx = ContextManager(applied_context=4096)
    # Injeta estado quebrado diretamente em messages (simula sessão corrupta)
    ctx.messages = [
        {"role": "tool", "tool_call_id": "call_orfao", "content": "resultado perdido"},
        {"role": "user", "content": "nova pergunta"},
    ]
    payload = ctx.get_payload()
    assert not any(m.get("role") == "tool" for m in payload)
    assert any(m["content"] == "nova pergunta" for m in payload)


def test_trim_remove_par_atomicamente():
    """_trim não deixa role:tool órfão quando remove o assistant correspondente."""
    ctx = ContextManager(applied_context=512)
    # Adiciona par completo ao messages diretamente (sem passar pelo add_user/add_assistant
    # que aciona auto_summarize)
    ctx.messages = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "function": {}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "resultado"},
        {"role": "assistant", "content": "ok"},
    ]
    # Força trim adicionando mensagem enorme
    ctx.add_user("x" * 10_000)
    # Verifica que não há tool result órfão
    payload = ctx.get_payload()
    tool_ids = {m.get("tool_call_id") for m in payload if m.get("role") == "tool"}
    declared = set()
    for m in payload:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                if tc.get("id"):
                    declared.add(tc["id"])
    assert not (tool_ids - declared), f"tool results órfãos: {tool_ids - declared}"
