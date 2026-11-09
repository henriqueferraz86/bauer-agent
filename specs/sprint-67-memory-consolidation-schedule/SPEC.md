# SPEC — Sprint 67: consolidação agendada e governada (plano 065, fatia 2)

Depende da sprint 66.

## Problema

O consolidador manual depende de alguém lembrar de rodá-lo. Uma execução
periódica é trabalho autônomo: não há humano no momento do disparo. Por isso
ela precisa respeitar a mesma governança das outras execuções autônomas
(kill-switch, policy e budget no Kernel) e o opt-in do workspace.

## Decisões

- **Agendador: o cron durável existente** (`AutomationStore` /
  `AutomationScheduler`, `bauer cron`). Um job com `metadata.kind =
  "memory_consolidation"` não vira task de Kanban com prompt para um agente
  LLM. O scheduler executa a consolidação direto, dentro de
  `core.kernel.entry.run_governed(..., autonomous=True)`. Escolhemos o cron, e
  não o `core/runtime/scheduler.py`, porque o cron já tem CLI, daemon e jobs
  por workspace (`bauer cron list|pause|resume|delete|daemon`).
- **O Kernel vem de `build_kernel(None, root=<sessions_dir>/../runtime,
  workspace=<workspace>)`**, o mesmo runtime root que os canais usam
  (`channel_governance.runtime_root`). O kill-switch do runtime, a policy, o
  modo `locked`/budget em dólar e o contrato de tarefa do workspace valem
  antes de o executor rodar.
- **Kill-switch próprio da feature:** `BAUER_MEMORY_CONSOLIDATION_DISABLED=1`.
  O opt-in por workspace (`bauer memory consolidate disable`) funciona como
  desligamento local.
- **Budget diário de tokens** por workspace (`daily_token_budget`, padrão
  20.000), medido pela soma dos `runs` do dia no ledger. A execução recebe só
  o saldo.
- **Retry/backoff.** Sem retry imediato no Kernel (`max_retries=0`), para não
  gastar o budget em rajada. Uma execução agendada que falha volta depois de
  15 min, 30 min, 1 h, até no máximo 6 h, nunca depois do próximo horário
  regular. O contador `consecutive_failures` fica no metadata do job. As
  sessões que falharam são re-tentadas pelo ledger até `max_attempts`.
- **Métricas:** a tabela `runs` do ledger (por execução: trigger, status,
  sessões ok/falha, registros, tokens), `bauer memory consolidate status`, e
  o run auditável do cron (`automation_runs.metadata.outcome`) e do Kernel.
- **Custo em dólar** reportado como 0 ao Kernel: o consumo é medido em
  tokens. Provider local custa zero, e o `daily_budget_usd` continua valendo
  como gate de admissão.

## Escopo

1. `bauer/memory_consolidation_schedule.py`: `schedule_consolidation`,
   `run_scheduled_consolidation` (que nunca levanta), `backoff_seconds` e
   `next_run_after_result`.
2. `AutomationScheduler.enqueue_job` desvia jobs com
   `kind=memory_consolidation` para `_run_memory_consolidation`: cria o run do
   cron e grava status/outcome, o próximo horário e o backoff. O `tick`
   reporta o `run_id` quando não há task.
3. `bauer memory consolidate schedule [--schedule] [--sessions-dir]`. A
   remoção/pausa usa o `bauer cron delete|pause` existente.

## Fora de escopo

- Disparo por ociosidade (idle) e cron do runtime (`core/runtime/scheduler`).
- Diretivas (sprint 68).

## Critérios de aceite

- [x] Uma execução agendada passa pelo Kernel (`governed=True`, `kernel_run_id`).
- [x] Com o kill-switch do runtime ou da feature ligado, ou com o opt-in
  desligado, o LLM nunca é chamado.
- [x] O budget diário de tokens bloqueia ou limita a execução.
- [x] Uma falha agenda retry com backoff e o sucesso posterior zera o
  contador, sem duplicar registros.
- [x] O job interno não cria task de Kanban.
