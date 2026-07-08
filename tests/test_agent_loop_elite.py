"""Testes da Fase 2 — agent loop elite.

2.1 Native tool calling com downgrade automático para bridge
2.3 Syntax check pós write_file/patch
2.4 Reflection forçada a cada N tool calls
2.5 Compressão prefere auxiliary model
"""

from __future__ import annotations

import pytest


# ─── 2.1 Downgrade native → bridge ─────────────────────────────────────────────

class TestNativeDowngrade:
    def test_http_400_e_unsupported(self):
        from bauer.agent import _is_native_unsupported_error
        from bauer.openai_client import OpenAIClientError
        exc = OpenAIClientError("[Provedor] HTTP 400 em tool calling. Detalhe: tools not supported")
        assert _is_native_unsupported_error(exc) is True

    def test_http_404_e_422_sao_unsupported(self):
        from bauer.agent import _is_native_unsupported_error
        from bauer.openai_client import OpenAIClientError
        assert _is_native_unsupported_error(OpenAIClientError("HTTP 404 em tool calling")) is True
        assert _is_native_unsupported_error(OpenAIClientError("HTTP 422 em tool calling")) is True

    def test_http_429_NAO_e_unsupported(self):
        """429 é rate-limit — downgrade seria errado (provider suporta tools)."""
        from bauer.agent import _is_native_unsupported_error
        from bauer.openai_client import OpenAIClientError
        assert _is_native_unsupported_error(OpenAIClientError("HTTP 429 em tool calling")) is False

    def test_http_500_NAO_e_unsupported(self):
        from bauer.agent import _is_native_unsupported_error
        from bauer.openai_client import OpenAIClientError
        assert _is_native_unsupported_error(OpenAIClientError("HTTP 500 em tool calling")) is False

    def test_erro_sem_http_NAO_e_unsupported(self):
        from bauer.agent import _is_native_unsupported_error
        from bauer.openai_client import OpenAIClientError
        assert _is_native_unsupported_error(OpenAIClientError("Timeout (30s)")) is False

    def test_run_one_turn_faz_downgrade_para_bridge(self, tmp_path):
        """Provider OpenAI-compat que rejeita tools= deve cair no bridge e
        completar via JSON — não queimar o budget em retries native."""
        from bauer.agent import run_one_turn
        from bauer.context_manager import ContextManager
        from bauer.openai_client import OpenAIClient, OpenAIClientError
        from bauer.tool_router import ToolRouter

        class FakeNativeBroken(OpenAIClient):
            """chat_with_tools sempre 400; chat_stream responde texto simples."""
            def __init__(self):
                super().__init__(host="http://fake")
                self.native_calls = 0
                self.bridge_calls = 0

            def chat_with_tools(self, model, messages, tools, tool_choice="auto"):
                self.native_calls += 1
                raise OpenAIClientError("[Provedor] HTTP 400 em tool calling. Detalhe: no tools")

            def chat_stream(self, model, messages):
                self.bridge_calls += 1
                yield "Resposta final via bridge"

        client = FakeNativeBroken()
        ctx = ContextManager(applied_context=32768, provider="custom")
        ctx.add_user("oi")
        router = ToolRouter(workspace=tmp_path)

        response, tool_log = run_one_turn(ctx, router, client, "fake-model")

        assert "Resposta final via bridge" in response
        assert client.native_calls == 1, "deveria tentar native só 1 vez antes do downgrade"
        assert client.bridge_calls >= 1


# ─── 2.3 Syntax check pós-write ────────────────────────────────────────────────

