"""ui_stream.py — a resposta aparecendo enquanto o modelo escreve (plano 028, F1).

Antes disto o `bauer agent` coletava a resposta INTEIRA (`parts = list(...)`) e
só então imprimia um bloco de Markdown: 5 a 60 segundos de spinner "pensando…"
e a resposta caindo de uma vez. O encanamento de streaming já existia e já
rodava — mas só o gateway instalava sink (`bauer/delta_stream.py`). O terminal
nunca instalou. Este módulo é o sink que faltava.

O problema difícil não é receber o token, é RENDERIZAR sem escolher entre duas
perdas:

  - imprimir cru token a token → aparece rápido, mas perde Markdown (código sem
    realce, lista sem recuo, tabela desmontada);
  - reparsear o documento inteiro a cada token → formata, mas o custo cresce
    com o quadrado do tamanho e trava o terminal em resposta longa.

A saída é renderizar por BLOCO. O bloco em curso vive dentro de um `Live` como
texto puro (aparece na hora); quando ele fecha — linha em branco fora de cerca,
ou a cerca de código fechando — o `Live` é atualizado para o Markdown formatado
daquele bloco e encerrado, deixando o resultado no histórico. O bloco seguinte
abre um `Live` novo. Cada trecho é parseado uma vez, e o usuário vê texto no
primeiro token.
"""

from __future__ import annotations

import os
from typing import Any

from rich.console import Console
from rich.text import Text

from . import theme, ui
from .delta_stream import StreamDiag

#: Bloco sem fechamento maior que isto é despejado como texto e recomeçado.
#: Modelo que escreve 20 KB sem uma linha em branco existe, e o `Live` re-renderiza
#: o bloco inteiro a cada quadro — sem esta válvula, o custo vira quadrático.
_MAX_BLOCO = 4000

#: Quadros por segundo do Live. 10 é suave ao olho e barato; acima disso o
#: terminal passa mais tempo redesenhando do que o modelo gerando.
_FPS = 10


def _plain_mode(env: "dict[str, str] | None" = None) -> bool:
    """Modo burro: sem Live, sem cor, chunk direto no stdout.

    Vale para BAUER_UI=plain, NO_COLOR e qualquer destino que não seja
    terminal (pipe, arquivo, CI) — onde sequência de controle de cursor não
    é enfeite, é lixo no meio do texto.
    """
    e = os.environ if env is None else env
    return e.get("BAUER_UI", "").strip().lower() == "plain"


