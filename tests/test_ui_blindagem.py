"""Blindagem do design system (plano 028 F6).

Cada terminal desta matriz tem um modo de falha que ESTE repositório já viu:

  - **cmd legado (cp1252)**: `UnicodeEncodeError` no primeiro `❯`/`▰`. Aconteceu
    duas vezes durante o próprio plano 028 — no gerador de tokens e no script
    de preview.
  - **sem TTY** (pipe, CI, `bauer agent < arquivo`): sequência de controle de
    cursor vira lixo no meio do texto.
  - **`NO_COLOR`**: padrão de facto que usuários de terminal esperam.
  - **largura estreita**: painel que assume 100 colunas quebra em 60.

O outro teste que importa aqui é o de CUSTO DE RENDER: é a única defesa real
contra "ficou lindo e travou em resposta longa" — a regressão para
comportamento quadrático que o buffer por bloco do F1 existe para evitar.
"""

from __future__ import annotations

import io
import time

import pytest
from rich.console import Console

from bauer import theme, ui
from bauer.ui_boot import boot_panel, serve_panel
from bauer.ui_diff import render_diff, render_saida
from bauer.ui_hud import HudState, render_hud
from bauer.ui_stream import ConsoleStreamRenderer

# ── a matriz ────────────────────────────────────────────────────────────────

ESTADO_BOOT = {
    "configured_provider": "ollama", "active_model": "qwen3-coder:30b",
    "model_available": True, "context": {"requested": 131072, "applied": 32768},
    "ram_available_mb": 47104, "ram_total_mb": 62464, "tool_mode": "native",
    "notes": ["contexto reduzido: teto do modelfile"], "generated_at": "",
}

DIFF = ("--- a/auth.py\n+++ b/auth.py\n@@ -1,3 +1,3 @@\n def token():\n"
        '-    return os.environ["X"]\n+    return pool.acquire("bauer")')


def _componentes() -> "list[tuple[str, object]]":
    """Todo renderável público do kit. Um componente novo entra AQUI e passa a
    ser coberto pela matriz inteira de graça."""
    return [
        ("response_header", ui.response_header("modelo-x", "$0.01", "1.2s")),
        ("tool_line", ui.tool_line("read_file", "auth.py", status="ok", elapsed_ms=41)),
        ("tool_line_fail", ui.tool_line("run_command", "docker build", status="fail",
                                        elapsed_ms=12400)),
        ("tool_block_diff", ui.tool_block("patch", "auth.py", status="ok",
                                          elapsed_ms=88, result=DIFF)),
        # título com o GLIFO do conjunto ativo, como o agent.py monta — um `⚠`
        # literal aqui testaria o dado do teste, não o componente.
        ("approval_card", ui.approval_card(
            f"{ui.active_glyphs().warn} confirmar comando", "rm -rf /tmp/x")),
        ("approval_options", ui.approval_options()),
        ("accent_swatches", ui.accent_swatches(destaque="violeta")),
        ("skill_line", ui.skill_line("Security Review", 80)),
        ("context_gauge", ui.context_gauge(0.92)),
        ("hud_local", render_hud(HudState(local=True, modelo="qwen3-coder:30b",
                                          ctx_pct=0.34, custo_usd=0.0,
                                          tokens_por_segundo=13.6,
                                          kernel_estado="running"))),
        ("hud_nuvem", render_hud(HudState(local=False, modelo="gpt-x",
                                          custo_usd=1.2345))),
        ("boot_panel", boot_panel(ESTADO_BOOT, tool_mode="native",
                                  tools=["read_file", "patch"], local=True)),
        ("serve_panel", serve_panel("http://127.0.0.1:8000", cockpit=True, auth=False)),
        ("render_diff", render_diff(DIFF)),
        ("render_saida", render_saida("linha 1\nlinha 2\nlinha 3\nlinha 4")),
    ]


@pytest.fixture(autouse=True)
def _isola_estado_global():
    """Glifos e acento são estado de módulo; sem isto um teste da matriz
    contamina os outros e, com `-n auto`, o flake é intermitente."""
    glifos, acento = ui.active_glyphs(), theme.ACCENT_NAME
    yield
    ui.use_glyphs(glifos)
    theme.set_accent(acento)


