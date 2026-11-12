# SPEC — Sprint 81: custo real e sem "ponto de reflexão" no /loop do Telegram

## Pedido

2026-11-12: "os últimos runs dão como concluído mas com custo 0 e as tasks
ainda estão em in progress; investigue todo o fluxo de Telegram vs Bauer".

## O que a investigação achou

Runs `channel.telegram.loop` do dia (ex.: `run-bc9f0c26`, 1.093 tools, 84 min;
`run-54805b79`, 68 tools, 3 min): todos com `llm_calls_count: 0`,
`cost_estimate: null` e status `completed`.

| Sintoma | Causa |
|---|---|
| Custo 0 e 0 chamadas ao modelo | O executor do /loop do Telegram devolvia ao Kernel só `output` e `tool_calls_count`. Nenhum medidor de custo era instalado, então `loop.max_cost_usd` também nunca era aplicado ali. O /loop da web mede. |
| Loop desiste em poucos minutos | A cada 6 tools o turno injeta o "ponto de reflexão" ("execute UM passo ou responda agora"), inclusive no /loop autônomo. No `run-54805b79` ele apareceu em 48, 54 e 60 tools e o modelo encerrou. |
| `completed` com a tarefa aberta | `completed` é o ciclo de vida do run: o modelo parou sozinho. Os gates passam sem validar: o workspace do projeto não é repo git ("sem diff a escanear", "nenhuma mudança — nada a testar"). **Não corrigido aqui.** |
| Tasks em in_progress | O agente não fecha task sem teste verde, e no container não há como rodar os testes do projeto: o `.venv` é do host (Python 3.14, symlink para `/usr/bin/python3`) e o Python do container não tem `pytest`. Ambiente, não código do Bauer. |

## Comportamento

- O /loop do Telegram instala um `TurnMetrics` por invocação do executor e
  devolve `cost_estimate`, `llm_calls_count` e `usage_known` ao Kernel. O
  custo é sincronizado com o orçamento a cada rodada, então
  `loop.max_cost_usd` passa a valer. O relatório final mostra o custo.
- `maybe_reflect` não injeta o ponto de reflexão quando o turno roda com o
  orçamento compartilhado do loop (`remaining_tool_calls`), nos caminhos do
  canal, do serve e da CLI. Conversa normal não muda. O detector de chamadas
  repetidas continua valendo nos dois modos.

## Fora do escopo

- Gates de validação em workspace sem git.
- Distinguir "loop encerrou" de "objetivo cumprido" no status do run.
