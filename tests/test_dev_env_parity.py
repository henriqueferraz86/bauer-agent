"""Paridade entre o ambiente do dev e o do CI.

## Por que este arquivo existe

"CI e máquina do dev divergirem em silêncio" foi um padrão recorrente, com
custo real em várias sessões:

  1. Suíte rodada com o venv de OUTRO projeto → 40 falhas com a mensagem
     críptica "async def functions are not natively supported". Não havia
     bug nenhum: faltava `pytest-asyncio` naquele venv.
  2. Baseline do mypy medido com pydantic 2.9.2 (python do sistema) enquanto
     o CI usa 2.13.4 (extra `dev`). Bibliotecas mudam os próprios stubs entre
     versões → módulo passa num lado e falha no outro. Escapou local e foi
     barrado no CI.
  3. `numpy` presente localmente e ausente no CI fazia o mypy abortar por erro
     de sintaxe em stub — só na máquina do dev.

Todos são a MESMA falha: o ambiente que roda a suíte não é o ambiente que
decide o merge, e nada dizia isso em voz alta.

## O que este teste faz

Transforma divergência silenciosa em erro nomeado, com o comando exato para
consertar. Ele falha ANTES de a divergência virar 40 falhas confusas em
outros arquivos.

Escape hatch: `BAUER_SKIP_ENV_PARITY=1` para quem está deliberadamente
testando outras versões (bisect de dependência, matriz manual).
"""

from __future__ import annotations

import os
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version as versao_instalada
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
LOCK = RAIZ / "uv.lock"

pular = pytest.mark.skipif(
    os.environ.get("BAUER_SKIP_ENV_PARITY") == "1",
    reason="BAUER_SKIP_ENV_PARITY=1 — verificação de paridade desligada de propósito",
)

COMO_CONSERTAR = (
    "\n\nPara alinhar com o ambiente do CI, na raiz do repo:\n"
    "    uv sync --frozen --extra dev\n"
    "e rode a suite com o python do venv do PROJETO:\n"
    "    .venv/Scripts/python -m pytest      (Windows)\n"
    "    .venv/bin/python -m pytest          (Linux/macOS)\n"
    "\nSe a divergencia for proposital, exporte BAUER_SKIP_ENV_PARITY=1."
)

# Pacotes cuja VERSAO muda o resultado da suíte ou do typecheck — não é a lista
# toda de propósito: pinar tudo transformaria qualquer bump transitivo em falha
# vermelha sem informação. Estes três já causaram divergência real.
MOLDAM_RESULTADO = ("pydantic", "pytest", "pytest-asyncio")

# Pacotes que, ausentes, produzem falha CONFUSA em outro lugar em vez de um
# erro claro aqui.
IMPRESCINDIVEIS = ("pytest", "pytest_asyncio", "fastapi", "httpx", "pydantic", "typer")


def _versoes_do_lock() -> dict[str, set[str]]:
    """Versões pinadas no uv.lock. Um pacote pode ter MAIS DE UMA (numpy tem,
    via resolution-markers por plataforma), então guarda-se um conjunto."""
    dados = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    mapa: dict[str, set[str]] = {}
    for pkg in dados.get("package", []):
        nome, v = pkg.get("name"), pkg.get("version")
        if nome and v:
            mapa.setdefault(nome, set()).add(v)
    return mapa


def test_uv_lock_existe_e_e_legivel():
    """Se o lock sumir, todo o resto deste arquivo vira ruído."""
    assert LOCK.exists(), f"uv.lock nao encontrado em {LOCK}"
    assert _versoes_do_lock(), "uv.lock sem pacotes — arquivo corrompido?"


@pular
def test_bauer_vem_deste_repositorio():
    """Pega o caso mais confuso de todos: rodar a suite contra OUTRA instalacao
    do bauer. Os testes importam `bauer`, e se ele resolver para um site-packages
    de outro lugar, mede-se codigo que nao e o do working tree — verde enganoso
    ou vermelho inexplicavel, os dois igualmente caros."""
    import bauer

    origem = Path(bauer.__file__).resolve().parent
    assert origem.parent == RAIZ, (
        f"`import bauer` resolveu para {origem}, nao para {RAIZ / 'bauer'}.\n"
        f"O interpretador em uso e {sys.executable}." + COMO_CONSERTAR
    )


@pular
@pytest.mark.parametrize("modulo", IMPRESCINDIVEIS)
def test_dependencia_de_dev_importavel(modulo):
    """Falha AQUI, com nome e solução, em vez de virar erro obscuro adiante.

    Caso real: sem `pytest_asyncio`, 40 testes falhavam com "async def functions
    are not natively supported" — mensagem que não menciona ambiente nenhum e
    manda o leitor procurar bug onde não há.
    """
    pytest.importorskip(modulo, reason=f"{modulo} ausente" + COMO_CONSERTAR)


@pular
@pytest.mark.parametrize("pacote", MOLDAM_RESULTADO)
def test_versao_bate_com_o_lock(pacote):
    """A divergência que quebrou o baseline do mypy: pydantic 2.9.2 local vs
    2.13.4 no CI. Bibliotecas mudam os próprios stubs e comportamento entre
    versões, então medir numa e mergear na outra é medir a coisa errada."""
    do_lock = _versoes_do_lock().get(pacote)
    if not do_lock:
        pytest.skip(f"{pacote} nao esta no uv.lock")

    try:
        atual = versao_instalada(pacote)
    except PackageNotFoundError:
        pytest.fail(f"{pacote} nao esta instalado." + COMO_CONSERTAR)

    assert atual in do_lock, (
        f"{pacote} instalado {atual}, lock diz {sorted(do_lock)}.\n"
        f"O CI vai rodar com a versao do lock — o que voce mede aqui pode nao "
        f"ser o que decide o merge." + COMO_CONSERTAR
    )
