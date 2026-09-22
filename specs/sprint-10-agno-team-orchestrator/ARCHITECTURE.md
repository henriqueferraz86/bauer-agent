# ARCHITECTURE — Agno Team Orchestrator

## Visão geral

O Kernel continua sendo a fronteira de governança. O novo orquestrador é um
adapter de execução que recebe uma `TeamSpec`, resolve seus `AgentSpec` e
monta um `agno.team.Team` em modo `coordinate`.

```text
mensagem
  → BauerKernel.execute()
    → TeamOrchestrator admission/policy/budget/concurrency
      → Agno Team (supervisor + membros)
        → tools Bauer com contexto de membro
      → resultado e eventos Bauer
```

## Componentes

- `bauer/core/runtime/agno_team_orchestrator.py`: montagem e execução do Team.
- `bauer/core/runtime/team_registry.py`: resolução e validação do TeamSpec.
- `bauer/core/runtime/agent_registry.py`: resolução dos AgentSpec.
- `bauer/core/runtime/adapters/agno_adapter.py`: construção de agentes Agno e
  mapeamento de tools.
- `bauer/core/kernel/`: admissão, policy, orçamento e custódia da run.
- `bauer/commands/runtime_cmd.py`: comando `runtime teams run`.
- `bauer/data/team_specs/`: composição padrão do time.

## Decisões técnicas

1. O Agno coordena apenas depois da admissão do Kernel.
2. O supervisor recebe `mode=coordinate`; os membros recebem seus próprios
   instructions, model, tools e contexto de execução.
3. O Agno não grava a fonte de verdade da run: o Bauer persiste o resultado e
   os eventos; o SQLite do Agno mantém apenas o histórico interno necessário.
4. O semáforo de concorrência é por `team_id` e respeita
   `limits.max_parallel_runs`.
5. O builder usa imports tardios para permitir que o diagnóstico informe a
   dependência ausente, mas a configuração padrão exige o SDK.
6. A integração usa uma factory de modelo e tools para que os testes usem um
   modelo offline determinístico.

## Segurança

- Nenhum membro recebe tools além das declaradas no AgentSpec.
- O `tool_context` de cada membro continua sendo aplicado pelo ToolRouter.
- Policy e orçamento são verificados antes da chamada ao Agno.
- Falha de membro, timeout ou erro do SDK termina a run de forma auditável.
- Chaves e prompts não entram em logs de evento.

## Observabilidade

- `team.run.started`, `team.member.started`, `team.member.completed`,
  `team.run.completed` e `team.run.failed`.
- `run_id`, `team_id`, `agent_id`, duração e custo agregado nos metadados.
- O output público contém a síntese do supervisor e um resumo dos membros.
