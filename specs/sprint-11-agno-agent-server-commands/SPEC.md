# Sprint 11 — Teams no Agent e no Server

## Objetivo

Disponibilizar o orquestrador Agno default nos dois pontos de uso do Bauer:
o `bauer agent` interativo e o frontend servido por `bauer serve`. O usuário
deve conseguir listar times, consultar membros e orçamento, executar uma tarefa
governada, acompanhar eventos e cancelar uma execução sem contornar o Bauer
Kernel.

## Escopo

- Slash commands `/teams` e `/team ...` no agente interativo.
- API autenticada do desktop/server para times, runs, eventos e cancelamento.
- Tela Teams no frontend com seleção do time, tarefa, resultado, status,
  orçamento e timeline.
- Reuso de `AgnoTeamOrchestrator`, `TeamRegistry`, `RunManager` e `EventBus`.
- Testes unitários, contrato HTTP, build do frontend e smoke offline.

## Fora de escopo

- Novo tipo de agente ou time.
- Alteração do protocolo Agno ou execução de provider real nos testes.
- Permitir execução sem as políticas e gates do Kernel.
- Configuração automática de allowlist ou mudança de permissões de tools.

## Critérios de aceite

1. `/teams` lista `bauer.software_team` e seus quatro membros.
2. `/team show bauer.software_team` mostra coordenação e limites.
3. `/team run bauer.software_team <tarefa>` executa pelo
   `AgnoTeamOrchestrator`, exibindo run id, status e saída.
4. O frontend lista os times e permite executar, atualizar e cancelar runs,
   mostrando eventos persistidos por membro.
5. A API rejeita time inexistente e corpo sem tarefa com respostas 4xx.
6. A rota de execução não chama Agno diretamente: usa a camada runtime e o
   Kernel existente.
7. Testes Python, lint crítico, typecheck e build do frontend passam.

## Validação

- `uv sync --frozen --extra dev`
- testes direcionados e `uv run pytest tests/ -q --tb=short`
- Ruff crítico e informativo
- `uv run mypy bauer/`
- `npm run build` em `desktop/`
- smoke offline no Windows e na Beelink Linux com `bauer runtime teams list`
  e chamada da API usando adapter fake.