class TestCmdLegadoCp1252:
    """O console do Windows sem UTF-8 não codifica ▰ ◆ ⟐ ❯ — e estourar no
    boot é pior que ficar feio."""

    @pytest.mark.parametrize("nome,_", [(n, None) for n, _ in _componentes()])
    def test_todo_componente_sai_em_ascii_puro(self, nome, _):
        ui.use_glyphs(theme.ASCII)
        # reconstrói DEPOIS de trocar os glifos: os componentes resolvem o
        # conjunto no momento do render
        alvo = dict(_componentes())[nome]
        saida = ui.render_str(alvo, 80)
        try:
            saida.encode("cp1252")
        except UnicodeEncodeError as exc:
            pytest.fail(f"{nome} emitiu caractere que o cmd legado não codifica: {exc}")

    def test_moldura_de_card_acompanha_os_glifos(self):
        """`box.ROUNDED` desenha ┌─┐│└┘, que o cp1252 não codifica. O card de
        aprovação é o que aparece antes de um comando perigoso — quebrar ali é
        o pior lugar possível. Este defeito era REAL e a matriz o encontrou."""
        from rich import box

        assert theme.box_style(theme.ASCII) is box.ASCII
        assert theme.box_style(theme.UNICODE) is box.ROUNDED

        ui.use_glyphs(theme.ASCII)
        saida = ui.render_str(ui.approval_card("aviso", "corpo"), 60)
        assert "┌" not in saida and "─" not in saida
        saida.encode("cp1252")

    def test_conteudo_arbitrario_nao_derruba_a_tela(self):
        """Camada distinta da anterior: os GLIFOS do kit caem para ASCII, mas o
        CONTEÚDO (nome de modelo, nota do doctor, saída de tool) é arbitrário e
        pode trazer qualquer caractere. A defesa aí não é o glifo — é o stream
        de saída aceitar substituição em vez de estourar."""
        buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="replace")
        con = Console(file=buf, force_terminal=False, width=80)
        ui.use_glyphs(theme.ASCII)
        con.print(ui.tool_line("read_file", "arquivo-日本語-😀.py", status="ok"))
        buf.flush()  # não pode levantar

    def test_deteccao_escolhe_ascii_sozinha(self):
        class _Stream:
            encoding = "cp1252"

        assert theme.glyphs({}, stream=_Stream()) is theme.ASCII

    def test_streaming_em_ascii(self):
        ui.use_glyphs(theme.ASCII)
        con = Console(file=io.StringIO(), force_terminal=False, width=80)
        r = ConsoleStreamRenderer(con)
        r.on_delta("resposta com acento: ação\n\n")
        r.close()
        con.file.getvalue().encode("cp1252", errors="strict")


class TestSemTTY:
    """Pipe, CI, redirecionamento para arquivo."""

    def test_streaming_nao_emite_sequencia_de_controle(self):
        con = Console(file=io.StringIO(), force_terminal=False, width=80)
        r = ConsoleStreamRenderer(con)
        for pedaco in ["Uma ", "resposta ", "qualquer.\n\n", "Segundo bloco."]:
            r.on_delta(pedaco)
        r.close()
        assert "\x1b[" not in con.file.getvalue()

    def test_modo_plain_ecoa_cru(self):
        con = Console(file=io.StringIO(), force_terminal=False, width=80)
        r = ConsoleStreamRenderer(con, plain=True)
        r.on_delta("texto puro")
        r.close()
        assert con.file.getvalue() == "texto puro"

    def test_make_streamer_desliga_sem_tty(self):
        from unittest.mock import patch

        from bauer.agent import _make_streamer

        with patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            assert _make_streamer(Console(file=io.StringIO())) is None


class TestNoColor:
    """`NO_COLOR` é padrão de facto — quem exporta espera texto limpo."""

    def test_no_color_desliga(self):
        assert theme.color_enabled({"NO_COLOR": "1"}) is False

    def test_bauer_ui_plain_desliga(self):
        assert theme.color_enabled({"BAUER_UI": "plain"}) is False

    @pytest.mark.parametrize("nome,_", [(n, None) for n, _ in _componentes()])
    def test_componente_sem_cor_nao_perde_conteudo(self, nome, _):
        """Com cor desligada o texto tem que continuar legível — informação não
        pode estar codificada SÓ na cor."""
        alvo = dict(_componentes())[nome]
        con = Console(file=io.StringIO(), width=80, color_system=None, force_terminal=False)
        con.print(alvo)
        saida = con.file.getvalue()
        assert saida.strip(), f"{nome} ficou vazio sem cor"
        assert "\x1b[" not in saida


class TestLarguraEstreita:
    @pytest.mark.parametrize("largura", [40, 60, 80])
    @pytest.mark.parametrize("nome,_", [(n, None) for n, _ in _componentes()])
    def test_componente_respeita_a_largura(self, nome, _, largura):
        alvo = dict(_componentes())[nome]
        saida = ui.render_str(alvo, largura)
        for linha in saida.splitlines():
            assert len(linha) <= largura, (
                f"{nome} passou de {largura} colunas: {len(linha)}"
            )


