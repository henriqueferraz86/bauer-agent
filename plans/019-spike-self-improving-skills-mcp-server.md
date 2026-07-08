# Plan 019 (SPIKE): Skills que se refinam por telemetria + Bauer como servidor MCP

> **Executor instructions**: Plano de DESIGN/SPIKE — entregável é spec, não
> código. São DUAS iniciativas irmãs do pilar extensibilidade; o spike avalia
> ambas e pode recomendar descartar uma (verdicts honestos > empolgação).
> Responda às perguntas com `file:line`, pare nos STOP conditions. Ao concluir,
> atualize `plans/README.md`.
>
> **Drift check (run first)**: `git log --oneline -5 -- bauer/tools/skills.py bauer/tools/mcp.py bauer/server.py`

## Status

- **Priority**: P3
- **Effort**: M para o spike (build M por iniciativa — grosseiro)
- **Risk**: LOW (spike)
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `2c9d86f`, 2026-07-07

## Why this matters (a visão 20/10)

Duas assimetrias evidentes no pilar de extensibilidade:

1. **Telemetria de skills que só observa**: o README declara textualmente que
   `bauer skills-hub stats` coleta "telemetria de uso (quais disparam,
   desfecho, 👍/👎)" e que "a telemetria é só observação (não age) — base para
   refinar skills por uso real". A base existe; o refino nunca foi construído.
   20/10: skills com 👎 recorrente são rebaixadas/aposentadas automaticamente
   (deixam de auto-injetar), padrões de sucesso repetidos geram RASCUNHO de
   skill nova que o usuário aprova — o catálogo passa a evoluir com o uso.

2. **MCP unidirecional**: o Bauer CONSOME servidores MCP (tool `mcp_call`,
   `bauer/tools/mcp.py`, config `mcp.servers`) mas não se EXPÕE como um.
   O Bauer tem ~75 tools maduras (fs sandboxed, web, browser, kanban, social,
   memória) atrás de um ToolRouter com política de segurança — servi-las via
   MCP tornaria o Bauer um backend de tools para Claude Code, Cursor e
   qualquer host MCP, com custo marginal (o `serve` FastAPI já existe; o
   ToolRouter já tem schemas por tool).

## Current state (verificado)

- README (seção Skills): catálogo built-in + `~/.bauer/skills`, auto-inject
  por confiança ("Na dúvida, não injeta"), toggle `agent.skill_auto_inject`,
  telemetria via `bauer skills-hub stats`.
- `bauer/tools/skills.py` + `bauer/skills*` — mixin de skills
  (skills_list/skill_view/skill_manage); localize o módulo da telemetria
  (`grep -rn "skills_hub\|skill_stats\|telemetr" bauer/ --include="*.py" -l`).
- `bauer/tools/mcp.py` — cliente MCP via stdio (`mcp_call`), requer
  `pip install mcp`; config em `McpSection` (config_loader).
- `bauer/tool_router.py` — `get_tool_schemas()` (~:1518) exporta schemas
  OpenAI-style por tool; `_TOOL_SECURITY` com permission/risk/approval;
  `tool_allowlist` para expor subconjunto (`__init__:379`).
- `bauer/server.py` — FastAPI com auth por API key; endpoint `/tools` já lista
  tools (autenticado).
- O SDK Python de MCP (`mcp`) suporta servir via stdio e streamable HTTP —
  o spike confirma a versão e o transporte adequado.

## Investigation steps

**Iniciativa A — skills que se refinam:**
1. Ache o store da telemetria (arquivo/tabela; campos gravados por disparo:
   skill, matched?, desfecho, 👍/👎?). O sinal atual sustenta um score por
   skill? O que falta gravar?
2. Onde o auto-inject decide (o "match com confiança")? Um score dinâmico
   (sucesso/fracasso acumulado) entra nessa decisão como multiplicador sem
   refactor grande?
3. Desenhe o ciclo de rebaixamento (skill abaixo de score X → para de
   auto-injetar → notifica usuário) e o de geração (o que define "padrão
   repetido com sucesso"? sessões com sequência similar de tools + 👍? isso é
   minerável do session store ou precisa de novo sinal?). Seja honesto se a
   geração automática for prematura — rebaixamento sozinho já vale.

**Iniciativa B — servidor MCP:**
4. Mapeie tool → MCP: `get_tool_schemas()` produz JSON schema compatível com
   `tools/list` do MCP? O que se perde (aprovação interativa! tools com
   `approval: True` não podem simplesmente rodar sob um host remoto — política:
   expor só risk low/medium? exigir tool_allowlist explícita?).
5. Transporte e ciclo de vida: processo novo `bauer mcp-serve` (stdio, padrão
   dos hosts) vs montar no `serve` HTTP existente? Como workspace/sandbox é
   escolhido por conexão? Auth?

## Design deliverable

`docs/architecture/skills-refinement-mcp-server.yaml` (status draft) com as
duas iniciativas em seções separadas, cada uma com: design, fatias de build,
e um VEREDITO (fazer / adiar / descartar) com justificativa. Para A: schema do
score, regra de rebaixamento, UX de notificação, e a decisão explícita sobre
geração automática (provável: adiar). Para B: superfície exposta (allowlist
default conservadora — só tools risk low sem approval), transporte
recomendado, mapa schema→MCP, e o tratamento de aprovação (negar tools
gated? callback?). ≥4 open questions por iniciativa.

## Done criteria

- [ ] `docs/architecture/skills-refinement-mcp-server.yaml` existe (draft)
- [ ] 5 perguntas respondidas com `file:line`
- [ ] Veredito explícito por iniciativa (fazer/adiar/descartar + por quê)
- [ ] Fatias de build para o que for "fazer"
- [ ] ≥4 open questions por iniciativa
- [ ] Nenhum código de produção alterado
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

- A telemetria de skills não existir de fato no código (só no README) — isso
  vira a fatia 0 do build ("construir o sinal antes do refino"); reporte.
- O SDK `mcp` disponível ser incompatível com expor tools dinamicamente
  (schemas gerados em runtime) — reporte a limitação de versão.
- Descobrir que expor tools via MCP contorna o approval flow sem mitigação
  razoável — o veredito da iniciativa B deve ser "adiar até taint/approval
  remoto (018/014) existirem", e isso é um resultado VÁLIDO do spike.
