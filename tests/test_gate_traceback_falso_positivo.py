"""O gate não pode reprovar o agente MOSTRANDO um erro que encontrou.

`NoTracebackGate` reprovava qualquer output que *contivesse* um traceback.
Medido antes do fix: 3 de 3 casos reprovados, dois deles legítimos — inclusive
o mais comum de todos, o agente explicando ao usuário a causa de uma falha.

Com `evaluator_enabled: true` isso não é cosmético: o gate reprovado consome o
orçamento de replan (re-executa a tarefa inteira) e depois derruba o run. Toda
sessão de depuração falharia. Encontrado ao preparar o kernel para ficar ligado
por default.
"""

from __future__ import annotations

import pytest

from bauer.core.kernel.evaluator import Evaluator, NoTracebackGate


def _passa(output: str) -> bool:
    return NoTracebackGate().check(request=None, result={"output": output}).passed


# ─── o que o gate DEVE deixar passar ─────────────────────────────────────────


def test_agente_mostrando_erro_encontrado():
    """O caso diariíssimo: o agente diagnosticou e está te contando."""
    assert _passa(
        "Encontrei a causa. O teste falha assim:\n\n"
        "Traceback (most recent call last):\n"
        '  File "x.py", line 3\n'
        "ZeroDivisionError: division by zero\n\n"
        "O fix é checar o divisor antes."
    )


def test_agente_explicando_typeerror():
    assert _passa("O problema é que soma() recebe str.\n"
                  "TypeError: unsupported operand type(s)")


def test_traceback_dentro_de_code_fence_e_citacao():
    """Bloco de código é exibição, nunca crash do processo."""
    assert _passa(
        "O erro que aparece é este:\n\n"
        "```\nTraceback (most recent call last):\n  File \"a.py\"\nKeyError: 'x'\n```\n\n"
        "Corrigi usando .get()."
    )


def test_fence_no_inicio_nao_conta_como_crash():
    """Output que ABRE com fence contendo traceback ainda é citação."""
    assert _passa("```\nTraceback (most recent call last):\nValueError\n```\nJá corrigido.")


def test_resposta_normal():
    assert _passa("Pronto: criei soma.py e o teste passou.")


def test_output_sem_texto_nao_e_traceback():
    """Output vazio é problema do NonEmptyOutputGate, não deste."""
    assert _passa("")


# ─── o que o gate DEVE reprovar ──────────────────────────────────────────────


@pytest.mark.parametrize("output", [
    'Traceback (most recent call last):\n  File "bauer/agent.py", line 91\nKeyError: 42',
    "\n\n   Traceback (most recent call last):\nValueError: x",   # com espaço/linha antes
    "SyntaxError: invalid syntax",
    "TypeError: unsupported operand type(s) for +",
])
def test_output_que_e_o_proprio_crash_reprova(output):
    """Executor 'completou' devolvendo o dump — é o caso que o gate existe p/ pegar."""
    assert not _passa(output)


# ─── efeito no ciclo do Kernel ───────────────────────────────────────────────


def test_replan_nao_e_gasto_com_agente_explicando_erro():
    """Antes: veredito reprovado -> Kernel replaneja e depois falha o run."""
    ev = Evaluator()
    verdict = ev.evaluate(
        run_id="r1", request=None,
        result={"output": "A causa é esta:\nTraceback (most recent call last):\n"
                          "IndexError\nAjustei o índice."},
    )
    assert verdict.passed, verdict.reason
