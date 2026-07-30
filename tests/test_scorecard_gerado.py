"""A tabela do scorecard no plano tem de bater com a medição.

Tabela escrita à mão envelhece em silêncio: o número fica no documento, o código
muda, e ninguém percebe — foi o que aconteceu com metade do scorecard, onde
"Observabilidade 70%" era avaliação minha e a contagem real deu 53%.

Gerada, ou está certa ou este teste quebra. É o mesmo princípio dos outros
ratchets do repo (`except:pass`, dívida das evals, custódia do Kernel): o número
só muda quando alguém decide mudá-lo.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PLANO = RAIZ / "docs" / "harness" / "PLANO_HARNESS_90.md"

COMANDO = "python -m evals.harness.medir --markdown"


def _gerado() -> str:
    from evals.harness.medir import CAPACIDADES, markdown

    return markdown([fn() for fn in CAPACIDADES]).strip()


def _no_documento() -> str:
    from evals.harness.medir import MARCA_FIM, MARCA_INICIO

    texto = PLANO.read_text(encoding="utf-8")
    i, f = texto.find(MARCA_INICIO), texto.find(MARCA_FIM)
    assert i != -1 and f != -1, (
        f"marcadores do scorecard sumiram de {PLANO.name} — regenere com `{COMANDO}`")
    return texto[i:f + len(MARCA_FIM)].strip()


def test_tabela_do_plano_esta_em_dia():
    doc, med = _no_documento(), _gerado()
    assert doc == med, (
        f"A tabela em {PLANO.name} divergiu da medição.\n"
        f"Regenere e substitua o bloco entre os marcadores:\n\n    {COMANDO}\n\n"
        "Se o número CAIU, o item que saiu da conta está na coluna 'o que falta'.")


def test_medicao_nao_inventa_numero():
    """Capacidade não contável fica DECLARADA, não vira número.

    Um scorecard onde metade é contagem e metade é chute vale menos que um que
    admite a fronteira.
    """
    from evals.harness.medir import CAPACIDADES, NAO_MENSURAVEIS

    contaveis = {fn().nome for fn in CAPACIDADES}
    assert not (contaveis & set(NAO_MENSURAVEIS)), (
        "capacidade não pode estar nos dois lados: ou é contada, ou é declarada")
    for nome, motivo in NAO_MENSURAVEIS.items():
        assert len(motivo) > 25, f"{nome}: motivo curto demais para ser útil"


def test_toda_capacidade_AVALIADA_tem_itens():
    """% sobre denominador zero seria 0% sem significar nada.

    A regra vale para capacidade que AVALIA. Uma `informativa` sem dado é o
    estado honesto — "ainda não há run autônomo registrado" não é 0% nem 100%,
    e forçá-la a inventar um item para satisfazer o ratchet seria exatamente o
    número fabricado que este arquivo existe para impedir. Ela já está fora da
    média (ver `test_informativa_fica_fora_da_media`), então não contamina nada.
    """
    from evals.harness.medir import CAPACIDADES

    for fn in CAPACIDADES:
        c = fn()
        if c.informativa:
            continue
        assert c.total > 0, f"{c.nome} não tem itens — a contagem seria vazia"


def test_informativa_nao_finge_que_avalia():
    """Contrapeso: `informativa` não pode virar rota de fuga para capacidade que
    deveria ser cobrada. Quem reporta tem meta 0 e fica fora da média — as duas
    coisas, senão é avaliação disfarçada."""
    from evals.harness.medir import CAPACIDADES

    for fn in CAPACIDADES:
        c = fn()
        if not c.informativa:
            continue
        assert c.meta == 0, f"{c.nome}: informativa com meta {c.meta} cobra sem dizer"


def test_o_que_falta_e_acionavel():
    """Item pendente sem nome não serve para nada — a coluna existe para virar
    backlog, não para exibir um número."""
    from evals.harness.medir import CAPACIDADES

    for fn in CAPACIDADES:
        c = fn()
        for item in c.falta:
            assert item and len(str(item)) > 3, f"{c.nome}: pendência sem nome útil"
