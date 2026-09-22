# SPEC — Sprint 10: Agno Team Orchestrator

## Problema

O Bauer possui `AgentSpec`, `TeamSpec` e delegação governada, mas o caminho
formal de times apenas enfileira uma run. O Agno está disponível como adapter
de agente individual, porém não coordena os membros de um time.

## Objetivo

Fazer o Bauer Kernel executar times formais por padrão através de um
orquestrador Agno em modo `coordinate`, mantendo no Bauer a autoridade sobre
admissão, policy, orçamento, eventos, persistência e isolamento.

## Escopo

- Construir agentes Agno a partir dos `AgentSpec` dos membros do `TeamSpec`.
- Construir um `agno.team.Team` com o supervisor como líder.
- Executar o time pelo caminho governado do Kernel.
- Propagar streaming, sessão, run id, ferramentas e eventos do Bauer.
- Aplicar orçamento diário e limite de paralelismo do time.
- Tornar o adapter Agno o padrão para agents formais quando o SDK estiver disponível.
- Manter `delegate_task` como compatibilidade para especialistas legados.
- Adicionar CLI para executar uma tarefa pelo time.

## Fora de escopo

- AgentOS HTTP separado.
- Times aninhados.
- Alterar o comportamento do `delegate_task` legado.
- Permitir que membros contornem policy ou o Kernel.

## Skills obrigatórias

- spec-driven-project-setup
- security-review
- test-strategy

## Sub-agents recomendados

- backend-implementer
- test-engineer
- security-reviewer
- code-reviewer

## Requisitos funcionais

1. `TeamSpec` deve resolver todos os membros no `RuntimeAgentRegistry`.
2. O supervisor do time deve ser o líder do `agno.team.Team`.
3. O modo padrão deve ser `coordinate`.
4. A execução deve passar pelo Kernel e produzir run/eventos auditáveis.
5. O limite diário do time deve ser verificado antes da execução.
6. O limite de execuções concorrentes deve ser aplicado por time.
7. O resultado deve conter resposta final, membros usados e custo registrado.
8. Falta do SDK Agno deve produzir erro operacional claro, sem fallback silencioso.

## Critérios de aceite

- `bauer runtime teams run bauer.software_team "..."` executa pelo Kernel e Agno.
- O time padrão é formado por Product, Dev, QA e DevOps.
- Uma delegação fora do time, sem supervisor ou acima do orçamento é negada.
- Duas execuções acima de `max_parallel_runs` ficam bloqueadas ou enfileiradas.
- Testes unitários cobrem montagem do Team, policy, orçamento e resultado.
- Teste de integração usa um modelo Agno determinístico, sem credencial externa.
- `uv run pytest tests/ -q --tb=short` e os gates Ruff passam.

## Plano de validação

1. Testes unitários do builder do time.
2. Testes da integração Agno com modelo offline.
3. Testes de policy, orçamento e concorrência.
4. Testes da CLI e do Kernel.
5. Suíte completa e Ruff bloqueante.
6. Smoke test na Beelink com o time padrão.