class ConsoleStreamRenderer:
    """Sink de `delta_stream` que desenha a resposta no terminal ao vivo.

    Implementa o protocolo completo do sink (`on_delta` / `on_round` /
    `on_tool`) e mais um `close()` para fechar o último bloco. Instale com
    `delta_stream.set_sink(...)` e feche no fim do turno.

    Nunca levanta: streaming é cosmético e um render quebrado não pode derrubar
    o turno — a mesma regra que os emissores de `delta_stream` já seguem. O
    texto acumulado fica em `.text` e é a fonte de verdade do que foi recebido,
    independente do que a tela conseguiu desenhar.
    """

    def __init__(
        self,
        console: Console,
        *,
        markdown: bool = True,
        plain: "bool | None" = None,
        show_header: bool = True,
    ) -> None:
        self.console = console
        self.markdown = markdown
        self.plain = _plain_mode() if plain is None else plain
        self.show_header = show_header
        self.diag = StreamDiag()

        self._buf: list[str] = []        # tudo que chegou (fonte de verdade)
        self._bloco: str = ""            # bloco ainda não fechado
        self._em_cerca = False           # dentro de ``` ?
        self._live: Any = None
        self._cabecalho_impresso = False
        self._algo_escrito = False

    # ── protocolo do sink ────────────────────────────────────────────────
    def on_round(self) -> None:
        """Nova chamada ao LLM. Fecha o que estava aberto do round anterior."""
        try:
            self._fechar_bloco(final=True)
            self.diag.on_round()
        except Exception:  # noqa: BLE001
            pass

    def on_delta(self, chunk: str) -> None:
        if not chunk:
            return
        try:
            self._buf.append(chunk)
            self.diag.on_delta(chunk)
            if self.plain:
                self.console.file.write(chunk)
                self.console.file.flush()
                self._algo_escrito = True
                return
            self._cabecalho()
            self._bloco += chunk
            self._consumir_blocos_fechados()
            self._pintar_parcial()
        except Exception:  # noqa: BLE001 — render nunca derruba o turno
            pass

    def on_tool(self, name: str) -> None:
        """Tool vai executar: fecha o bloco corrente para a linha da tool não
        cair dentro do `Live` (que a apagaria no quadro seguinte)."""
        try:
            self._fechar_bloco(final=True)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> str:
        """Fecha o último bloco e devolve o texto completo recebido."""
        try:
            self._fechar_bloco(final=True)
        except Exception:  # noqa: BLE001
            pass
        return self.text

    # ── estado ───────────────────────────────────────────────────────────
    @property
    def text(self) -> str:
        return "".join(self._buf)

    @property
    def escreveu_algo(self) -> bool:
        """A resposta já apareceu na tela? Quem chama usa isto para não
        reimprimir o texto no fim do turno (seria a resposta em dobro)."""
        return self._algo_escrito

    # ── render ───────────────────────────────────────────────────────────
    def _cabecalho(self) -> None:
        if self._cabecalho_impresso or not self.show_header:
            return
        self._cabecalho_impresso = True
        frame = self._frame()
        _print = frame.print_above if frame is not None else self.console.print
        _print("")
        try:
            _print(ui.response_header())
        except Exception:  # noqa: BLE001
            _print(Text("bauer", style=f"bold {theme.ACCENT_TEXT}"))

    def _consumir_blocos_fechados(self) -> None:
        """Fecha todo bloco já completo dentro do buffer corrente.

        Um bloco fecha em linha em branco (fora de cerca) ou na cerca de
        fechamento. A cerca importa: dentro dela a linha em branco é conteúdo,
        e cortar ali quebraria o realce de sintaxe ao meio.
        """
        while True:
            corte = self._proximo_corte()
            if corte is None:
                break
            pronto, self._bloco = self._bloco[:corte], self._bloco[corte:]
            self._emitir(pronto)

        if len(self._bloco) > _MAX_BLOCO and not self._em_cerca:
            self._emitir(self._bloco)
            self._bloco = ""

    def _proximo_corte(self) -> "int | None":
        """Índice logo após o fim do primeiro bloco completo, ou None."""
        if self._em_cerca:
            fim = self._bloco.find("```", 3 if self._bloco.startswith("```") else 0)
            if fim == -1:
                return None
            quebra = self._bloco.find("\n", fim)
            self._em_cerca = False
            return len(self._bloco) if quebra == -1 else quebra + 1

        abertura = self._bloco.find("```")
        branco = self._bloco.find("\n\n")

        if abertura != -1 and (branco == -1 or abertura < branco):
            if abertura > 0:
                return abertura  # texto antes da cerca é um bloco próprio
            # a cerca só é "aberta" quando a linha dela terminou; senão o
            # fechamento ``` do mesmo chunk seria confundido com a abertura
            if "\n" not in self._bloco[3:]:
                return None
            self._em_cerca = True
            return self._proximo_corte()

        if branco == -1:
            return None
        return branco + 2

    def _emitir(self, texto: str) -> None:
        """Sela um bloco: apaga a prévia crua e imprime o Markdown formatado.

        Selar SEMPRE por `console.print`, nunca pelo quadro final do `Live`.
        Ter dois caminhos de selagem produzia espaçamento diferente entre
        parágrafos conforme o provider tivesse ou não fatiado o chunk — a
        formatação da resposta não pode depender do tamanho do pacote SSE.
        """
        if not texto.strip():
            self._parar_live()
            return
        self._parar_live()  # transitório: a prévia crua some
        try:
            frame = self._frame()
            if frame is not None:
                # Com quadro ativo, o bloco selado vai para o HISTÓRICO (acima
                # da região viva), não para o console direto — senão sairia
                # embaixo do HUD e seria apagado no refresh seguinte.
                frame.print_above(self._formatar(texto))
            else:
                self.console.print(self._formatar(texto))
        except Exception:  # noqa: BLE001
            return
        self._algo_escrito = True

    def _formatar(self, texto: str) -> Any:
        if not self.markdown:
            return Text(texto.rstrip("\n"))
        try:
            from rich.markdown import Markdown

            return Markdown(texto.strip("\n"))
        except Exception:  # noqa: BLE001 — Markdown quebrado vira texto puro
            return Text(texto.rstrip("\n"))

    def _frame(self):
        """O quadro do turno, se houver (plano 028 F2). None = caminho do F1,
        com `Live` próprio por bloco."""
        try:
            from .ui_frame import current_frame

            return current_frame()
        except Exception:  # noqa: BLE001
            return None

    def _pintar_parcial(self) -> None:
        """Desenha o bloco ainda aberto como texto puro, com o cursor piscando."""
        if not self._bloco:
            return
        corpo = Text(self._bloco.rstrip("\n"))
        corpo.append(ui.active_glyphs().caret, style=theme.ACCENT)

        # Com o quadro do turno ativo, a prévia mora na região viva COMPARTILHADA
        # — abrir um Live próprio aqui colidiria com o do quadro (o Rich admite
        # um só) e o texto sumiria.
        frame = self._frame()
        if frame is not None:
            frame.set_rail([corpo])
            return

        if self._live is None:
            self._live = self._abrir_live(corpo)
        else:
            try:
                self._live.update(corpo)
            except Exception:  # noqa: BLE001
                pass

    def _abrir_live(self, renderavel: Any) -> Any:
        try:
            from rich.live import Live

            live = Live(
                renderavel,
                console=self.console,
                refresh_per_second=_FPS,
                # transitório: o Live é só a PRÉVIA crua do bloco em curso.
                # Ao selar, ele some e o bloco reaparece formatado — um único
                # caminho de saída definitiva (ver _emitir).
                transient=True,
                # resposta longa passa da altura da tela; sem isto o Rich corta
                # o excedente e o texto some ao rolar
                vertical_overflow="visible",
            )
            live.__enter__()
            return live
        except Exception:  # noqa: BLE001 — outro Live ativo, console capturado…
            # Sem Live não há atualização no lugar: imprime e segue. Perde-se o
            # efeito de digitação, não o conteúdo.
            try:
                self.console.print(renderavel)
                self._algo_escrito = True
            except Exception:  # noqa: BLE001
                pass
            return None

    def _parar_live(self) -> None:
        # Com quadro ativo, a "prévia" é o trilho compartilhado — limpar é
        # esvaziá-lo, não fechar um Live (que nem é nosso).
        frame = self._frame()
        if frame is not None:
            frame.clear_rail()
            return
        if self._live is None:
            return
        try:
            self._live.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        self._live = None

    def _fechar_bloco(self, *, final: bool = False) -> None:
        resto, self._bloco = self._bloco, ""
        self._em_cerca = False
        if resto.strip():
            self._emitir(resto)
        else:
            self._parar_live()
