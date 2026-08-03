"""Testes do render de streaming no terminal (bauer/ui_stream.py) — plano 028 F1.

O que mais importa aqui é a EQUIVALÊNCIA POR FRAGMENTAÇÃO: o texto que aparece
não pode depender de onde o provider cortou os chunks. Um `\\n\\n` partido entre
dois pacotes SSE é o caso normal, não o exótico.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from bauer.ui_stream import ConsoleStreamRenderer


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, width=80, highlight=False)


def _sem_padding(saida: str) -> str:
    """Remove o preenchimento de fim de linha.

    Bloco selado com um `Live` aberto sai preenchido até a largura do console
    (é assim que o Rich apaga o quadro anterior); selado sem `Live`, sai justo.
    Espaço à direita não é conteúdo — comparar com ele mediria por qual caminho
    o render passou, não o que o usuário lê.
    """
    return "\n".join(linha.rstrip() for linha in saida.splitlines())


def _alimentar(texto: str, *, pedaco: int = 0, plain: bool = False) -> tuple[str, ConsoleStreamRenderer]:
    """Passa `texto` pelo renderer em pedaços de `pedaco` chars (0 = tudo de uma vez)."""
    con = _console()
    r = ConsoleStreamRenderer(con, plain=plain)
    if pedaco <= 0:
        r.on_delta(texto)
    else:
        for i in range(0, len(texto), pedaco):
            r.on_delta(texto[i:i + pedaco])
    r.close()
    return con.file.getvalue(), r


class TestTextoChega:
    def test_texto_simples_aparece(self):
        out, _ = _alimentar("Olá, isso é uma resposta.")
        assert "Olá, isso é uma resposta." in out

    def test_texto_completo_fica_disponivel(self):
        _, r = _alimentar("um dois três")
        assert r.text == "um dois três"

    def test_cabecalho_bauer_sai_uma_vez(self):
        out, _ = _alimentar("bloco um\n\nbloco dois\n\nbloco três\n\n")
        assert out.count("bauer") == 1

    def test_nada_a_renderizar_nao_imprime_cabecalho(self):
        con = _console()
        r = ConsoleStreamRenderer(con)
        r.close()
        assert con.file.getvalue() == ""

    def test_chunk_vazio_e_ignorado(self):
        con = _console()
        r = ConsoleStreamRenderer(con)
        r.on_delta("")
        assert r.text == ""
        assert con.file.getvalue() == ""


class TestEquivalenciaPorFragmentacao:
    """Mesmo conteúdo, cortes diferentes → mesmo texto visível."""

    CASOS = [
        "Um parágrafo só.",
        "Primeiro.\n\nSegundo.\n\nTerceiro.",
        "Antes.\n\n```python\nx = 1\n\ny = 2\nprint(x + y)\n```\n\nDepois.",
        "- item um\n- item dois\n\nfecho",
        "```\nsem linguagem\n```",
        "Texto **negrito** e `code` inline.\n\nOutro.",
    ]

    @pytest.mark.parametrize("texto", CASOS)
    @pytest.mark.parametrize("pedaco", [1, 2, 5, 13])
    def test_render_independe_do_corte(self, texto: str, pedaco: int):
        """O teste que vale: compara a saída RENDERIZADA (markdown, blocos,
        recuos), não o eco cru do modo plain — que passaria mesmo com o
        divisor de blocos quebrado."""
        inteiro, _ = _alimentar(texto)
        fatiado, _ = _alimentar(texto, pedaco=pedaco)
        assert _sem_padding(fatiado) == _sem_padding(inteiro)

    @pytest.mark.parametrize("texto", CASOS)
    @pytest.mark.parametrize("pedaco", [1, 3, 7, 50])
    def test_acumulado_independe_do_corte(self, texto: str, pedaco: int):
        _, r = _alimentar(texto, pedaco=pedaco)
        assert r.text == texto

    def test_quebra_dupla_partida_entre_chunks(self):
        """O caso que quebra implementação ingênua: '\\n' num pacote, '\\n' no
        seguinte. Se o corte de bloco olhar só o chunk, os dois parágrafos
        grudam."""
        con = _console()
        r = ConsoleStreamRenderer(con)
        r.on_delta("primeiro parágrafo\n")
        r.on_delta("\nsegundo parágrafo")
        r.close()
        out = con.file.getvalue()
        assert "primeiro parágrafo" in out and "segundo parágrafo" in out
        assert "primeiro parágrafosegundo" not in out.replace("\n", "")

    def test_cerca_partida_entre_chunks(self):
        con = _console()
        r = ConsoleStreamRenderer(con)
        for pedaco in ["Veja:\n\n``", "`py\nx=", "1\n``", "`\n\nfim"]:
            r.on_delta(pedaco)
        r.close()
        assert r.text == "Veja:\n\n```py\nx=1\n```\n\nfim"


class TestBlocoDeCodigo:
    def test_linha_em_branco_dentro_da_cerca_nao_corta(self):
        """Dentro de ``` a linha em branco é conteúdo. Cortar ali partiria o
        realce de sintaxe em dois blocos."""
        con = _console()
        r = ConsoleStreamRenderer(con)
        r.on_delta("```python\ndef a():\n    pass\n\ndef b():\n    pass\n```\n")
        # antes de fechar a cerca, nada do código pode ter sido selado
        assert r._em_cerca is False  # a cerca fechou no mesmo chunk
        r.close()
        out = con.file.getvalue()
        assert "def a" in out and "def b" in out

    def test_cerca_aberta_segura_o_bloco(self):
        con = _console()
        r = ConsoleStreamRenderer(con)
        r.on_delta("```python\nx = 1\n\n")
        assert r._em_cerca is True
        r.on_delta("y = 2\n```\n")
        assert r._em_cerca is False

    def test_texto_antes_da_cerca_vira_bloco_proprio(self):
        con = _console()
        r = ConsoleStreamRenderer(con)
        r.on_delta("olha isso:\n```sh\nls\n```\n")
        r.close()
        out = con.file.getvalue()
        assert "olha isso" in out and "ls" in out


