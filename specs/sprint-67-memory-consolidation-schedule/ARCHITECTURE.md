# ARCHITECTURE — Sprint 67

```text
bauer cron daemon|tick|run --workspace <ws>
  └─ AutomationScheduler.tick → enqueue_job(job)
        job.metadata.kind == "memory_consolidation"
          └─ _run_memory_consolidation
               ├─ AutomationStore.create_run(status=running)
               ├─ run_scheduled_consolidation(ws, sessions_dir)
               │    1. BAUER_MEMORY_CONSOLIDATION_DISABLED → skipped
               │    2. settings.enabled == False           → skipped
               │    3. saldo do budget diário <= 0          → skipped
               │    4. run_governed(build_kernel(...), executor, autonomous=True)
               │         preflight: kill-switch · policy · budget/locked · contrato
               │         executor: MemoryConsolidator.run(max_tokens=saldo, trigger="scheduled")
               │    5. blocked_before_start → skipped(governance)
               ├─ AutomationStore.update_run(status, outcome)
               └─ update_job(next_run_at = regular | backoff, consecutive_failures)
        senão: fluxo antigo (task READY no Kanban)
```

## Mapeamento de status

| `run_scheduled_consolidation` | run do cron | próximo horário |
|---|---|---|
| `completed` / `partial` / `nothing_to_do` | `completed` | regular; falhas = 0 |
| `skipped` (qualquer motivo) | `skipped` | regular; falhas = 0 |
| `failed` | `failed` | `due + backoff(n)`, limitado ao regular |

`partial` (budget esgotado) não é falha: o saldo de amanhã continua o
trabalho.

## Por que não `admit()`

A consolidação é um executor síncrono e curto. `run_governed` dá custódia
completa: o Kernel declara `completed`/`failed`, e a regra do `AGENTS.md`
reserva `admit()` para motores que não podem ser envolvidos. O mapa
`PERMITIDOS` de `test_arquitetura_custodia_kernel.py` fica igual.

## Isolamento

- O job vive no `.bauer_automation` do próprio workspace e consolida só esse
  workspace (o escopo é derivado de `self.workspace`).
- O `sessions_dir` do job é só a fonte. A seleção continua filtrando por
  `workspace_id`.