class TestPostWriteSyntaxCheck:
    def _router(self, tmp_path):
        from bauer.tool_router import ToolRouter
        return ToolRouter(workspace=tmp_path)

    def test_python_invalido_gera_aviso(self, tmp_path):
        router = self._router(tmp_path)
        result = router.execute({
            "action": "write_file",
            "args": {"path": "broken.py", "content": "def f(:\n    pass"},
        })
        assert "syntax" in result.lower() or "sintaxe" in result.lower()

    def test_python_valido_sem_aviso(self, tmp_path):
        router = self._router(tmp_path)
        result = router.execute({
            "action": "write_file",
            "args": {"path": "ok.py", "content": "def f():\n    return 1\n"},
        })
        assert "erro" not in result.lower()

    def test_json_invalido_gera_aviso(self, tmp_path):
        router = self._router(tmp_path)
        result = router.execute({
            "action": "write_file",
            "args": {"path": "broken.json", "content": '{"a": 1,}'},
        })
        assert "syntax" in result.lower() or "sintaxe" in result.lower() or "json" in result.lower()

    def test_yaml_invalido_gera_aviso(self, tmp_path):
        router = self._router(tmp_path)
        result = router.execute({
            "action": "write_file",
            "args": {"path": "broken.yaml", "content": "a: [1, 2\nb: }{"},
        })
        assert "syntax" in result.lower() or "sintaxe" in result.lower() or "yaml" in result.lower()

    def test_arquivo_txt_nao_e_checado(self, tmp_path):
        router = self._router(tmp_path)
        result = router.execute({
            "action": "write_file",
            "args": {"path": "nota.txt", "content": "def f(: isso nao e python"},
        })
        assert "sintaxe" not in result.lower()


# ─── 2.4 Reflection forçada ────────────────────────────────────────────────────

class TestReflectionNudge:
    def test_nudge_apos_n_tool_calls(self, tmp_path):
        """Após _REFLECT_EVERY tool calls sem resposta final, o contexto deve
        receber um nudge de reflexão."""
        from bauer.agent import _REFLECT_EVERY, run_one_turn
        from bauer.context_manager import ContextManager
        from bauer.tool_router import ToolRouter

        class LoopyClient:
            """Pede calculate com expressões diferentes — resultados distintos
            a cada call (não dispara loop-detection nem dedup; isola o teste
            no mecanismo de reflexão)."""
            def __init__(self):
                self.calls = 0

            def chat_stream(self, model, messages):
                self.calls += 1
                yield (
                    '{"action": "calculate", "args": {"expression": "1+%d"}}'
                    % self.calls
                )

        ctx = ContextManager(applied_context=32768)
        ctx.add_user("calcule várias coisas")
        router = ToolRouter(workspace=tmp_path)

        run_one_turn(ctx, router, LoopyClient(), "fake")

        nudges = [
            m for m in ctx.messages
            if m["role"] == "user" and "reflexão" in str(m.get("content", "")).lower()
        ]
        assert nudges, f"esperava nudge de reflexão após {_REFLECT_EVERY} tool calls"


# ─── 2.5 Compressão aux-first ──────────────────────────────────────────────────

class TestCompressaoAuxFirst:
    def test_auxiliary_tem_prioridade_sobre_principal(self, monkeypatch):
        """Quando o auxiliary client está configurado, a compressão usa ele —
        não o modelo principal da sessão (que é caro/lento)."""
        from bauer.context_manager import ContextManager

        class MarkClient:
            def __init__(self, mark):
                self.mark = mark
                self.used = False

            def chat_stream(self, model, messages):
                self.used = True
                yield f"resumo via {self.mark}"

        principal = MarkClient("principal")
        auxiliar = MarkClient("auxiliar")

        import bauer.auxiliary_client as aux_mod
        monkeypatch.setattr(
            aux_mod, "get_compression_client", lambda: (auxiliar, "aux-model")
        )

        ctx = ContextManager(applied_context=8192)
        ctx.set_llm(principal, "main-model")
        for _ in range(30):
            ctx.messages.append({"role": "user", "content": "x" * 500})

        assert ctx.force_compress() is True
        assert auxiliar.used is True
        assert principal.used is False
        assert any("via auxiliar" in str(m.get("content", "")) for m in ctx.messages)

    def test_fallback_para_principal_sem_auxiliary(self, monkeypatch):
        from bauer.context_manager import ContextManager

        class MarkClient:
            def __init__(self):
                self.used = False

            def chat_stream(self, model, messages):
                self.used = True
                yield "resumo via principal"

        principal = MarkClient()
        import bauer.auxiliary_client as aux_mod
        monkeypatch.setattr(aux_mod, "get_compression_client", lambda: (None, None))

        ctx = ContextManager(applied_context=8192)
        ctx.set_llm(principal, "main-model")
        for _ in range(30):
            ctx.messages.append({"role": "user", "content": "y" * 500})

        assert ctx.force_compress() is True
        assert principal.used is True