class TestDegradaSemTruecolor:
    """O tema assume truecolor. Onde ele não existe, degradar para algo que
    pareça INTENCIONAL — não para algo que pareça quebrado."""

    def test_logo_sem_truecolor_usa_cor_solida(self):
        """Uma rampa de 40 passos em 8 cores vira listra. Medido em uso real:
        o gradiente violeta (#61348f → #a855f7 → #cfa2fb, três roxos) saiu
        oliva + salmão na tela do usuário."""
        from bauer.ascii_intro import _logo_rows

        solido = _logo_rows("BAUER", gradiente=False)[0]
        cores = {str(s.style) for s in solido.spans}
        assert len(cores) == 1, f"deveria ser uma cor só, veio {cores}"
        assert cores == {theme.ACCENT}

    def test_logo_com_truecolor_mantem_a_rampa(self):
        from bauer.ascii_intro import _logo_rows

        grad = _logo_rows("BAUER", gradiente=True)[0]
        assert len({str(s.style) for s in grad.spans}) > 10

    def test_logo_solido_segue_o_acento(self):
        from bauer.ascii_intro import _logo_rows

        theme.set_accent("lima")
        cores = {str(s.style) for s in _logo_rows("BAUER", gradiente=False)[0].spans}
        assert cores == {theme.PALETAS["lima"]}

    def test_play_intro_decide_pelo_console(self):
        """A decisão sai do `color_system` do console real, não de env var
        lida à parte — é o mesmo valor que o Rich usa para quantizar."""
        import inspect

        from bauer import ascii_intro

        fonte = inspect.getsource(ascii_intro.play_intro)
        assert "color_system" in fonte and "truecolor" in fonte


class TestCustoDeRender:
    """A única defesa real contra 'ficou lindo e travou em resposta longa'.

    O F1 renderiza por BLOCO justamente para não reparsear o documento a cada
    token — comportamento quadrático. Se alguém trocar por 'reparseia tudo',
    este teste é quem grita.
    """

    @staticmethod
    def _tempo(n_chunks: int) -> float:
        con = Console(file=io.StringIO(), force_terminal=False, width=80)
        r = ConsoleStreamRenderer(con)
        t0 = time.perf_counter()
        for i in range(n_chunks):
            # parágrafos fecham de tempos em tempos, como num texto real
            r.on_delta(f"palavra{i} " + ("\n\n" if i % 25 == 24 else ""))
        r.close()
        return time.perf_counter() - t0

    def test_custo_cresce_linearmente(self):
        base = self._tempo(200)
        quadruplo = self._tempo(800)
        # 4x de entrada num render linear custa ~4x; quadrático custaria ~16x.
        # Teto em 8x: folga generosa para ruído de máquina, e ainda assim longe
        # do quadrático.
        assert quadruplo < max(base, 1e-4) * 8, (
            f"render parece quadrático: 200 chunks={base:.4f}s, "
            f"800 chunks={quadruplo:.4f}s"
        )

    def test_bloco_gigante_nao_trava(self):
        """Modelo que escreve 20 KB sem uma linha em branco existe. A válvula
        do F1 despeja o bloco em vez de re-renderizar o mundo a cada quadro."""
        con = Console(file=io.StringIO(), force_terminal=False, width=80)
        r = ConsoleStreamRenderer(con)
        t0 = time.perf_counter()
        for _ in range(400):
            r.on_delta("x" * 50)   # 20 KB, zero quebra de parágrafo
        r.close()
        assert (time.perf_counter() - t0) < 2.0

    def test_diff_gigante_tem_teto(self):
        enorme = "\n".join(f"+linha {i}" for i in range(5000))
        t0 = time.perf_counter()
        saida = ui.render_str(render_diff(enorme), 80)
        assert (time.perf_counter() - t0) < 1.0
        assert len(saida.splitlines()) < 40   # teto respeitado


class TestTodoAcentoSobreviveAMatriz:
    """A troca de acento não pode quebrar nenhum componente em nenhum modo."""

    @pytest.mark.parametrize("acento", ["violeta", "lima", "ouro", "teal", "fucsia"])
    def test_render_completo_por_acento(self, acento):
        theme.set_accent(acento)
        for nome, _ in _componentes():
            alvo = dict(_componentes())[nome]
            assert ui.render_str(alvo, 80).strip(), f"{nome} vazio no acento {acento}"

    def test_acento_e_ascii_juntos(self):
        """A combinação mais hostil: cmd legado + acento trocado."""
        theme.set_accent("lima")
        ui.use_glyphs(theme.ASCII)
        for nome, _ in _componentes():
            alvo = dict(_componentes())[nome]
            ui.render_str(alvo, 60).encode("cp1252")
