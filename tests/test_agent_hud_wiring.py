"""Ligação do quadro vivo / HUD no turno (plano 028 F2).

O F2 resolve o D3: o estado da sessão parava de existir no instante em que a
máquina começava a trabalhar (a `bottom_toolbar` do prompt_toolkit só vive
enquanto o prompt espera input). Estes testes cobrem a LIGAÇÃO — o que os
testes unitários de `ui_frame`/`ui_hud` não veem.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from rich.console import Console

from bauer import ui_frame
from bauer.agent import _quadro_do_turno
from bauer.ui_frame import TurnFrame
from bauer.ui_hud import HudState
from bauer.ui_stream import ConsoleStreamRenderer


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=100)


class TestQuadroDoTurno:
    def test_sem_tty_nao_abre_quadro(self):
        """Pipe/CI: sequência de controle de cursor no meio de um arquivo é
        lixo — o caminho antigo (impressão simples) continua valendo."""
        with patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            with _quadro_do_turno(_console()) as frame:
                assert frame is None

    def test_com_tty_abre_quadro(self):
        with patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            with _quadro_do_turno(_console()) as frame:
                assert frame is not None
                assert isinstance(frame, TurnFrame)

    def test_quadro_fecha_mesmo_com_excecao(self):
        """Turno que quebra no meio não pode deixar o Live pendurado — o
        terminal ficaria com a região viva órfã até o processo morrer."""
        with patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            try:
                with _quadro_do_turno(_console()):
                    raise RuntimeError("turno quebrou")
            except RuntimeError:
                pass
        assert ui_frame.current_frame() is None

    def test_frame_fica_acessivel_por_contextvar(self):
        """É assim que o spinner e o streaming descobrem o quadro sem receber
        o objeto por parâmetro em três níveis de chamada."""
        with patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            assert ui_frame.current_frame() is None
            with _quadro_do_turno(_console()) as frame:
                assert ui_frame.current_frame() is frame
            assert ui_frame.current_frame() is None


class TestSpinnerViraTrilho:
    def test_com_quadro_ativo_nao_abre_status(self):
        """Rich admite UM display ao vivo por vez: abrir console.status() com
        o quadro ativo falharia em silêncio e o usuário ficaria sem indicador
        nenhum. Com quadro, a atividade vira linha do trilho."""
        from bauer.agent import _busy_spinner

        con = MagicMock()
        frame = MagicMock()
        frame.is_live = True

        with patch("bauer.ui_frame.current_frame", return_value=frame):
            with _busy_spinner(con, "[dim]executando run_command…[/dim]"):
                pass

        con.status.assert_not_called()
        # atividade setada ao entrar e limpa ao sair
        assert frame.set_atividade.call_count == 2
        assert "run_command" in frame.set_atividade.call_args_list[0].args[0]
        assert frame.set_atividade.call_args_list[1].args[0] == ""

    def test_sem_quadro_mantem_spinner_classico(self):
        from bauer.agent import _busy_spinner

        con = _console()
        with patch("bauer.ui_frame.current_frame", return_value=None):
            with _busy_spinner(con, "[dim]executando algo…[/dim]"):
                pass  # não deve levantar

    def test_markup_nao_vaza_para_o_trilho(self):
        """O rótulo vem com markup Rich ([dim]…[/dim]); o trilho recebe Text
        já estilizado, então o markup cru precisa ser removido — senão o
        usuário lê '[dim]executando…' literalmente."""
        from bauer.agent import _busy_spinner

        frame = MagicMock()
        frame.is_live = True
        with patch("bauer.ui_frame.current_frame", return_value=frame):
            with _busy_spinner(MagicMock(), "[dim]executando read_file… (Ctrl+C interrompe)[/dim]"):
                pass
        rotulo = frame.set_atividade.call_args_list[0].args[0]
        assert "[dim]" not in rotulo
        assert rotulo.startswith("executando read_file")


class TestStreamingDentroDoQuadro:
    def test_resposta_vai_para_o_historico_nao_para_a_regiao_viva(self):
        """Bloco selado impresso na região viva seria APAGADO no refresh
        seguinte — a resposta do modelo sumiria da tela."""
        con = _console()
        frame = MagicMock()
        frame.is_live = True

        with patch("bauer.ui_frame.current_frame", return_value=frame):
            r = ConsoleStreamRenderer(con)
            r.on_delta("resposta selada\n\n")
            r.close()

        assert frame.print_above.called
        # renderiza o que foi enviado ao histórico e procura o texto de fato —
        # comparar str(renderable) testaria o repr do objeto Rich, não o
        # conteúdo que o usuário lê
        from bauer import ui

        impresso = "".join(
            ui.render_str(c.args[0], 100) if c.args and c.args[0] != "" else ""
            for c in frame.print_above.call_args_list
        )
        assert "resposta selada" in impresso
        # e NADA da resposta foi para a região viva (que seria apagada)
        for c in frame.set_rail.call_args_list:
            assert "resposta selada" not in ui.render_str(c.args[0][0], 100)

    def test_previa_vai_para_o_trilho(self):
        """O bloco ainda aberto é prévia: mora na região viva (some quando o
        bloco fecha), não no histórico."""
        con = _console()
        frame = MagicMock()
        frame.is_live = True

        with patch("bauer.ui_frame.current_frame", return_value=frame):
            r = ConsoleStreamRenderer(con)
            r.on_delta("texto parcial sem fechar")

        assert frame.set_rail.called

    def test_sem_quadro_usa_live_proprio(self):
        """Sem quadro (o caminho do F1), o renderer continua abrindo o próprio
        Live — o comportamento antigo não pode ter regredido."""
        con = _console()
        with patch("bauer.ui_frame.current_frame", return_value=None):
            r = ConsoleStreamRenderer(con)
            r.on_delta("bloco completo\n\n")
            r.close()
        assert "bloco completo" in con.file.getvalue()


class TestHudRecebeEstado:
    def test_hud_e_setado_ao_abrir_o_quadro(self):
        frame = TurnFrame(_console())
        with frame:
            frame.set_hud(HudState(modelo="qwen3-coder:30b", local=True))
            assert frame._hud is not None
            assert frame._hud.modelo == "qwen3-coder:30b"
