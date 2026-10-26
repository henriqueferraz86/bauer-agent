# BACKLOG — Sprint 35: spike de cancelamento no Agno

- [x] Criar contrato, arquitetura, escopo e plano de validação antes do código.
- [x] Confirmar versão instalada/fixada do Agno: 3.0.10 em `uv.lock` e no
  ambiente frozen.
- [x] Implementar probe dinâmico hermético no seam sync usado pelo adapter;
  Agent real + modelo fake comprovam `ExecutionControl` e sessão na tool.
- [x] Inspecionar dispatch sync de Agent/Team, streams e execução de tools;
  APIs `run(stream=True)` usam streams sync e o Team mantém membros
  sequenciais durante streaming.
- [x] Registrar evidência, limitações e decisão: avançar; `arun()` e tarefas
  auxiliares em background ficam fora da garantia atual.
- [x] Adicionar checkpoint antes/depois de avançar cada event do stream Agno.
- [x] Mapear cancelamento de tool para `RunCancelledException` e manter
  `run.cancelled` nos agregadores de Agent e Team.
- [x] Cobrir contexto real no Agent, cancelamento entre events, cancelamento
  durante espera simulada de aprovação dentro da tool router e resultado
  terminal do Team.
- [x] Revisão independente e correção dos achados P1/P2: checkpoint inicial
  também usa o sinal terminal do Agno; probe usa ToolRouter; Team prova que o
  segundo event não avança.
- [x] Gates completos do `AGENTS.md`: `uv sync --frozen --extra dev`, suíte
  `pytest tests/ -q --tb=short` e os dois seletores Ruff passaram.
- [ ] Métricas parciais Agno/custo: dependência explícita da Sprint 36 antes de
  migrar qualquer canal (uso não faz parte do contrato atual da Sprint 35).
