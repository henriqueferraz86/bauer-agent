"""Quem pode declarar o desfecho de um Run — e por quê.

O critério 4 do plano de harness diz: "o agente não pode declarar sucesso sem
validação". Na prática isso significa que ``complete_run()`` pertence ao Kernel,
depois dos gates. Um caller que fecha o run à mão está declarando sucesso por
conta própria — foi exatamente o que o S7 mediu no ``bauer run``, onde o
Evaluator nunca rodava.

Este teste NÃO proíbe todo uso: alguns casos são legítimos e ficam registrados
abaixo com justificativa. O que ele impede é o crescimento silencioso — um
caminho novo fechando run por fora sem ninguém decidir que isso é aceitável.

É a versão executável do teste arquitetural do PLANO §7, e o mecanismo de §9.3(b)
para os caminhos que não podem ter custódia.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent / "bauer"

#: quem fecha run tem de estar aqui, com o motivo. Chave = caminho relativo.
#: O número é a contagem esperada de ocorrências de complete_run/fail_run —
#: subir exige justificar; descer é progresso e o teste pede para atualizar.
PERMITIDOS: dict[str, tuple[int, str]] = {
    # o dono legítimo: é ele que fecha depois dos gates
    "core/kernel/kernel.py": (
        8,
        "o Kernel É o dono do ciclo de vida — complete_run/fail_run depois dos "
        "gates, mais o cancelamento e o guarda de terminal (_fail_se_nao_terminal)",
    ),
    # recuperação pós-restart: marcar run preso como failed É o mecanismo
    "core/runtime/resilience.py": (
        1,
        "RuntimeRecovery.recover_stuck_runs — run preso há >max_age_s vira failed; "
        "é a recuperação em si, não um caller declarando desfecho",
    ),
    # diagnóstico: governar ferramenta de medição só a acopla à policy
    "commands/benchmark_cmd.py": (
        2,
        "bauer benchmark é medição, não execução autônoma — governar um "
        "diagnóstico o faria depender da policy que ele deveria ajudar a testar",
    ),
    # ── exceções que ainda são dívida, cada uma com o motivo ────────────────
    "server.py": (
        13,
        "TRÊS grupos, todos rastreados em docs/harness/EXECUTION_PATHS.md:\n"
        " (a) ramos LEGADOS de /chat e /loop, usados só quando kernel.enabled "
        "     está desligado — o contrato da flag é o caminho antigo intocado;\n"
        " (b) /stream SSE: roda o turno em thread órfã com persistência própria "
        "     após timeout/desconexão. Envolver em stream() disputaria a posse "
        "     do run com essa thread. É o §9.3(b) do plano: a garantia aqui é "
        "     ESTE teste, não a custódia;\n"
        " (c) /v1/chat/completions: cria e fecha run próprio. Dívida conhecida, "
        "     próxima da fila — o modo não-streaming é síncrono e cabe em "
        "     continue_governed().",
    ),
}

_PADRAO = re.compile(r"\.(complete_run|fail_run)\s*\(")
_DEF = re.compile(r"def\s+(complete_run|fail_run)\b")


def _ocorrencias(caminho: Path) -> int:
    texto = caminho.read_text(encoding="utf-8", errors="replace")
    total = 0
    for linha in texto.splitlines():
        nu = linha.strip()
        if nu.startswith("#") or _DEF.search(nu):
            continue
        # docstrings citando o nome não contam (o run_cmd explica o bug antigo)
        if "``" in nu or nu.startswith(('"', "'", "*")):
            continue
        total += len(_PADRAO.findall(nu))
    return total


def _mapa_real() -> dict[str, int]:
    achados: dict[str, int] = {}
    for py in sorted(RAIZ.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        n = _ocorrencias(py)
        if n:
            achados[py.relative_to(RAIZ).as_posix()] = n
    return achados


def test_nenhum_caminho_novo_fecha_run_por_fora():
    """Fechar run fora do Kernel exige entrada em PERMITIDOS, com motivo."""
    real = _mapa_real()
    novos = {k: v for k, v in real.items() if k not in PERMITIDOS}
    assert not novos, (
        "Estes arquivos fecham Run por fora do Kernel sem justificativa "
        f"registrada: {novos}\n\n"
        "Se o caminho é de execução, use core.kernel.entry.run_governed() (ou "
        "continue_governed() para trabalho assíncrono) — quem declara completed "
        "é o Kernel, depois dos gates. Se há motivo real para a exceção, "
        "adicione a entrada em PERMITIDOS explicando qual é."
    )


@pytest.mark.parametrize("arquivo", sorted(PERMITIDOS))
def test_excecao_nao_cresce_em_silencio(arquivo: str):
    """A contagem por arquivo é travada — caminho novo dentro de um arquivo já
    conhecido também precisa de decisão consciente."""
    esperado, motivo = PERMITIDOS[arquivo]
    real = _mapa_real().get(arquivo, 0)
    assert real <= esperado, (
        f"{arquivo} passou a fechar run em {real} lugares (esperado {esperado}).\n"
        f"Motivo registrado da exceção:\n{motivo}\n\n"
        "Um caminho novo aqui precisa de custódia do Kernel, não de mais uma "
        "chamada direta."
    )
    if real < esperado:
        pytest.fail(
            f"{arquivo} caiu para {real} (travado em {esperado}) — dívida paga. "
            f"Baixe o número em PERMITIDOS para travar o ganho."
        )


def test_todo_permitido_tem_motivo_escrito():
    """Exceção sem justificativa é exceção esquecida."""
    for arquivo, (_n, motivo) in PERMITIDOS.items():
        assert len(motivo) > 40, f"{arquivo}: justificativa curta demais"


def test_entry_e_o_caminho_oferecido():
    """O helper existe e expõe as duas formas de custódia."""
    from bauer.core.kernel import continue_governed, run_governed
    assert callable(run_governed) and callable(continue_governed)
