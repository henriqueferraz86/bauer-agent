"""A Evaluation Suite roda no CI e o resultado não pode piorar em silêncio.

Sem isto a suíte seria um script que alguém lembra de rodar. Aqui ela vira parte
do contrato de release: os cenários CRÍTICOS têm de passar 100%, a taxa geral
não pode cair abaixo da meta, e `false_success_rate` / `orphaned_run_rate` —
as duas métricas que de fato dizem se um harness governa — ficam travadas.
"""

from __future__ import annotations

import pytest

from evals.harness.runner import formatar, rodar

#: Metas do §15 do plano (docs/harness/PLANO_HARNESS_90.md).
META_CRITICOS = 1.0
META_GERAL = 0.90
META_FALSO_SUCESSO = 0.02
META_ORFAOS = 0.01

#: Cenários que hoje falham por DÍVIDA CONHECIDA, não por regressão. Cada um
#: precisa de justificativa e do item de backlog que o fecha — a lista existe
#: para encolher, e o teste abaixo falha se alguém a deixar frouxa.
DIVIDA_CONHECIDA: dict[str, str] = {
    # VAZIA — e o teste `test_divida_paga_sai_da_lista` mantem assim: entrada
    # que volta a passar TEM de sair, senao a lista vira cemiterio e para de
    # significar coisa alguma. A primeira ocupante foi o HARNESS-030 (juiz do G4
    # julgando a si mesmo), quitada no mesmo PR que criou a suite.
}


@pytest.fixture(scope="module")
def relatorio():
    return rodar()


def test_cenarios_suficientes(relatorio):
    """§15: harness_eval_scenarios >= 23."""
    assert relatorio.total >= 23, f"só {relatorio.total} cenários"


def test_criticos_passam_todos(relatorio):
    falhos = [v.nome for v in relatorio.vereditos if v.critico and not v.passou]
    assert not falhos, "cenários CRÍTICOS falhando:\n" + "\n".join(
        f"  - {n}" for n in falhos) + "\n\n" + formatar(relatorio)


def test_taxa_geral_acima_da_meta(relatorio):
    assert relatorio.taxa_geral >= META_GERAL, (
        f"taxa geral {relatorio.taxa_geral:.0%} < {META_GERAL:.0%}\n" + formatar(relatorio))


def test_false_success_rate(relatorio):
    """A métrica mais importante do plano: concluir como sucesso o que não era."""
    assert relatorio.taxa_falso_sucesso <= META_FALSO_SUCESSO, (
        f"false_success_rate {relatorio.taxa_falso_sucesso:.1%} > {META_FALSO_SUCESSO:.0%}\n"
        + formatar(relatorio))


def test_orphaned_run_rate(relatorio):
    assert relatorio.taxa_run_orfao <= META_ORFAOS, (
        f"orphaned_run_rate {relatorio.taxa_run_orfao:.1%} > {META_ORFAOS:.0%}\n"
        + formatar(relatorio))


def test_falhas_sao_apenas_divida_registrada(relatorio):
    """Falha nova = regressão. Falha antiga = dívida, e precisa estar escrita."""
    falhos = {v.nome for v in relatorio.vereditos if not v.passou}
    novas = falhos - set(DIVIDA_CONHECIDA)
    assert not novas, (
        "cenários falhando SEM justificativa registrada:\n"
        + "\n".join(f"  - {n}" for n in sorted(novas))
        + "\n\nSe for regressão, conserte. Se for dívida aceita, registre em "
          "DIVIDA_CONHECIDA com o item de backlog que a fecha.\n\n" + formatar(relatorio))


def test_divida_paga_sai_da_lista(relatorio):
    """Quando a dívida é quitada, a entrada tem de sair — senão a lista vira
    cemitério e para de significar alguma coisa."""
    falhos = {v.nome for v in relatorio.vereditos if not v.passou}
    quitadas = set(DIVIDA_CONHECIDA) - falhos
    assert not quitadas, (
        "estes cenários JÁ PASSAM — remova de DIVIDA_CONHECIDA para travar o ganho:\n"
        + "\n".join(f"  - {n}" for n in sorted(quitadas)))


def test_toda_divida_tem_justificativa():
    for nome, motivo in DIVIDA_CONHECIDA.items():
        assert len(motivo) > 60, f"{nome}: justificativa curta demais"
