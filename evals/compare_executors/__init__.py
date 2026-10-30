"""Harness de comparação bauer_native vs Agno — plano 062, sprint 36, item 4.

Diferente de ``evals/harness`` (que mede se o Kernel governa certo, hermético,
sem rede), este módulo mede o executor de verdade: roda a MESMA tarefa nos
dois motores contra um provider real e compara custo, latência e tokens —
métricas duras, comparáveis diretamente. "Qualidade" não tem veredito
automático aqui (não existe harness de avaliação de qualidade no repositório
hoje, e inventar um score ad-hoc seria pior que não medir — ver
``specs/sprint-36-agno-streaming-cost-context/SPEC.md``): o relatório grava as
duas respostas lado a lado para um humano decidir
equivalente/pior/melhor por tarefa.

Exige um provider real configurado (``config.yaml``) e credenciais válidas —
NUNCA roda na suíte hermética padrão. Uso:

    uv run python -m evals.compare_executors --config config.yaml
    uv run python -m evals.compare_executors --config config.yaml --json out.json
"""

from __future__ import annotations

__all__ = ["ExecutionResult", "ComparisonRow", "Report", "run_comparison"]


def __getattr__(name: str):
    if name in set(__all__):
        from . import runner as _r

        return getattr(_r, name)
    raise AttributeError(name)
