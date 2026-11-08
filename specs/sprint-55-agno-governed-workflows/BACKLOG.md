# BACKLOG — Sprint 55: workflows Agno sob o Kernel

- [x] Medir semântica do `Workflow` Agno instalado (skip-on-error ⇒
      `COMPLETED`; HumanReview exige db) e registrar no SPEC.
- [x] `GovernedWorkflow` com `StepSpec`/`ParallelSpec`/`ConditionSpec`,
      sem db, dentro de `run_governed`.
- [x] Livro de falhas e checagens por etapa (run, cancelamento, prazo).
- [x] Revisão humana vinculada ao run com retomada pelo Kernel.
- [x] Eventos por etapa sem conteúdo.
- [x] Processo piloto `data_report_workflow`.
- [x] Testes `tests/test_agno_governed_workflow.py`: sucesso+retomada,
      negação, cancelamento, expiração da revisão, falha de etapa, prazo,
      gate do Kernel, ausência de bypass de custódia/db.
- [x] Gates: suíte completa, Ruff bloqueante e de estilo.
