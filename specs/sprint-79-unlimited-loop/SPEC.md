# SPEC — Sprint 79: sem limite de iterações nem de custo

## Pedido

2026-11-12, depois de um /loop da T0011 (`run-bc2b4555…`) terminar com
`"output": "[Limite de iterações atingido]"`: "resolva definitivamente, não
quero limite de interações nem de custo".

## Causa

O loop rodou 13 min e executou 205 tools, até esgotar um teto que não está
no config: `AutonomousBudget.max_llm_calls = 200`. O loop só passava tempo,
ferramentas e custo, e herdava os padrões de 200 chamadas ao modelo e de
500 mil tokens de saída. Havia ainda mais quatro tetos:

| Teto | Onde | Antes |
|---|---|---|
| chamadas ao modelo, tokens de saída | `AutonomousBudget` (fixo) | 200 e 500 mil |
| rodadas do loop | `serve_loop.MAX_ROUNDS_HARD_CAP` (fixo) | 100 |
| iterações por turno de conversa | `tools.max_tool_turns` | 150, aplicado só no `bauer agent`; no gateway e no serve ficava fixo no código |
| tempo, ferramentas, custo do loop | `loop:` | sem opção "sem limite" (e `max_cost_usd: 0` virava US$ 2) |
| orçamento diário, ferramentas e tempo por run | perfil do Kernel | o diário não aceitava "sem limite" |

## Comportamento

- O orçamento do loop (`loop_budget`, `ResolvedLoopLimits.budget()`) não tem
  mais teto de chamadas ao modelo nem de tokens. Os três loops (Telegram,
  web, CLI) usam a mesma fábrica.
- `loop.max_minutes`, `loop.max_tool_calls`, `loop.max_cost_usd` e o novo
  `loop.max_rounds` aceitam `0` = sem limite. `max_rounds` tem padrão 0. Um
  cliente HTTP ainda pode pedir limites **menores**.
- `tools.max_tool_turns: 0` = sem teto por turno. O gateway e o serve agora
  aplicam o valor do config.
- `AutonomyProfile.daily_budget_usd` aceita `None` = sem limite.
  `max_tool_calls_per_run: 0` e `max_runtime_s_per_run: 0` já significavam
  sem limite.
- Internamente "sem limite" é infinito. Para fora (JSON, painel), vira
  `None`, porque JSON não tem `Infinity`.

## O que continua valendo

`/cancelar`, kill-switch, aprovação de ações de risco, detector de loop
(chamadas idênticas repetidas) e o prazo de turno de conversa do Telegram
(`BAUER_GATEWAY_TURN_TIMEOUT`). Trabalho longo vai para o /loop, que não tem
prazo quando `loop.max_minutes: 0`.
