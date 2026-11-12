# SPEC — Sprint 80: sem teto de tool calls por sessão

## Pedido

2026-11-12, depois de uma auditoria da implementação CVM terminar com "a
sessão atingiu o limite de chamadas de ferramentas". Continua o pedido da
sprint 79: "não quero limite de interações nem de custo".

## Causa

A sprint 79 tirou os tetos do loop e do turno, mas sobrou o teto por sessão
do `ToolRouter` (LIMITS-001): `tools.max_tool_calls`, padrão 500, e um `500`
fixo no `AgnoAdapter`, o executor padrão. O contador só zera em
`reset_call_count()`, que ninguém chama. Por isso uma sessão longa de canal ou
de serve soma as chamadas de todos os turnos e, depois de 500, toda tool falha
com `Limite de 500 chamadas de tool por sessão atingido`.

## Comportamento

- `tools.max_tool_calls` aceita `0` = sem limite, e esse passa a ser o padrão.
- `ToolRouter(max_tool_calls=0|None)` = sem limite (padrão). Um valor positivo
  continua valendo como teto explícito.
- O `AgnoAdapter` lê `max_tool_calls` do `adapter_config` (padrão 0) em vez do
  500 fixo.

## O que continua valendo

O mesmo que na sprint 79: `/cancelar`, kill-switch, aprovação de ações de
risco, detector de loop e o prazo de turno do Telegram.
