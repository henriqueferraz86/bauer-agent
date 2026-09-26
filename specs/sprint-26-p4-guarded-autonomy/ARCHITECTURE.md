# ARCHITECTURE — Sprint 26: autonomia P4 com freios verificáveis

## Visão geral

Reusar o Kernel, o `BudgetManager` persistente e os relacionamentos DAG já
existentes. O controlador de autonomia não deve executar LLM/tools por fora:
ele admite/plana/materializa tasks; o dispatcher valida elegibilidade; a
execução e seus gates permanecem sob o caminho governado já existente.

## Componentes

- `AutopilotController`: estado da missão, orçamento agregado e reconciliação.
- `GoalTracker`: estado durável de metas/marcos, checkpoints e idempotência.
- Workspace manager / Kanban SQLite: persistência de predecessores.
- `TaskDispatcher`: gate final de dependências antes do claim/worker.
- `RunEntry`/Kernel/Evaluator/BudgetManager: autoridade de execução, budget e
  gate de conclusão; não criar um executor paralelo.
- `RuntimeSupervisor`: lifecycle/status/kill-switch, mantendo opt-in.

## Fluxo

```text
missão declarada → planner → marcos/tasks + predecessors → budget admission
   → dispatcher (todos predecessores DONE?) → Kernel/worker → quality gate
   → atualizar consumo persistente → reconciliar objetivo
```

Qualquer gate obrigatório falho/inconclusivo ou budget indisponível sob cap
ativo bloqueia avanço e deixa razão redigida/auditável. Nunca converte falha em
DONE.

## Decisões

- Dependências são condições para despacho, não apenas metadata/UI.
- Budget agregado é autoridade além dos limites locais de cada processo; os
  contadores devem derivar de registros reais e usar atualização atômica.
- Usar IDs estáveis de objetivo/task/run para deduplicar consumo em restart.
- Gate obrigatório é declarado por contrato/capacidade e testado; não mudar o
  comportamento global dos runs manuais.
- Se o dispatcher não puder confirmar todos os predecessores, falha fechada.

## Alternativas rejeitadas

- Somar saídas de texto do worker: incompleto e sujeito a parsing não confiável.
- Zerar `AutonomousBudget` no restart: torna o limite contornável.
- Usar somente `parent_id` em backend que suporta múltiplos predecessores.
- Marcar goal `DONE` pelo status do Kanban sem consultar evidência de gate.
- Reescrever Kernel/RunEntry em um executor específico do autopilot.

## Riscos

- Metadados de custo/usage podem não estar disponíveis para todos os providers.
- Run antigo pode não possuir gate de validação; requer migração compatível ou
  bloqueio explicitamente visível, não aprovação implícita.
- Claims concurrentes exigem reserva transacional para impedir overspend.
- Mudanças na relação pai/filho devem preservar instalações Markdown legadas.

## Observabilidade

Expor estado e limites usados/restantes, IDs correlacionáveis de goal/task/run,
razão de gate/budget e eventos redigidos. Nunca expor prompts, argumentos de
tools, respostas, chaves ou conteúdo privado no evento/status.
