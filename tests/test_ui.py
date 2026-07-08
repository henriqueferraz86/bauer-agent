"""Testes do Bauer UI kit (bauer/ui.py) — tema Minimal."""

from __future__ import annotations

from bauer import ui


class TestResponseHeader:
    def test_contem_bauer_e_barra_de_acento(self):
        out = ui.render_str(ui.response_header(), 60)
        assert "bauer" in out
        assert ui.GLYPH_BOT in out  # ▏ barra de acento

    def test_meta_aparece(self):
        out = ui.render_str(ui.response_header("qwen", "$0.002", "1.8s"), 60)
        assert "qwen" in out and "$0.002" in out and "1.8s" in out

    def test_sem_meta_nao_quebra(self):
        assert "bauer" in ui.render_str(ui.response_header(), 60)

    def test_minimal_sem_regua_gradiente(self):
        # Tema Minimal não desenha régua de gradiente (isso era o Tema Rail).
        assert "━" not in ui.render_str(ui.response_header("m"), 60)


class TestToolLine:
    def test_status_primeiro_em_colchetes(self):
        out = ui.render_str(ui.tool_line("read_file", "x.py", status="ok", elapsed_ms=90), 60)
        assert out.strip().startswith("[")
        assert ui.GLYPH_OK in out and "read_file" in out and "90ms" in out

    def test_fail_tem_x(self):
        out = ui.render_str(ui.tool_line("run_command", status="fail", elapsed_ms=12400), 60)
        assert ui.GLYPH_FAIL in out and "12.4s" in out

    def test_run_status_neutro(self):
        out = ui.render_str(ui.tool_line("x", status="run"), 60)
        assert ui.GLYPH_OK not in out and ui.GLYPH_FAIL not in out

    def test_nome_alinhado_em_coluna(self):
        # nome curto é preenchido até a largura da coluna (visual de tabela)
        out = ui.render_str(ui.tool_line("ls", "dir", status="ok"), 60)
        assert "ls" in out and "dir" in out
        assert "ls" + " " in out  # padding aplicado

    def test_arg_summary_opcional(self):
        assert "args" in ui.render_str(ui.tool_line("x", "args"), 60)

    def test_elapsed_formatado(self):
        assert ui._fmt_elapsed(90) == "90ms"
        assert ui._fmt_elapsed(1500) == "1.5s"
        assert ui._fmt_elapsed(None) == ""
        assert ui._fmt_elapsed(-1) == ""


class TestSkillLine:
    def test_skill_line(self):
        out = ui.render_str(ui.skill_line("Security Review", 80), 60)
        assert "Security Review" in out and "80%" in out and ui.GLYPH_SKILL in out


class TestContextGauge:
    def test_pct_e_barra(self):
        out = ui.render_str(ui.context_gauge(0.3, width=10), 40)
        assert "30%" in out and "▰" in out and "▱" in out

    def test_zero_e_cheio(self):
        assert "0%" in ui.render_str(ui.context_gauge(0.0, 10), 40)
        cheio = ui.render_str(ui.context_gauge(1.0, 10), 40)
        assert "100%" in cheio and "▱" not in cheio

    def test_clamp(self):
        assert "100%" in ui.render_str(ui.context_gauge(5.0), 40)
        assert "0%" in ui.render_str(ui.context_gauge(-1.0), 40)

    def test_niveis(self):
        assert "63%" in ui.render_str(ui.context_gauge(0.63), 40)
        assert "92%" in ui.render_str(ui.context_gauge(0.92), 40)  # faixa de perigo


class TestGradientHelpersAindaExistem:
    # grad_color segue exposto (para quem quiser), mesmo o tema não usando.
    def test_grad_extremos(self):
        assert ui.grad_color(0.0) == ui.GRADIENT[0]
        assert ui.grad_color(1.0) == ui.GRADIENT[-1]
