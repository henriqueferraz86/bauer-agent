# SPEC — Sprint 26: autonomia P4 com freios verificáveis

## Problema

O autopilot persistente já materializa e reconcilia tarefas, mas o orçamento
local não contabiliza o trabalho dos workers nem sobrevive como consumo
acumulado. A conclusão do objetivo confia no estado `DONE` das tarefas sem
confirmar o gate determinístico exigido. A superfície do Kanban admite DAGs
com múltiplos pais, mas os caminhos de workspace/dispatcher não mantêm a mesma
garantia em todo claim.

## Objetivo

Fechar as lacunas do P4 no caminho real de autonomia persistente: conclusão
governada, orçamento agregado e dependências entre marcos. O opt-in existente
continua necessário; esta sprint não transforma o Bauer em agente autônomo
aberto.

## Escopo

- Aplicar um contrato verificável de gate ao objetivo/tarefas autônomas e
  impedir conclusão quando o gate falha, é inconclusivo ou não está disponível.
- Persistir o consumo agregado de budget relevante através das tasks e de
  reinícios do supervisor, reutilizando ledger/runtime stores existentes onde
  possível.
- Efetivar limites de custo, duração e ferramentas no caminho de admissão e
  worker, usando a fonte real de execução; custo desconhecido não pode fingir
  ser zero quando há teto de custo ativo.
- Preservar e fazer cumprir todos os predecessores do DAG durante planning,
  materialização e dispatch, inclusive para backend SQLite e Markdown.
- Atualizar runbook e roadmap com evidência e limitações residuais.

## Fora de escopo

- Gerar metas sem declaração/aprovação do operador.
- Autoaprovar ações, ampliar allowlists, remover worktrees ou fazer merge.
- Reimplementar Kernel, BudgetManager, AutonomyBudget ou o DAG do orchestrator.
- Tornar todos os gates globalmente fail-closed em caminhos manuais/supervisionados.
- Migração destrutiva de tarefas existentes ou mudança silenciosa de status.

## Skills obrigatórias

- spec-driven-project-setup
- security-review
- test-strategy
- python-service-pattern

## Sub-agents recomendados

- spec-architect: verificar contrato e fronteiras do Kernel;
- backend-implementer: integração autopilot/dispatcher por fatia;
- security-reviewer: falha de gate, custo desconhecido, modos de autonomia;
- test-engineer: restart, múltiplos pais, budget no limite e regressões;
- code-reviewer: custódia, idempotência e compatibilidade Markdown/SQLite.

## Requisitos funcionais

1. Missão autônoma não pode ser marcada concluída sem validação determinística
   identificável e aprovada; falha/inconclusividade deixa run/goal não terminal
   de sucesso e fornece razão auditável.
2. Orçamento não pode ser resetado por restart do controller e seu consumo
   corresponde ao trabalho de fato executado para os objetivos daquela missão.
3. Budget esgotado impede materialização/admissão de nova task; custo não
   disponível segue política explícita e segura.
4. Dependências DAG não liberam task enquanto qualquer predecessor estiver em
   estado diferente de `DONE`, incluindo caminho direto de claim e `dry_run`.
5. Markdown (pai único) mantém compatibilidade; SQLite respeita múltiplos pais.
6. Tudo permanece opt-in e sem novos efeitos externos implícitos.

## Critérios de aceite

- [x] Teste de integração: receipt ausente/reprovado/inconclusivo não conclui goal; gate
  aprovado permite conclusão.
- [x] Budget acumula entre pelo menos duas tasks, persiste após restart e bloqueia
  no limite; custo desconhecido não é tratado como zero quando cap está ativo.
- [x] Teste de dispatcher com dois pais (um `DONE`, outro não) impede claim e
  `dry_run`; após ambos `DONE`, task fica elegível.
- [x] Estados `FAILED`, `BLOCKED` e dependência ausente não são promovidos.
- [x] Suíte completa, Ruff, gates de arquitetura/custódia e diff-check passam.
- [x] Documentação distingue capacidades já existentes das garantias novas.
- [x] Métricas de execução que precedem gate/falha são auditáveis; duração
  inclui gates sem contar espera anterior à execução.

## Plano de validação

1. `uv sync --frozen --extra dev`
2. Testes direcionados para Kernel, autopilot, budget ledger, kanban e dispatcher.
3. `uv run pytest tests/ -q --tb=short`
4. Ruff bloqueante e amplo conforme `AGENTS.md`.
5. Validar execução no workspace limpo temporário e compatibilidade em Windows.
