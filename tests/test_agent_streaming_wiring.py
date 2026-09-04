"""Ligação do streaming no turno interativo (plano 028 F1).

Os módulos isolados já têm teste. O que quebra em silêncio é a LIGAÇÃO: o
turno esquecer de passar `on_delta`, ou o fim do turno reimprimir o que já
apareceu ao vivo (resposta em dobro na tela).
"""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

from rich.console import Console

from bauer.agent import _ja_exibido, _make_streamer, _native_turn_interactive
from bauer.tool_dedup import ToolCallDeduper
from bauer.ui_stream import ConsoleStreamRenderer


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=100)


def _ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.get_payload.return_value = []
    ctx.messages = []
    return ctx


class TestTurnoNativoStreama:
    def test_json_de_tool_no_conteudo_nativo_e_executado(self):
        """Endpoints compatíveis podem devolver uma tool call como conteúdo.

        Isso precisa seguir pelo mesmo ToolRouter dos tool calls nativos para
        a resposta de voz só ser gerada depois do resultado real da ferramenta.
        """
        client = MagicMock()
        client.chat_with_tools.return_value = {
            "content": '{"command":"docker ps -q"}',
            "tool_calls": [],
        }
        router = MagicMock()
        router.get_tool_schemas.return_value = []
        router.available_tools.return_value = ["run_command"]
        router.execute_native_call.return_value = "abc123\n"

        kind, _ = _native_turn_interactive(
            _ctx(), router, client, "m", _console(),
            cli_tool_log=[], deduper=ToolCallDeduper(), calls_left=10,
        )

        assert kind == "continue"
        router.execute_native_call.assert_called_once_with(
            "run_command", {"command": "docker ps -q"}
        )

    def test_passa_on_delta_ao_client(self):
        client = MagicMock()
        client.chat_with_tools.return_value = {"content": "pronto", "tool_calls": []}
        router = MagicMock()
        router.get_tool_schemas.return_value = []
        con = _console()
        streamer = ConsoleStreamRenderer(con)

        kind, texto = _native_turn_interactive(
            _ctx(), router, client, "m", con,
            cli_tool_log=[], deduper=ToolCallDeduper(), calls_left=10,
            streamer=streamer,
        )

        assert kind == "final" and texto == "pronto"
        assert client.chat_with_tools.call_args.kwargs["on_delta"] == streamer.on_delta

    def test_sem_streamer_mantem_spinner(self):
        """Sem streaming o caminho antigo continua: spinner cobrindo a espera."""
        client = MagicMock()
        client.chat_with_tools.return_value = {"content": "ok", "tool_calls": []}
        router = MagicMock()
        router.get_tool_schemas.return_value = []
        con = MagicMock()

        _native_turn_interactive(
            _ctx(), router, client, "modelo-x", con,
            cli_tool_log=[], deduper=ToolCallDeduper(), calls_left=10,
        )

        assert con.status.called
        assert "on_delta" not in client.chat_with_tools.call_args.kwargs

    def test_com_streamer_nao_abre_spinner(self):
        """Spinner + streaming no mesmo console se atropelam (Rich admite um
        display ao vivo por vez) — foi o bug do 'totodo' com o clarify."""
        client = MagicMock()
        client.chat_with_tools.return_value = {"content": "ok", "tool_calls": []}
        router = MagicMock()
        router.get_tool_schemas.return_value = []
        con = MagicMock()

        _native_turn_interactive(
            _ctx(), router, client, "m", con,
            cli_tool_log=[], deduper=ToolCallDeduper(), calls_left=10,
            streamer=ConsoleStreamRenderer(_console()),
        )

        assert not con.status.called

    def test_client_antigo_sem_on_delta_ainda_funciona(self):
        """Client de terceiro que não conhece `on_delta` não pode derrubar o
        turno — repete sem streaming."""
        chamadas = {"n": 0}

        def _chat(*args, **kwargs):
            chamadas["n"] += 1
            if "on_delta" in kwargs:
                raise TypeError("chat_with_tools() got an unexpected keyword argument 'on_delta'")
            return {"content": "ok", "tool_calls": []}

        client = MagicMock()
        client.chat_with_tools.side_effect = _chat
        router = MagicMock()
        router.get_tool_schemas.return_value = []

        kind, texto = _native_turn_interactive(
            _ctx(), router, client, "m", _console(),
            cli_tool_log=[], deduper=ToolCallDeduper(), calls_left=10,
            streamer=ConsoleStreamRenderer(_console()),
        )

        assert (kind, texto) == ("final", "ok")
        assert chamadas["n"] == 2  # a primeira com on_delta, a segunda sem

    def test_type_error_do_provider_nao_vira_chamada_dupla(self):
        """Repetir a chamada por um TypeError qualquer cobraria o usuário duas
        vezes. Só o erro do parâmetro `on_delta` justifica a repetição."""
        client = MagicMock()
        client.chat_with_tools.side_effect = TypeError("erro interno do provider")
        router = MagicMock()
        router.get_tool_schemas.return_value = []

        try:
            _native_turn_interactive(
                _ctx(), router, client, "m", _console(),
                cli_tool_log=[], deduper=ToolCallDeduper(), calls_left=10,
                streamer=ConsoleStreamRenderer(_console()),
            )
        except TypeError:
            pass
        assert client.chat_with_tools.call_count == 1

    def test_tool_call_ainda_funciona_com_streaming(self):
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
        router.execute_native_call.return_value = "conteúdo"

        kind, _ = _native_turn_interactive(
            _ctx(), router, client, "m", _console(),
            cli_tool_log=[], deduper=ToolCallDeduper(), calls_left=10,
            streamer=ConsoleStreamRenderer(_console()),
        )
        assert kind == "continue"
        assert router.execute_native_call.called


