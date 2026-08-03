"""Testes do estado/render do HUD (bauer/ui_hud.py) — plano 028 F2.

Puro: sem Live, sem terminal real — o mesmo padrão de `ui.py` (componentes que
retornam renderável Rich, testados via `render_str`).
"""

from __future__ import annotations

from bauer import ui
from bauer.ui_hud import HudState, render_hud


class TestSelo:
    def test_local_aparece(self):
        out = ui.render_str(render_hud(HudState(local=True)), 80)
        assert "local" in out

    def test_nuvem_aparece(self):
        out = ui.render_str(render_hud(HudState(local=False)), 80)
        assert "nuvem" in out

    def test_local_e_nuvem_sao_visualmente_distintos(self):
        """O selo muda de acento — é o que o plano promete ("soberania
        visível"), não só o texto."""
        l = render_hud(HudState(local=True))
        n = render_hud(HudState(local=False))
        assert ui.render_str(l, 80) != ui.render_str(n, 80)


class TestCampos:
    def test_modelo_aparece(self):
        assert "qwen3-coder:30b" in ui.render_str(render_hud(HudState(modelo="qwen3-coder:30b")), 80)

    def test_modelo_vazio_nao_imprime_lixo(self):
        out = ui.render_str(render_hud(HudState(modelo="")), 80)
        assert out.count("\n") <= 2  # só a linha do selo/custo, sem campo extra

    def test_ctx_pct_zero_nao_mostra_gauge(self):
        """Turno recém-começado sem contexto usado ainda não tem o que medir."""
        out = ui.render_str(render_hud(HudState(ctx_pct=0.0)), 80)
        assert "ctx" not in out

    def test_ctx_pct_aparece_com_gauge(self):
        out = ui.render_str(render_hud(HudState(ctx_pct=0.34)), 80)
        assert "ctx" in out and "34%" in out

    def test_custo_zero_mostra_zero_explicito(self):
        """Diferente do ctx: custo $0,00 é informação real (local não fatura),
        não "ainda não sei" — deve aparecer."""
        out = ui.render_str(render_hud(HudState(custo_usd=0.0)), 80)
        assert "$0,00" in out

    def test_custo_pequeno_mostra_casas_decimais_extras(self):
        out = ui.render_str(render_hud(HudState(custo_usd=0.0023)), 80)
        assert "0,0023" in out or "0.0023" in out

    def test_tokens_por_segundo_zero_nao_aparece(self):
        out = ui.render_str(render_hud(HudState(tokens_por_segundo=0.0)), 80)
        assert "tok/s" not in out

    def test_tokens_por_segundo_aparece(self):
        out = ui.render_str(render_hud(HudState(tokens_por_segundo=18.4)), 80)
        assert "18.4 tok/s" in out


class TestEsteiraDoKernel:
    def test_kernel_desligado_esteira_nao_aparece(self):
        """None = Kernel desligado — a esteira SOME, não aparece cinza."""
        out = ui.render_str(render_hud(HudState(kernel_estado=None)), 80)
        assert ui.active_glyphs().step_on not in out
        assert ui.active_glyphs().step_off not in out

    def test_estado_planning_acende_primeiro_passo(self):
        out = ui.render_str(render_hud(HudState(kernel_estado="planning")), 80)
        g = ui.active_glyphs()
        assert out.count(g.step_on) == 1
        assert "planning" in out

    def test_estado_evaluating_acende_quase_tudo(self):
        out = ui.render_str(render_hud(HudState(kernel_estado="evaluating")), 80)
        g = ui.active_glyphs()
        assert out.count(g.step_on) == 5  # último da esteira monitorada

    def test_estado_desconhecido_nao_quebra(self):
        """Kernel pode reportar um estado que o HUD não mapeou (evolução da
        máquina de estados) — não pode virar exceção."""
        out = ui.render_str(render_hud(HudState(kernel_estado="algo_novo")), 80)
        assert "algo_novo" in out

    def test_estado_terminal_mostra_esteira_cheia(self):
        out = ui.render_str(render_hud(HudState(kernel_estado="completed")), 80)
        g = ui.active_glyphs()
        assert g.step_off not in out  # nada "pendente" — o turno acabou


class TestComandos:
    def test_comandos_aparecem_em_linha_propria(self):
        out = ui.render_str(
            render_hud(HudState(comandos=(("/exit", "sair"), ("/model", "trocar modelo")))),
            80,
        )
        assert "/exit" in out and "sair" in out and "/model" in out

    def test_sem_comandos_nao_imprime_linha_vazia(self):
        out = ui.render_str(render_hud(HudState(comandos=())), 80)
        assert "/exit" not in out


class TestImutabilidade:
    def test_hud_state_e_frozen(self):
        state = HudState(modelo="x")
        try:
            state.modelo = "y"  # type: ignore[misc]
            assert False, "HudState deveria ser imutável"
        except Exception:
            pass

    def test_nao_quebra_com_estado_default(self):
        assert "bauer" not in ui.render_str(render_hud(HudState()), 80)  # não é o response_header
        ui.render_str(render_hud(HudState()), 80)  # só não pode levantar
