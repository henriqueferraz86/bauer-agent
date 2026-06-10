"""E2E smoke com provider REAL — o teste que os 3.000 mocks não substituem.

Roda contra o opencode free tier (sem API key). Desabilitado por padrão;
ativa com a env var BAUER_E2E_REAL=1::

    BAUER_E2E_REAL=1 pytest tests/test_e2e_smoke_real.py -v

Por que existe: em 2026-06-10, 8 bugs de wiring chegaram ao usuário com a
suite inteira verde — todos invisíveis para testes com mock (think field
ignorado, contexto cloud travado em 4096, compressão inalcançável...).
Este smoke exercita o caminho de produção: client real → payload real →
resposta real.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("BAUER_E2E_REAL") != "1",
    reason="E2E real desabilitado (set BAUER_E2E_REAL=1 para rodar)",
)

OPENCODE_HOST = "https://opencode.ai/zen"
OPENCODE_MODEL = "deepseek-v4-flash-free"


def _make_client():
    from bauer.openai_client import OpenAIClient
    return OpenAIClient(
        host=OPENCODE_HOST,
        api_key="public",
        timeout_seconds=60,
    )


class TestSmokeOpencode:
    def test_chat_simples_responde(self):
        client = _make_client()
        parts = list(client.chat_stream(
            OPENCODE_MODEL,
            [{"role": "user", "content": "Responda apenas: OK"}],
        ))
        response = "".join(parts)
        assert response.strip(), "provider real retornou resposta vazia"

    def test_multi_turn_nao_retorna_vazio(self):
        """Regressão do bug gemma4: 2º turno retornava vazio."""
        client = _make_client()
        messages = [{"role": "user", "content": "Diga 'um'"}]
        r1 = "".join(client.chat_stream(OPENCODE_MODEL, messages))
        assert r1.strip()
        messages += [
            {"role": "assistant", "content": r1},
            {"role": "user", "content": "Agora diga 'dois'"},
        ]
        r2 = "".join(client.chat_stream(OPENCODE_MODEL, messages))
        assert r2.strip(), "segundo turno retornou vazio (bug classe gemma4)"

    def test_run_one_turn_com_tool_bridge(self, tmp_path):
        """Caminho completo: ContextManager + ToolRouter + bridge JSON."""
        from bauer.agent import run_one_turn
        from bauer.context_manager import ContextManager
        from bauer.tool_router import ToolRouter

        client = _make_client()
        ctx = ContextManager(applied_context=65536, provider="opencode")
        ctx.add_user("Quanto é 6 x 7? Responda só o número.")
        router = ToolRouter(workspace=tmp_path)

        response, tool_log = run_one_turn(ctx, router, client, OPENCODE_MODEL)
        assert response.strip()
        assert "42" in response

    def test_compressao_forcada_e_retry(self):
        """force_compress num histórico real seguido de chamada real."""
        from bauer.context_manager import ContextManager

        client = _make_client()
        ctx = ContextManager(applied_context=65536, provider="opencode")
        # Enche o histórico com lixo comprimível
        for i in range(30):
            ctx.messages.append({"role": "user", "content": f"dado {i}: " + "x" * 500})
            ctx.messages.append({"role": "assistant", "content": f"anotado {i}"})
        before = ctx.used_tokens
        compressed = ctx.force_compress()
        assert compressed
        assert ctx.used_tokens < before
        # Pós-compressão o modelo ainda responde
        ctx.add_user("Responda apenas: FIM")
        parts = list(client.chat_stream(OPENCODE_MODEL, ctx.get_payload()))
        assert "".join(parts).strip()
