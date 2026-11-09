# BACKLOG — Sprint 67

### 1. Execução governada
Status: concluído
- `run_scheduled_consolidation` com kill-switch da feature, opt-in, budget
  diário e `run_governed(autonomous=True)`.

### 2. Integração com o cron durável
Status: concluído
- Job interno `memory_consolidation` no `AutomationScheduler`, sem Kanban.
- Run auditável, próximo horário, backoff e `consecutive_failures`.

### 3. CLI
Status: concluído
- `bauer memory consolidate schedule`, aviso quando o opt-in está desligado.

### 4. Testes e gates
Status: concluído
- `tests/test_memory_consolidation_schedule.py`: Kernel, kill-switches,
  opt-in, budget diário, tick sem Kanban, falha→backoff→sucesso.
- Gates do plano 065 verdes.
