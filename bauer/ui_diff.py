"""ui_diff.py — colore o diff que a tool JÁ produziu (plano 028 F3).

A tool `patch` (bauer/tools/fs.py) monta um `unified_diff` e o embute no
resultado que devolve ao modelo:

    Arquivo 'auth.py' atualizado.
    --- a/auth.py
    +++ b/auth.py
    @@ -1,3 +1,3 @@
     import os
    -    token = os.environ["BAUER_TOKEN"]
    +    token = pool.acquire("bauer")

Este módulo NÃO recalcula diff nenhum: recalcular abriria espaço para mostrar
uma coisa e ter aplicado outra. Ele reconhece o bloco de diff dentro do texto
da tool e o pinta — o que aparece na tela é, por construção, exatamente o que
foi escrito no arquivo.

Sanitiza ANSI de subprocesso no caminho: saída de `run_command` pode trazer
sequências de escape (cores do npm, do docker, barras de progresso). Repassar
isso cru para o terminal deixa a tela refém do processo filho — cor que não
fecha, cursor que anda, linha que se apaga.
"""

from __future__ import annotations

import re

from rich.text import Text

from . import theme

#: Sequências de escape ANSI (CSI e OSC). Saída de subprocesso é dado, não
#: instrução de desenho — o terminal é nosso, não do processo filho.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")

#: Cabeçalho de arquivo do unified diff.
_CABECALHO = ("--- ", "+++ ")


def limpar_ansi(texto: str) -> str:
    """Remove sequências de escape ANSI e caracteres de controle perigosos."""
    limpo = _ANSI.sub("", texto)
    # \r isolado reposiciona o cursor no início da linha (barra de progresso);
    # dentro de um bloco renderizado isso sobrescreve o que já está na tela.
    return limpo.replace("\r\n", "\n").replace("\r", "")


def parece_diff(texto: str) -> bool:
    """True se o texto contém um bloco de unified diff."""
    return "\n@@" in texto or texto.startswith("@@") or "\n--- " in texto


def separar_diff(texto: str) -> "tuple[str, str]":
    """Parte o resultado da tool em (mensagem, bloco de diff).

    A tool devolve uma linha de status ANTES do diff ("Arquivo 'x' atualizado.")
    e às vezes um aviso DEPOIS (erro de sintaxe). O que interessa colorir é só
    o miolo.
    """
    linhas = texto.splitlines()
    inicio = None
    for i, linha in enumerate(linhas):
        if linha.startswith(_CABECALHO) or linha.startswith("@@"):
            inicio = i
            break
    if inicio is None:
        return texto, ""
    return "\n".join(linhas[:inicio]).strip(), "\n".join(linhas[inicio:])


def render_diff(diff: str, *, max_linhas: int = 24, largura_max: int = 200) -> Text:
    """Pinta um unified diff: `+` ok, `-` bad, `@@` e cabeçalhos apagados.

    `max_linhas` é teto obrigatório, não gentileza: um patch grande jogaria
    centenas de linhas na tela a cada tool call e enterraria a resposta do
    modelo. O corte é anunciado — silenciar o que foi omitido faria a tela
    mentir sobre o tamanho da mudança.
    """
    from .ui import active_glyphs

    g = active_glyphs()
    linhas = limpar_ansi(diff).splitlines()
    saida = Text()

    mostradas = linhas[:max_linhas]
    for i, linha in enumerate(mostradas):
        if i:
            saida.append("\n")
        # trunca linha muito longa (minified, base64) — quebra de linha
        # automática de uma linha de 40k chars trava o render
        if len(linha) > largura_max:
            linha = linha[:largura_max] + " …"

        saida.append("     ")
        if linha.startswith("+++") or linha.startswith("---"):
            saida.append(linha, style=theme.FAINT)
        elif linha.startswith("@@"):
            saida.append(linha, style=theme.DIM)
        elif linha.startswith("+"):
            saida.append(f"{g.bot} ", style=theme.OK)
            saida.append(linha, style=theme.OK)
        elif linha.startswith("-"):
            saida.append(f"{g.bot} ", style=theme.BAD)
            saida.append(linha, style=theme.BAD)
        else:
            saida.append(linha, style=theme.FAINT)

    omitidas = len(linhas) - len(mostradas)
    if omitidas > 0:
        saida.append("\n     ")
        saida.append(f"… +{omitidas} linha(s) de diff", style=theme.FAINT)
    return saida


def render_saida(texto: str, *, max_linhas: int = 3, largura_max: int = 160) -> Text:
    """As últimas linhas de uma saída de tool, apagadas, para o trilho.

    Mostra o FIM e não o começo: numa build/teste demorado, o que importa é a
    linha mais recente — o começo já rolou para fora do interesse.
    """
    linhas = [linha for linha in limpar_ansi(texto).splitlines() if linha.strip()]
    ultimas = linhas[-max_linhas:] if max_linhas > 0 else []
    saida = Text()
    for i, linha in enumerate(ultimas):
        if i:
            saida.append("\n")
        if len(linha) > largura_max:
            linha = linha[:largura_max] + " …"
        saida.append("     ")
        saida.append(linha, style=theme.FAINT)
    return saida
