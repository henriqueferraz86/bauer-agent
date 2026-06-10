"""Testes de regressão nomeados — bugs reais de 2026-06-10.

Princípio: todo bug que chega ao usuário vira um teste nomeado ANTES do fix
ser considerado completo. Cada teste aqui referencia o sintoma observado.
"""

from __future__ import annotations

import pytest


class TestThinkFieldIgnorado:
    """Bug: `think: false` no config.yaml era silenciosamente ignorado.

    Sintoma: gemma4:12b retornava resposta vazia em multi-turn porque o
    Ollama ativava thinking mode e devolvia tudo no campo `thinking`.
    """

    def test_model_section_aceita_think(self):
        from bauer.config_loader import ModelSection
        m = ModelSection(name="x", requested_context=4096, think=False)
        assert m.think is False

    def test_think_default_none(self):
        from bauer.config_loader import ModelSection
        m = ModelSection(name="x", requested_context=4096)
        assert m.think is None

    def test_campo_desconhecido_e_erro_nao_silencio(self):
        """A causa-raiz: Pydantic aceitava campo desconhecido sem reclamar."""
        from pydantic import ValidationError
        from bauer.config_loader import ModelSection
        with pytest.raises(ValidationError):
            ModelSection(name="x", requested_context=4096, thnik=False)

    def test_ollama_client_tem_atributo_think(self):
        from bauer.ollama_client import OllamaClient
        c = OllamaClient()
        assert hasattr(c, "think")
        assert c.think is None  # None → False no request


class TestContextoCloudTravado:
    """Bug: provider cloud preso em requested_context=4096 (limite de RAM
    do Ollama aplicado indevidamente a providers sem restrição local)."""

    def test_cloud_usa_max_de_requested_e_default(self):
        from bauer.provider_profile import get_default_context
        # opencode default 65536: config com 4096 deve subir para 65536
        assert max(4096, get_default_context("opencode")) == 65536

    def test_fonte_unica_sem_divergencia(self):
        """Bug derivado: preflight dizia 65536, context_manager dizia 128000."""
        from bauer.context_manager import PROVIDER_CONTEXT_WINDOWS
        from bauer.preflight import _CLOUD_CONTEXT_DEFAULTS
        from bauer.provider_profile import default_context_map
        canonical = default_context_map()
        for provider, value in PROVIDER_CONTEXT_WINDOWS.items():
            assert canonical.get(provider) == value, f"divergência em {provider}"
        for provider, value in _CLOUD_CONTEXT_DEFAULTS.items():
            assert canonical.get(provider) == value, f"divergência em {provider}"

    def test_provider_desconhecido_tem_fallback(self):
        from bauer.provider_profile import get_default_context
        assert get_default_context("provider_inexistente") == 32768


@pytest.fixture(autouse=True)
def _sem_llm_de_compressao(monkeypatch):
    """Compressão nestes testes deve usar o caminho rule-based (rápido e
    determinístico) — sem chamadas de rede ao auxiliary client."""
    import bauer.auxiliary_client as aux
    monkeypatch.setattr(aux, "get_compression_client", lambda: (None, None))


class TestCompressaoInalcancavel:
    """Bug: TAIL_BUDGET_TOKENS=8192 fixo > budget inteiro (3072 com ctx 4096)
    → to_compress sempre vazio → compressão jamais disparava."""

    def test_tail_dinamico_menor_que_budget(self):
        from bauer.context_manager import ContextManager
        ctx = ContextManager(applied_context=4096)
        # budget = 3072; tail deve ser no máx 1/3 disso, nunca 8192
        assert ctx._tail_budget <= ctx.budget // 3
        assert ctx._tail_budget >= 512

    def test_tail_grande_em_contexto_grande(self):
        from bauer.context_manager import ContextManager, TAIL_BUDGET_TOKENS
        ctx = ContextManager(applied_context=131072)
        assert ctx._tail_budget == TAIL_BUDGET_TOKENS  # constante preservada

    def test_compressao_dispara_em_contexto_pequeno(self):
        """Com contexto 4096, encher o histórico TEM que comprimir algo."""
        from bauer.context_manager import ContextManager
        ctx = ContextManager(applied_context=4096)
        for i in range(40):
            ctx.messages.append({"role": "user", "content": f"msg {i}: " + "x" * 400})
            ctx.messages.append({"role": "assistant", "content": "ok " + "y" * 200})
        before_count = len(ctx.messages)
        ctx.add_user("nova mensagem")
        # _auto_summarize deve ter comprimido (mensagens viram 1 resumo + tail)
        assert len(ctx.messages) < before_count

    def test_force_compress_funciona(self):
        from bauer.context_manager import ContextManager
        ctx = ContextManager(applied_context=8192)
        for i in range(30):
            ctx.messages.append({"role": "user", "content": "z" * 500})
        tokens_before = ctx.used_tokens
        assert ctx.force_compress() is True
        assert ctx.used_tokens < tokens_before

    def test_force_compress_vazio_retorna_false(self):
        from bauer.context_manager import ContextManager
        ctx = ContextManager(applied_context=65536)
        ctx.add_user("oi")
        assert ctx.force_compress() is False


class TestModelfileParamsDuplicado:
    """Bug: openai_client tinha cópia local de ModelfileParams sem os campos
    novos — show_model() explodia com TypeError."""

    def test_show_model_nao_explode(self):
        from bauer.openai_client import OpenAIClient
        params = OpenAIClient().show_model("gpt-4o")
        assert params.num_ctx is None
        assert params.context_length is None

    def test_classe_unica(self):
        from bauer.ollama_client import ModelfileParams as A
        from bauer.openai_client import ModelfileParams as B
        assert A is B
