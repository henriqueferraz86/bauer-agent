# ADR 001 — Manter `bauer_native` como fallback permanente configurável

**Status:** aceito
**Data:** 2026-11-03
**Contexto:** plano 062 ("Agno como motor de execução padrão do turno"),
consolidado no sprint 39.

## Decisão

`bauer_native` (o executor histórico de turno do Bauer Agent, sem o
adapter Agno) continua existindo como alternativa configurável de
primeira classe ao lado de `agno`, selecionável via o mesmo toggle único
(`runtime_mode`, exposto em `/api/runtime/mode` e no painel desktop
`Runtime.tsx`). Não há depreciação planejada, nem prazo, nem sprint
futura dedicada a removê-lo.

## Motivação

O plano 062 nunca decidiu formalmente aposentar o `bauer_native` — a
intenção original era apenas tornar Agno o *padrão*, não o único motor.
Observado ao longo dos sprints 35-38:

- Nenhuma regressão de comportamento ou cobertura foi atribuída à
  manutenção dos dois caminhos lado a lado; a suíte hermética cobre
  ambos continuamente.
- `bauer_native` já funciona como fallback real, não apenas teórico: é o
  default de fábrica (`_state["runtime_mode"] = "bauer_native"` em
  `bauer/server.py`), o único caminho usado pelo CLI antes do sprint 37 e
  ainda o default do CLI (`runtime.cli_default_adapter`), e continua
  sendo exercitado em paralelo pela suíte de testes a cada sprint que
  tocou `/chat`, `/stream` e agora `/v1/chat/completions`.
- Os harnesses de comparação (sprint 35/36) mediram custo e qualidade
  praticamente equivalentes entre os dois executores — não há vantagem
  técnica unilateral que justifique remover o nativo.
- Manter os dois caminhos dá ao operador uma saída operacional imediata
  (reverter via painel ou `POST /api/runtime/mode`) caso o adapter Agno
  ou uma dependência externa (SDK, provider) apresente um problema em
  produção, sem precisar reverter código.

## Consequências

- Todo sprint que adicionar comportamento ao caminho `agno` de um
  endpoint (`/chat`, `/stream`, `/v1/chat/completions`) deve continuar
  verificando paridade com o caminho nativo existente, como já é a
  prática desde o sprint 38 (ex.: `set_policy_approval_wait`,
  `ctx.add_assistant`).
- Nenhuma tarefa futura deve assumir que `bauer_native` será removido;
  mudanças que dependam disso devem ser propostas e decididas
  explicitamente, não assumidas a partir do nome do plano 062.
- O código de ambos os caminhos (`run_one_turn_with_fallback` nativo e
  `_run_agno_turn`/`AgnoRuntimeAdapter`) permanece sob manutenção normal,
  incluindo testes.
