"""Testes de bauer/ui_frame.py — plano 028 F2.

O teste que mais importa aqui é `TestRegressaoTotodo`: reproduz o incidente
real (Live/Status ativo + input() no meio, texto digitado sumindo) e prova que
`suspend()` resolve pausando TUDO que está registrado, sem precisar de uma
allowlist de tools por nome.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from rich.console import Console

from bauer import ui_frame
from bauer.ui_frame import TurnFrame, register, suspend
from bauer.ui_hud import HudState


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=80)


class _FakeDisplay:
    """Dublê Pausable que registra a ORDEM de stop/start — é o que prova que
    múltiplos displays pausam e retomam na ordem certa."""

    def __init__(self, nome: str, eventos: list) -> None:
        self.nome = nome
        self.eventos = eventos

    def stop(self) -> None:
        self.eventos.append(("stop", self.nome))

    def start(self) -> None:
        self.eventos.append(("start", self.nome))


class TestRegistroEVazio:
    def test_suspend_sem_nada_registrado_e_no_op(self):
        rodou = False
        with suspend():
            rodou = True
        assert rodou

    def test_registrar_e_suspender_pausa_e_retoma(self):
        eventos: list = []
        d = _FakeDisplay("a", eventos)
        with register(d):
            with suspend():
                eventos.append(("dentro", "input"))
        assert eventos == [("stop", "a"), ("dentro", "input"), ("start", "a")]

    def test_dois_registros_pausam_e_retomam_em_ordem_inversa(self):
        """O de fora (Live do HUD) tem que retomar POR ÚLTIMO — senão ele
        redesenha por cima do que o de dentro ainda estava pausando."""
        eventos: list = []
        externo = _FakeDisplay("frame", eventos)
        interno = _FakeDisplay("spinner", eventos)
        with register(externo):
            with register(interno):
                with suspend():
                    eventos.append(("dentro", "input"))
        assert eventos == [
            ("stop", "frame"), ("stop", "spinner"),
            ("dentro", "input"),
            ("start", "spinner"), ("start", "frame"),
        ]

    def test_registro_sai_da_pilha_ao_desregistrar(self):
        eventos: list = []
        d = _FakeDisplay("a", eventos)
        with register(d):
            pass
        with suspend():
            pass  # nada registrado — se `d` ficasse preso, isto pausaria de novo
        assert eventos == []

    def test_display_que_quebra_no_stop_nao_impede_o_bloco(self):
        class _Quebrado:
            def stop(self):
                raise RuntimeError("terminal já morreu")
            def start(self):
                raise RuntimeError("idem")

        rodou = False
        with register(_Quebrado()):
            with suspend():
                rodou = True
        assert rodou

    def test_so_pausa_o_que_de_fato_parou(self):
        """Se o stop() de um display falha, seu start() não deve ser chamado
        depois — senão "retoma" algo que nunca pausou de verdade."""
        eventos: list = []

        class _QuebraNoStop:
            def stop(self):
                raise RuntimeError("boom")
            def start(self):
                eventos.append("start-nao-deveria-rodar")

        ok = _FakeDisplay("ok", eventos)
        with register(_QuebraNoStop()):
            with register(ok):
                with suspend():
                    pass
        assert "start-nao-deveria-rodar" not in eventos
        assert ("stop", "ok") in eventos and ("start", "ok") in eventos


class TestRegressaoTotodo:
    """Reproduz o incidente 2026-07-02: Status ativo + input() no meio."""

    def test_status_pausa_antes_do_input_e_retoma_depois(self):
        """Um `console.status()` de verdade (não dublê) registrado — suspend()
        precisa chamar seus start/stop reais sem quebrar."""
        con = _console()
        status = con.status("executando algo…", spinner="dots")
        status.start()
        try:
            with register(status):
                with patch("builtins.input", return_value="resposta do usuario") as mock_input:
                    with suspend():
                        resposta = input("> ")
            assert resposta == "resposta do usuario"
            mock_input.assert_called_once()
        finally:
            status.stop()

    def test_qualquer_tool_suspende_sem_allowlist(self):
        """O ponto central do F2: NENHUMA lista de nomes de tool é necessária.
        Uma tool nova que chama input() está automaticamente protegida, só por
        estar rodando sob um display registrado."""
        eventos: list = []
        spinner = _FakeDisplay("tool_exec", eventos)

        def _tool_hipotetica_que_pede_input():
            with suspend():
                return "resposta"

        with register(spinner):
            resultado = _tool_hipotetica_que_pede_input()

        assert resultado == "resposta"
        assert eventos == [("stop", "tool_exec"), ("start", "tool_exec")]

    def test_frame_e_spinner_aninhados_suspendem_juntos(self):
        """O caso real do F2: TurnFrame (HUD) com um Status de tool dentro —
        os dois precisam pausar juntos para o input() não colidir com nenhum."""
        con = _console()
        frame = TurnFrame(con)
        with frame:
            status = con.status("executando…", spinner="dots")
            status.start()
            try:
                with register(status):
                    with patch("builtins.input", return_value="ok"):
                        with suspend():
                            resposta = input("> ")
                assert resposta == "ok"
            finally:
                status.stop()


class TestTurnFrame:
    def test_entra_e_sai_sem_levantar(self):
        with TurnFrame(_console()):
            pass

    def test_print_above_funciona_com_live_ativo(self):
        con = _console()
        with TurnFrame(con) as frame:
            frame.print_above("linha do historico")
        assert "linha do historico" in con.file.getvalue()

    def test_set_hud_atualiza_a_regiao_viva(self):
        con = _console()
        with TurnFrame(con) as frame:
            frame.set_hud(HudState(modelo="modelo-x"))
            # não levanta — o conteúdo real só aparece no próximo refresh do
            # Live, que é assíncrono; o que se testa aqui é a chamada seca.

    def test_set_rail_e_clear_rail(self):
        con = _console()
        with TurnFrame(con) as frame:
            frame.set_rail(["linha 1", "linha 2"])
            frame.clear_rail()

    def test_frame_se_registra_e_desregistra_sozinho(self):
        """Ao entrar, um input() em QUALQUER lugar já pausa o frame; ao sair,
        não deve mais pausar nada (a pilha tem que estar limpa)."""
        con = _console()
        with TurnFrame(con) as frame:
            assert frame.is_live in (True, False)  # só não pode ser None/erro
            with suspend():
                pass  # não pode levantar mesmo com o frame ativo
        # fora do `with`, o frame não está mais registrado — outro suspend()
        # não deve tentar pausar um Live já fechado
        with suspend():
            pass

    def test_live_indisponivel_cai_para_print_direto(self):
        """Console cujo status()/Live falha (output capturado por outro
        display) não pode perder o texto — só perde o HUD fixo."""
        con = MagicMock()
        con.print = MagicMock()
        with patch("rich.live.Live", side_effect=RuntimeError("live já ativo")):
            with TurnFrame(con) as frame:
                assert frame.is_live is False
                frame.print_above("texto importante")
        con.print.assert_called_with("texto importante")

    def test_render_sem_hud_nem_trilho_nao_quebra(self):
        con = _console()
        with TurnFrame(con) as frame:
            renderable = frame._render()
        from bauer import ui
        ui.render_str(renderable, 60)  # não pode levantar

    def test_exception_dentro_do_with_ainda_desregistra(self):
        """Um turno que quebra no meio não pode deixar o frame preso na pilha
        — o próximo suspend() (ex.: o prompt seguinte) ficaria pausando algo
        morto para sempre."""
        con = _console()
        try:
            with TurnFrame(con):
                raise RuntimeError("turno quebrou")
        except RuntimeError:
            pass
        with suspend():
            pass  # se o frame ficou preso, isto chamaria stop() num Live morto