class TestValvulaDeBlocoGigante:
    def test_bloco_sem_quebra_e_despejado(self):
        """Sem válvula, um bloco enorme sem linha em branco faz o Live
        re-renderizar tudo a cada quadro — custo quadrático."""
        from bauer import ui_stream

        con = _console()
        r = ConsoleStreamRenderer(con)
        r.on_delta("x" * (ui_stream._MAX_BLOCO + 10))
        assert len(r._bloco) == 0  # despejado
        assert r.escreveu_algo is True

    def test_valvula_nao_dispara_dentro_de_cerca(self):
        """Despejar no meio de uma cerca produziria código sem fechamento."""
        from bauer import ui_stream

        con = _console()
        r = ConsoleStreamRenderer(con)
        r.on_delta("```python\n" + "a = 1\n" * (ui_stream._MAX_BLOCO // 3))
        assert r._em_cerca is True
        assert len(r._bloco) > 0  # segurou


class TestModoPlain:
    def test_plain_escreve_cru_sem_controle(self):
        out, _ = _alimentar("linha um\n\nlinha dois", plain=True)
        assert out == "linha um\n\nlinha dois"
        assert "\x1b[" not in out  # nenhuma sequência ANSI

    def test_plain_detectado_por_env(self, monkeypatch):
        from bauer import ui_stream

        monkeypatch.setenv("BAUER_UI", "plain")
        assert ui_stream._plain_mode() is True
        monkeypatch.setenv("BAUER_UI", "rich")
        assert ui_stream._plain_mode() is False


class TestNuncaDerruba:
    def test_markdown_quebrado_cai_para_texto(self):
        from unittest.mock import patch

        con = _console()
        r = ConsoleStreamRenderer(con)
        with patch("rich.markdown.Markdown", side_effect=RuntimeError("boom")):
            r.on_delta("conteúdo qualquer\n\n")
            r.close()
        assert "conteúdo qualquer" in con.file.getvalue()

    def test_live_indisponivel_nao_perde_texto(self):
        """Outro Live ativo (o spinner) é o caso real — o texto tem que sair
        mesmo sem atualização no lugar."""
        from unittest.mock import patch

        con = _console()
        r = ConsoleStreamRenderer(con)
        with patch("rich.live.Live", side_effect=RuntimeError("live já ativo")):
            r.on_delta("resposta importante")
            r.close()
        assert "resposta importante" in con.file.getvalue()

    def test_console_quebrado_nao_levanta(self):
        from unittest.mock import MagicMock

        con = MagicMock()
        con.print.side_effect = RuntimeError("console morto")
        r = ConsoleStreamRenderer(con)
        r.on_delta("texto")   # não pode levantar
        r.on_tool("read_file")
        r.on_round()
        assert r.close() == "texto"  # o texto recebido continua íntegro


class TestProtocoloDoSink:
    def test_on_round_fecha_bloco_pendente(self):
        con = _console()
        r = ConsoleStreamRenderer(con)
        r.on_delta("parcial sem quebra")
        r.on_round()
        assert r._bloco == ""
        assert "parcial sem quebra" in con.file.getvalue()

    def test_on_tool_fecha_bloco_pendente(self):
        """A linha da tool não pode cair dentro do Live — o quadro seguinte
        a apagaria."""
        con = _console()
        r = ConsoleStreamRenderer(con)
        r.on_delta("vou ler o arquivo")
        r.on_tool("read_file")
        assert r._bloco == ""
        assert "vou ler o arquivo" in con.file.getvalue()

    def test_diag_conta_tokens(self):
        _, r = _alimentar("um dois três", pedaco=1)
        assert r.diag.token_count == len("um dois três")
        assert r.diag.tokens_per_second >= 0

    def test_serve_como_sink_do_delta_stream(self):
        """Contrato de verdade: instalado via set_sink, recebe os emits."""
        from bauer import delta_stream

        con = _console()
        r = ConsoleStreamRenderer(con)
        token = delta_stream.set_sink(r)
        try:
            delta_stream.emit_round_start()
            delta_stream.emit_delta("olá ")
            delta_stream.emit_delta("mundo")
            delta_stream.emit_tool("read_file")
        finally:
            delta_stream.reset_sink(token)
        assert r.close() == "olá mundo"
