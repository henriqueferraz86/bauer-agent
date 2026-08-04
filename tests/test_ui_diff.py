"""Testes do colorizador de diff e saída de tool (bauer/ui_diff.py) — plano 028 F3."""

from __future__ import annotations

from bauer import ui
from bauer.ui_diff import (
    limpar_ansi,
    parece_diff,
    render_diff,
    render_saida,
    separar_diff,
)

#: Saída REAL da tool `patch` (capturada rodando a tool, não inventada).
DIFF_REAL = """Arquivo 'exemplo.py' atualizado.
--- a/exemplo.py
+++ b/exemplo.py
@@ -1,3 +1,3 @@
 import os
 def token():
-    return os.environ["BAUER_TOKEN"]
+    return pool.acquire("bauer")"""


class TestSepararDiff:
    def test_separa_mensagem_do_diff(self):
        msg, diff = separar_diff(DIFF_REAL)
        assert msg == "Arquivo 'exemplo.py' atualizado."
        assert diff.startswith("--- a/exemplo.py")
        assert '+    return pool.acquire("bauer")' in diff

    def test_texto_sem_diff_volta_inteiro_como_mensagem(self):
        msg, diff = separar_diff("Arquivo criado com sucesso.")
        assert msg == "Arquivo criado com sucesso."
        assert diff == ""

    def test_diff_sem_cabecalho_de_arquivo(self):
        msg, diff = separar_diff("resumo\n@@ -1 +1 @@\n-a\n+b")
        assert msg == "resumo"
        assert diff.startswith("@@")


class TestPareceDiff:
    def test_reconhece_diff_real(self):
        assert parece_diff(DIFF_REAL) is True

    def test_texto_comum_nao_e_diff(self):
        assert parece_diff("Arquivo lido. 42 linhas.") is False

    def test_codigo_com_menos_nao_vira_diff(self):
        """Código que por acaso tem linha começando com '-' não é diff — sem
        o marcador @@ ou o cabeçalho ---, não conta."""
        assert parece_diff("resultado:\n-1\n-2\n") is False


class TestRenderDiff:
    def test_adicionadas_e_removidas_aparecem(self):
        _, diff = separar_diff(DIFF_REAL)
        out = ui.render_str(render_diff(diff), 100)
        assert 'pool.acquire("bauer")' in out
        assert 'os.environ["BAUER_TOKEN"]' in out

    def test_contexto_preservado(self):
        _, diff = separar_diff(DIFF_REAL)
        out = ui.render_str(render_diff(diff), 100)
        assert "def token():" in out

    def test_teto_de_linhas_e_anunciado(self):
        """Teto silencioso faria a tela mentir sobre o tamanho da mudança."""
        grande = "\n".join(f"+linha {i}" for i in range(100))
        out = ui.render_str(render_diff(grande, max_linhas=5), 100)
        assert "linha 4" in out
        assert "linha 90" not in out
        assert "+95 linha" in out

    def test_linha_gigante_e_truncada(self):
        """Linha minificada de 40k chars trava o render por wrap automático."""
        gigante = "+" + ("x" * 5000)
        out = ui.render_str(render_diff(gigante, largura_max=80), 200)
        assert "…" in out
        assert len(max(out.splitlines(), key=len)) < 200

    def test_diff_vazio_nao_quebra(self):
        ui.render_str(render_diff(""), 80)


class TestLimparAnsi:
    def test_remove_cor_de_subprocesso(self):
        """npm/docker colorem a saída; repassar cru deixa a tela refém do
        processo filho (cor que não fecha, cursor que anda)."""
        sujo = "\x1b[32mok\x1b[0m normal"
        assert limpar_ansi(sujo) == "ok normal"

    def test_remove_osc(self):
        assert limpar_ansi("\x1b]0;titulo\x07texto") == "texto"

    def test_carriage_return_isolado_some(self):
        """`\\r` sozinho é barra de progresso reposicionando o cursor —
        sobrescreveria o que já está na tela."""
        assert limpar_ansi("50%\r100%") == "50%100%"

    def test_crlf_vira_lf(self):
        assert limpar_ansi("a\r\nb") == "a\nb"

    def test_texto_limpo_passa_intacto(self):
        assert limpar_ansi("nada a limpar") == "nada a limpar"

    def test_diff_com_ansi_e_limpo_no_render(self):
        out = ui.render_str(render_diff("\x1b[31m-removido\x1b[0m"), 80)
        assert "removido" in out
        assert "\x1b[31m" not in out


class TestRenderSaida:
    def test_mostra_as_ultimas_linhas(self):
        """Numa build demorada o que importa é a linha mais recente."""
        saida = "\n".join(f"passo {i}" for i in range(10))
        out = ui.render_str(render_saida(saida, max_linhas=3), 80)
        assert "passo 9" in out
        assert "passo 0" not in out

    def test_ignora_linhas_vazias(self):
        out = ui.render_str(render_saida("a\n\n\n\nb", max_linhas=2), 80)
        assert "a" in out and "b" in out

    def test_saida_vazia_nao_quebra(self):
        ui.render_str(render_saida(""), 80)

    def test_ansi_limpo_na_saida(self):
        out = ui.render_str(render_saida("\x1b[32mbuild ok\x1b[0m"), 80)
        assert "build ok" in out
        assert "\x1b[" not in out
