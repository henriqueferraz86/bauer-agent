"""Blocos de execução ricos e cronômetro real (plano 028 F3).

Duas coisas que este arquivo prova, e que o F3 existe para resolver:

1. O diff exibido é o que a tool APLICOU (vem pronto no resultado da `patch`),
   não uma recomputação que poderia divergir do arquivo real.
2. `elapsed_ms` é MEDIDO. O componente aceitava o parâmetro desde sempre, mas
   nenhum call site o passava — a duração exibida em qualquer mockup era
   ficção até aqui.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from bauer import ui

DIFF_REAL = """Arquivo 'exemplo.py' atualizado.
--- a/exemplo.py
+++ b/exemplo.py
@@ -1,3 +1,3 @@
 import os
 def token():
-    return os.environ["BAUER_TOKEN"]
+    return pool.acquire("bauer")"""


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


class TestToolBlock:
    def test_sem_diff_e_so_a_linha(self):
        """Tool comum não ganha corpo — o resumo do cabeçalho basta, e um
        bloco por tool call encheria a tela de ruído."""
        out = ui.render_str(
            ui.tool_block("read_file", "auth.py", status="ok", elapsed_ms=41,
                          result="conteudo do arquivo"),
            100,
        )
        assert "read_file" in out and "41ms" in out
        assert "\n" not in out.strip()  # uma linha só

    def test_com_diff_mostra_o_corpo(self):
        out = ui.render_str(
            ui.tool_block("patch", "exemplo.py", status="ok", elapsed_ms=88,
                          result=DIFF_REAL),
            100,
        )
        assert "patch" in out and "88ms" in out
        assert 'pool.acquire("bauer")' in out
        assert 'os.environ["BAUER_TOKEN"]' in out

    def test_resultado_vazio_nao_quebra(self):
        ui.render_str(ui.tool_block("x", status="ok", result=""), 80)

    def test_falha_mantem_glifo_de_erro(self):
        out = ui.render_str(
            ui.tool_block("run_command", "docker build", status="fail", elapsed_ms=12400),
            100,
        )
        assert ui.GLYPH_FAIL in out and "12.4s" in out

    def test_diff_exibido_e_o_que_a_tool_aplicou(self, ws: Path):
        """Fim a fim: roda a tool `patch` DE VERDADE, e o que aparece na tela
        sai do resultado dela — não de uma recomputação."""
        from bauer.tool_router import ToolRouter

        alvo = ws / "exemplo.py"
        alvo.write_text('import os\ntoken = os.environ["X"]\n', encoding="utf-8")

        router = ToolRouter(workspace=ws)
        resultado = router.execute_native_call("patch", {
            "path": "exemplo.py",
            "old_string": 'os.environ["X"]',
            "new_string": 'pool.acquire("bauer")',
        })

        out = ui.render_str(ui.tool_block("patch", "exemplo.py", status="ok",
                                          result=resultado), 100)
        # o que está na TELA bate com o que está no ARQUIVO
        assert 'pool.acquire("bauer")' in out
        assert 'pool.acquire("bauer")' in alvo.read_text(encoding="utf-8")


class TestCronometroReal:
    def test_native_mede_duracao_da_tool(self):
        """Antes do F3 nenhum call site passava elapsed_ms — a duração era
        sempre None e o componente exibia nada."""
        import json
        import time as _time

        from bauer.agent import _native_turn_interactive
        from bauer.tool_dedup import ToolCallDeduper

        client = MagicMock()
        client.chat_with_tools.return_value = {
            "content": "",
            "tool_calls": [{
                "id": "c1",
                "function": {"name": "read_file", "arguments": json.dumps({"path": "a.py"})},
            }],
        }
        router = MagicMock()
        router.get_tool_schemas.return_value = []

        def _lento(*a, **k):
            _time.sleep(0.05)
            return "conteudo"

        router.execute_native_call.side_effect = _lento

        con = Console(file=io.StringIO(), force_terminal=False, width=120)
        ctx = MagicMock()
        ctx.get_payload.return_value = []
        ctx.messages = []

        with patch("bauer.ui.tool_block", wraps=ui.tool_block) as mock_block:
            _native_turn_interactive(
                ctx, router, client, "m", con,
                cli_tool_log=[], deduper=ToolCallDeduper(), calls_left=10,
            )

        elapsed = mock_block.call_args.kwargs["elapsed_ms"]
        assert elapsed is not None
        assert elapsed >= 45, f"cronometro nao mediu a espera real: {elapsed}ms"

    def test_bridge_mede_duracao(self, ws: Path):
        from bauer.agent import _TurnState, _run_tool_loop_body
        from bauer.context_manager import ContextManager
        from bauer.performance_tracker import SessionStats
        from bauer.tool_router import ToolRouter

        responses = ['{"action": "list_dir", "args": {"path": "."}}', "pronto"]
        calls = {"n": 0}

        def _side_effect(*args, **kwargs):
            idx = min(calls["n"], len(responses) - 1)
            calls["n"] += 1
            return iter([responses[idx]])

        client = MagicMock()
        client.chat_stream.side_effect = _side_effect
        client.last_usage = {}

        ctx = ContextManager(applied_context=4096, system_prompt="S")
        router = ToolRouter(workspace=ws)
        stats = SessionStats(model="m", context_tokens=4096, machine_id="x", provider="")
        state = _TurnState(client=client, active_model="m", native_session_ok=False,
                           fb_idx=0, mem_turn_idx=0)

        with patch("bauer.ui.tool_block", wraps=ui.tool_block) as mock_block:
            _run_tool_loop_body(
                ctx=ctx, router=router, state=state,
                console=Console(file=io.StringIO(), force_terminal=False),
                fallback_clients=None, stats=stats, tool_timeout_s=5.0,
                session_store=None, session_id=None, active_workspace=str(ws),
                turn_input_text="liste", memprov=None,
            )

        assert mock_block.call_args.kwargs["elapsed_ms"] is not None

    def test_resultado_de_dedup_nao_inventa_duracao(self, ws: Path):
        """Sem execução não há o que cronometrar — exibir '0ms' mentiria sobre
        o que aconteceu (o resultado veio do cache, não do disco)."""
        from bauer.agent import _TurnState, _run_tool_loop_body
        from bauer.context_manager import ContextManager
        from bauer.performance_tracker import SessionStats
        from bauer.tool_router import ToolRouter

        # a MESMA action duas vezes: a segunda cai no dedup
        responses = [
            '{"action": "list_dir", "args": {"path": "."}}',
            '{"action": "list_dir", "args": {"path": "."}}',
            "pronto",
        ]
        calls = {"n": 0}

        def _side_effect(*args, **kwargs):
            idx = min(calls["n"], len(responses) - 1)
            calls["n"] += 1
            return iter([responses[idx]])

        client = MagicMock()
        client.chat_stream.side_effect = _side_effect
        client.last_usage = {}

        ctx = ContextManager(applied_context=4096, system_prompt="S")
        router = ToolRouter(workspace=ws)
        stats = SessionStats(model="m", context_tokens=4096, machine_id="x", provider="")
        state = _TurnState(client=client, active_model="m", native_session_ok=False,
                           fb_idx=0, mem_turn_idx=0)

        with patch("bauer.ui.tool_block", wraps=ui.tool_block) as mock_block:
            _run_tool_loop_body(
                ctx=ctx, router=router, state=state,
                console=Console(file=io.StringIO(), force_terminal=False),
                fallback_clients=None, stats=stats, tool_timeout_s=5.0,
                session_store=None, session_id=None, active_workspace=str(ws),
                turn_input_text="liste", memprov=None,
            )

        elapsados = [c.kwargs["elapsed_ms"] for c in mock_block.call_args_list]
        assert elapsados[0] is not None       # primeira: executou
        assert elapsados[-1] is None          # segunda: veio do dedup


class TestApprovalCard:
    def test_card_mostra_titulo_e_corpo(self):
        out = ui.render_str(ui.approval_card("⚠ confirmar comando", "rm -rf /"), 80)
        assert "confirmar comando" in out and "rm -rf" in out

    def test_opcoes_destacam_o_que_ensina(self):
        """`a` (sempre) é a opção que ENSINA o allowlist — é ela que faz o
        gate deixar de engessar, então recebe o acento."""
        out = ui.render_str(ui.approval_options(), 80)
        for tecla in ("e", "s", "a", "n"):
            assert tecla in out
        assert "sempre (aprende)" in out

    def test_card_nao_usa_cor_solta(self):
        """Regressão do F0: 'yellow' cru do Rich não existe no design.

        Inspeciona o CÓDIGO, não a fonte bruta — a docstring da própria função
        menciona 'yellow' ao explicar o que substituiu, e casar com ela
        testaria a redação do comentário em vez do comportamento.
        """
        import ast
        import inspect
        import textwrap

        func = ast.parse(textwrap.dedent(inspect.getsource(ui.approval_card))).body[0]
        corpo = func.body
        if (corpo and isinstance(corpo[0], ast.Expr)
                and isinstance(getattr(corpo[0], "value", None), ast.Constant)
                and isinstance(corpo[0].value.value, str)):
            corpo = corpo[1:]  # descarta a docstring

        for no in corpo:
            for filho in ast.walk(no):
                if isinstance(filho, ast.Constant) and isinstance(filho.value, str):
                    assert "yellow" not in filho.value.lower(), (
                        f"cor solta no código: {filho.value!r}"
                    )
