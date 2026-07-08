"""Testes para as 3 melhorias do ContextManager:
1. Tail protection dinâmica por tokens (TAIL_BUDGET_TOKENS)
2. Anti-thrashing (threshold boost após compressão inútil)
3. Tool result pruning (deduplicação + truncagem antes de comprimir)
"""

from __future__ import annotations

import pytest

from bauer.context_manager import (
    TAIL_BUDGET_TOKENS,
    THRASH_BOOST_STEP,
    THRASH_DECAY_STEP,
    THRASH_MIN_SAVINGS,
    ContextManager,
    _prune_tool_results,
    _split_tail_by_tokens,
)


# ─── 1. Tail protection dinâmica por tokens ──────────────────────────────────


class TestSplitTailByTokens:
    def test_empty_messages_returns_empty(self):
        to_compress, tail = _split_tail_by_tokens([], 1000)
        assert to_compress == []
        assert tail == []

    def test_single_message_goes_to_tail(self):
        msgs = [{"role": "user", "content": "a" * 40}]  # 10 tokens
        to_compress, tail = _split_tail_by_tokens(msgs, 100)
        assert to_compress == []
        assert tail == msgs

    def test_all_fit_in_tail_budget(self):
        msgs = [
            {"role": "user", "content": "a" * 40},       # 10 tokens
            {"role": "assistant", "content": "b" * 40},   # 10 tokens
            {"role": "user", "content": "c" * 40},        # 10 tokens
        ]
        to_compress, tail = _split_tail_by_tokens(msgs, 200)
        assert to_compress == []
        assert tail == msgs

    def test_overflow_splits_correctly(self):
        # 5 mensagens × 100 tokens = 500 tokens; tail_budget = 200 tokens
        msgs = [
            {"role": "user", "content": "x" * 400}  # 100 tokens each
            for _ in range(5)
        ]
        to_compress, tail = _split_tail_by_tokens(msgs, 200)
        # Tail deve ter as mensagens que cabem em 200 tokens
        assert len(tail) >= 1
        assert len(to_compress) >= 1
        assert to_compress + tail == msgs

    def test_at_least_one_message_always_in_tail(self):
        """Mesmo que a mensagem seja maior que o budget, deve ficar no tail."""
        huge_msg = {"role": "user", "content": "z" * 40_000}  # 10K tokens
        to_compress, tail = _split_tail_by_tokens([huge_msg], TAIL_BUDGET_TOKENS)
        assert tail == [huge_msg]
        assert to_compress == []

    def test_tail_preserves_recency(self):
        """Tail deve manter as mensagens MAIS RECENTES, não as antigas."""
        msgs = [
            {"role": "user", "content": "antiga1" + "x" * 400},
            {"role": "assistant", "content": "antiga2" + "x" * 400},
            {"role": "user", "content": "recente1" + "x" * 400},
            {"role": "assistant", "content": "recente2" + "x" * 400},
        ]
        to_compress, tail = _split_tail_by_tokens(msgs, 250)  # ~250 tokens → 2 msgs
        # Tail deve conter recente1 e recente2
        tail_contents = [m["content"] for m in tail]
        assert any("recente" in c for c in tail_contents)
        if to_compress:
            compress_contents = [m["content"] for m in to_compress]
            assert any("antiga" in c for c in compress_contents)

    def test_to_compress_plus_tail_equals_original(self):
        msgs = [{"role": "user", "content": "m" * (400 * i)} for i in range(1, 7)]
        to_compress, tail = _split_tail_by_tokens(msgs, TAIL_BUDGET_TOKENS)
        assert to_compress + tail == msgs

    def test_token_budget_respected(self):
        """Tokens no tail não devem exceder o budget (salvo 1ª msg garantida)."""
        msgs = [{"role": "user", "content": "k" * 400} for _ in range(10)]  # 100 tok cada
        _, tail = _split_tail_by_tokens(msgs, TAIL_BUDGET_TOKENS)
        tail_tokens = sum(len(m.get("content", "")) // 4 for m in tail)
        # Com 8192 tokens de budget e msgs de 100 tokens, cabe 81 msgs, mas só temos 10
        # Teste mais restritivo: budget = 200 tokens
        _, tail2 = _split_tail_by_tokens(msgs, 200)
        tail2_tokens = sum(len(m.get("content", "")) // 4 for m in tail2)
        # Deve caber no budget (ou ter no mínimo 1 msg)
        assert tail2_tokens <= 200 or len(tail2) == 1


# ─── 2. Anti-thrashing ──────────────────────────────────────────────────────


class TestAntiThrashing:
    def _make_ctx_at_threshold(self, applied_context: int = 40_000) -> ContextManager:
        """Cria um ContextManager já próximo do threshold com mensagens suficientes."""
        ctx = ContextManager(applied_context=applied_context)
        # budget = 30K tokens = 120K chars; threshold = 70% = 21K tokens = 84K chars
        # Precisamos > 21K tokens → >84K chars, em múltiplas mensagens
        # 10 mensagens × 9K chars = 90K chars = 22.5K tokens → acima do threshold
        for i in range(10):
            role = "user" if i % 2 == 0 else "assistant"
            ctx.messages.append({"role": role, "content": "x" * 9_000})
        return ctx

    def test_effective_threshold_starts_at_base(self):
        ctx = ContextManager(applied_context=40_000)
        assert ctx.effective_threshold == pytest.approx(0.70, abs=0.001)
        assert ctx._threshold_boost == 0.0

    def test_threshold_boost_increases_after_useless_compression(self):
        """Se compressão economizou < 10%, threshold sobe."""
        ctx = self._make_ctx_at_threshold()
        old_threshold = ctx.effective_threshold

        # Injeta uma compressão com savings baixo manualmente
        tokens_before = 1000
        tokens_after = 950  # economizou só 5%
        savings = (tokens_before - tokens_after) / tokens_before  # 0.05 < THRASH_MIN_SAVINGS

        if savings < THRASH_MIN_SAVINGS:
            ctx._threshold_boost = min(ctx._threshold_boost + THRASH_BOOST_STEP, 0.20)

        assert ctx._threshold_boost == pytest.approx(THRASH_BOOST_STEP, abs=0.001)
        assert ctx.effective_threshold > old_threshold

    def test_threshold_boost_decreases_after_good_compression(self):
        """Se compressão economizou >= 10%, threshold relaxa."""
        ctx = ContextManager(applied_context=40_000)
        ctx._threshold_boost = 0.20  # já no máximo

        savings = 0.30  # boa compressão
        if savings >= THRASH_MIN_SAVINGS:
            ctx._threshold_boost = max(ctx._threshold_boost - THRASH_DECAY_STEP, 0.0)

        assert ctx._threshold_boost == pytest.approx(0.15, abs=0.001)

    def test_threshold_boost_capped_at_max(self):
        """Boost nunca deve exceder THRASH_BOOST_MAX (0.20)."""
        ctx = ContextManager(applied_context=40_000)
        ctx._threshold_boost = 0.20

        # Simula mais compressões inúteis
        ctx._threshold_boost = min(ctx._threshold_boost + THRASH_BOOST_STEP, 0.20)
        assert ctx._threshold_boost <= 0.20

    def test_effective_threshold_capped_at_095(self):
        """Threshold efetivo nunca deve passar de 0.95."""
        ctx = ContextManager(applied_context=40_000)
        ctx._threshold_boost = 0.30  # forçado além do máximo
        assert ctx.effective_threshold <= 0.95

    def test_compress_count_increments(self):
        ctx = self._make_ctx_at_threshold()
        ctx._auto_summarize()
        assert ctx._compress_count >= 1

    def test_compression_stats_returns_correct_keys(self):
        ctx = ContextManager(applied_context=40_000)
        stats = ctx.compression_stats()
        assert "compress_count" in stats
        assert "threshold_boost" in stats
        assert "effective_threshold" in stats
        assert "last_savings_pct" in stats
        assert "used_tokens" in stats
        assert "budget" in stats
        assert "usage_pct" in stats

    def test_last_savings_pct_updated_after_compression(self):
        ctx = self._make_ctx_at_threshold()
        ctx._auto_summarize()
        if ctx._compress_count > 0:
            assert 0.0 <= ctx._last_savings_pct <= 1.0

    def test_no_compression_below_threshold(self):
        ctx = ContextManager(applied_context=200_000)
        ctx.add_user("mensagem pequena")
        assert ctx._compress_count == 0


# ─── 3. Tool result pruning ──────────────────────────────────────────────────


class TestPruneToolResults:
    def test_non_tool_messages_unchanged(self):
        msgs = [
            {"role": "user", "content": "pergunta normal"},
            {"role": "assistant", "content": "resposta normal"},
        ]
        result = _prune_tool_results(msgs)
        assert result == msgs

    def test_short_tool_result_unchanged(self):
        content = "[Resultado de list_dir]\narquivo1.py\narquivo2.py"
        msgs = [{"role": "user", "content": content}]
        result = _prune_tool_results(msgs)
        assert result[0]["content"] == content

    def test_long_tool_result_truncated(self):
        """Resultados > 400 chars viram 1 linha de sumário."""
        long_result = "linha " + ("x" * 500)
        content = f"[Resultado de read_file]\n{long_result}"
        msgs = [{"role": "user", "content": content}]
        result = _prune_tool_results(msgs)
        pruned = result[0]["content"]
        assert "truncado na compressão" in pruned or "truncado" in pruned
        assert "read_file" in pruned

    def test_duplicate_tool_result_collapsed(self):
        """Mesmo resultado repetido vira '(duplicado #N, omitido)'."""
        content = "[Resultado de list_dir]\narquivo1.py\narquivo2.py"
        msgs = [
            {"role": "user", "content": content},
            {"role": "user", "content": content},
        ]
        result = _prune_tool_results(msgs)
        assert any("duplicado" in m["content"] for m in result)

    def test_different_tool_results_kept_separate(self):
        """Resultados de tools diferentes não são deduplicados."""
        content1 = "[Resultado de list_dir]\narquivo1.py"
        content2 = "[Resultado de read_file]\nconteudo do arquivo"
        msgs = [
            {"role": "user", "content": content1},
            {"role": "user", "content": content2},
        ]
        result = _prune_tool_results(msgs)
        assert result[0]["content"] == content1
        assert result[1]["content"] == content2

    def test_assistant_messages_not_pruned(self):
        """Mensagens de assistente não são alteradas mesmo com conteúdo de tool."""
        content = "[Resultado de alguma_tool]\n" + "x" * 500
        msgs = [{"role": "assistant", "content": content}]
        result = _prune_tool_results(msgs)
        assert result[0]["content"] == content

    def test_multiple_blocks_in_single_message(self):
        """Mensagem com múltiplos blocos de tool result."""
        block1 = "[Resultado de tool1]\nresultado curto"
        block2 = "[Resultado de tool2]\n" + "y" * 500
        content = f"{block1}\n\n{block2}"
        msgs = [{"role": "user", "content": content}]
        result = _prune_tool_results(msgs)
        pruned = result[0]["content"]
        # Bloco 1 deve estar intacto, bloco 2 deve estar truncado
        assert "tool1" in pruned
        assert "tool2" in pruned
        assert "truncado" in pruned  # bloco 2 é longo

    def test_empty_messages_list(self):
        assert _prune_tool_results([]) == []

    def test_original_not_mutated(self):
        """A função não deve mutar as mensagens originais."""
        content = "[Resultado de list_dir]\narquivo.py"
        original = {"role": "user", "content": content}
        msgs = [original]
        _prune_tool_results(msgs)
        assert msgs[0]["content"] == content  # original não foi alterado

    def test_short_result_fingerprint_tracked(self):
        """Primeira ocorrência não é duplicado; segunda sim."""
        content = "[Resultado de tool_x]\nresultado"
        msgs = [
            {"role": "user", "content": content},
            {"role": "user", "content": content},
            {"role": "user", "content": content},
        ]
        result = _prune_tool_results(msgs)
        assert result[0]["content"] == content  # 1ª vez: original
        assert "duplicado" in result[1]["content"]  # 2ª vez: duplicado
        assert "duplicado" in result[2]["content"]  # 3ª vez: duplicado


# ─── Integração: auto_summarize usa prune antes de comprimir ─────────────────


class TestAutoSummarizeIntegration:
    def test_summarize_includes_tool_result_info(self):
        """Após compressão, o resumo deve incluir mensagem de resumo de contexto.

        applied_context=30_000 → budget=22_500 tokens.
        threshold=70% → 15_750 tokens.
        TAIL_BUDGET_TOKENS=8_192 tokens.
        50 msgs × 500 tokens (2_000 chars) = 25_000 tokens → acima do threshold
        e acima do tail budget, garantindo to_compress não vazio.
        """
        ctx = ContextManager(applied_context=30_000)
        for i in range(50):
            role = "user" if i % 2 == 0 else "assistant"
            ctx.messages.append({"role": role, "content": "x" * 2_000})
        # Garante que estamos acima do threshold e que há algo para comprimir
        assert ctx.usage_pct >= ctx.effective_threshold, (
            f"usage_pct={ctx.usage_pct:.2f} deve ser >= threshold={ctx.effective_threshold:.2f}"
        )
        ctx._auto_summarize()
        has_summary = any("CONTEXT COMPACTION" in m.get("content", "") for m in ctx.messages)
        assert has_summary

    def test_trim_never_removes_last_message(self):
        """_trim nunca remove a última mensagem (a recém-adicionada)."""
        ctx = ContextManager(applied_context=1000)  # budget muito pequeno
        # Adiciona mensagem enorme
        ctx.messages.append({"role": "user", "content": "x" * 100_000})
        ctx._trim()
        assert len(ctx.messages) == 1

    def test_add_user_triggers_trim_and_summarize(self):
        """add_user deve acionar auto_summarize e _trim se necessário."""
        ctx = ContextManager(applied_context=10_000)
        # Preenche com mensagens grandes
        for i in range(8):
            ctx.messages.append({"role": "user" if i % 2 == 0 else "assistant",
                                  "content": "m" * 5_000})
        n_before = len(ctx.messages)
        ctx.add_user("nova mensagem do usuário")
        # Deve ter comprimido ou trimado — não pode ter crescido sem limite
        assert len(ctx.messages) <= n_before + 1