class TestNaoImprimirDuasVezes:
    def test_json_de_tool_nao_aparece_durante_streaming(self):
        con = _console()
        s = ConsoleStreamRenderer(con)
        s.on_delta('{"command":"docker ps -q"}')
        s.close()

        assert '{"command"' not in con.file.getvalue()
        assert s.escreveu_algo is False

    def test_texto_ja_transmitido_nao_reimprime(self):
        con = _console()
        s = ConsoleStreamRenderer(con)
        s.on_delta("a resposta completa")
        s.close()
        assert _ja_exibido(s, "a resposta completa") is True

    def test_espaco_em_volta_nao_conta(self):
        con = _console()
        s = ConsoleStreamRenderer(con)
        s.on_delta("resposta\n\n")
        s.close()
        assert _ja_exibido(s, "  resposta  ") is True

    def test_texto_diferente_do_transmitido_imprime(self):
        """Gate de qualidade/replan pode trocar o texto final — aí a tela
        precisa mostrar o que valeu, não o que foi descartado."""
        con = _console()
        s = ConsoleStreamRenderer(con)
        s.on_delta("rascunho reprovado")
        s.close()
        assert _ja_exibido(s, "resposta corrigida") is False

    def test_sem_streamer_sempre_imprime(self):
        assert _ja_exibido(None, "qualquer coisa") is False

    def test_streamer_que_nao_escreveu_nada(self):
        assert _ja_exibido(ConsoleStreamRenderer(_console()), "texto") is False


class TestQuandoLigar:
    def test_desligado_sem_tty(self):
        """Sem terminal (pipe, CI) não há o que animar, e sequência de cursor
        no meio de um arquivo é lixo."""
        with patch("sys.stdin") as stdin:
            stdin.isatty.return_value = False
            assert _make_streamer(_console()) is None

    def test_ligado_em_tty(self):
        with patch("sys.stdin") as stdin:
            stdin.isatty.return_value = True
            assert isinstance(_make_streamer(_console()), ConsoleStreamRenderer)

    def test_config_pode_desligar(self):
        cfg = MagicMock()
        cfg.agent.stream_response = False
        with patch("sys.stdin") as stdin, patch("bauer.config_loader.load_config", return_value=cfg):
            stdin.isatty.return_value = True
            assert _make_streamer(_console()) is None

    def test_config_ilegivel_mantem_o_padrao(self):
        with patch("sys.stdin") as stdin, \
             patch("bauer.config_loader.load_config", side_effect=RuntimeError("config quebrado")):
            stdin.isatty.return_value = True
            assert isinstance(_make_streamer(_console()), ConsoleStreamRenderer)
