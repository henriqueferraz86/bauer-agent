# Plano 059 — P4: autonomia persistente com freios verificáveis

> Implementar conforme `specs/sprint-26-p4-guarded-autonomy/`. O plano 056 é
> base, não prova que orçamento e gates estão aplicados ponta a ponta.

## Status

**DONE**

- **Priority**: P4
- **Effort**: L
- **Risk**: HIGH — muda execução autônoma; default continua opt-in e fail-closed.
- **Depends on**: 056 (autopilot persistente)
- **Category**: governança / confiabilidade

## Evidências que motivam o plano

- `AutopilotController._build_budget()` cria um `AutonomousBudget`, mas o
  controlador não consome custo/tools e o relógio/counters reiniciam com o
  processo.
- O dispatcher verificava apenas `Task.parent_id`; SQLite aceita múltiplos
  pais, mas o workspace/claim não provava que todos os predecessores terminaram.
- O caminho Autopilot→dispatcher executa `bauer run`; antes desta sprint,
  `DONE`/exit code zero não carregava evidência durável de que os gates rodaram.
- O roadmap cita tasks `#44/#50` sem referência acionável neste checkout; este
  plano substitui essas referências por critérios verificáveis.

## Progresso implementado

- Receipt de gate é cunhado pelo dispatcher após conferir o run Kernel persistido,
  IDs de task/claim/dispatcher e resultados de todos os gates. Gate ausente,
  reprovado ou inconclusivo bloqueia o sucesso. O caminho do store é consumido
  por `bauer run` antes de expor tools ao worker.
- O uso de cada missão fica nos receipts persistidos das tasks concluídas. O
  dispatcher serializa tasks do mesmo goal, calcula saldo de custo/tools/tempo,
  passa custo e tools restantes a `bauer run` e aplica o tempo restante como
  timeout. Usage ausente com teto ativo bloqueia sem retry automático.
- Métricas do executor são persistidas antes de gates/desfechos de falha; a
  duração usa relógio monotônico desde o início efetivo da execução, incluindo
  retries/replans e gates, mas excluindo espera anterior em fila/aprovação.
- Dependências do planner são validadas (IDs, ausências, autorreferência e
  ciclos), materializadas em ordem topológica e reconciliadas sem duplicar
  links. SQLite mantém todos os pais; Markdown falha fechado com múltiplos pais.
  No SQLite, a task fica `TODO` sob o lock compartilhado até as arestas serem
  persistidas, evitando claim antes da dependência.

Validação final concluída: suíte integral, Ruff bloqueante/amplo e
`git diff --check` passaram; revisão independente confirmou as lacunas de
auditoria e a implementação agora cobre falhas, duração das gates e espera
pré-execução. A execução continua opt-in e sem autoaprovação/auto-merge.

## Ordem e condições de parada

1. Exigir gate verificável para uma missão antes de reconciliá-la como concluída.
2. Persistir consumo agregado e aplicar budgets no limite de admissão/execução.
3. Fazer a elegibilidade do dispatcher/autopilot respeitar todos os
   predecessores, inclusive após restart.

STOP se fechar um gate exigir contornar a custódia do Kernel, se não houver
fonte confiável de custo/uso por task, ou se a solução de budget/dependências
exigir migração destrutiva. Registre evidência e mantenha a capacidade opt-in.

## Critérios de aceite

- Execução supervisionada mantém compatibilidade; execução explicitamente
  autônoma só conclui com o gate determinístico exigido aprovado.
- Budget de missão não reinicia em restart e impede novas admissões ao atingir
  qualquer limite efetivo de custo, tempo ou ferramentas.
- Custo desconhecido não é interpretado como custo zero em configuração que
  exige teto financeiro.
- Uma task só é elegível quando todos os seus predecessores estão `DONE`;
  failed/blocked/missing não libera descendants.
- Não há geração autônoma ilimitada de metas, auto-aprovação ou auto-merge.
- Suíte, Ruff, gate de arquitetura/custódia e diff-check passam.

## Validação

`uv sync --frozen --extra dev`; testes focados de Kernel/autopilot/budget/
dispatcher; `uv run pytest tests/ -q --tb=short`; os dois comandos Ruff do
`AGENTS.md`; `git diff --check`.
