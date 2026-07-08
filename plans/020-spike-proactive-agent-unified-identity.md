# Plan 020 (SPIKE): Agente proativo (briefing/alertas iniciados pelo Bauer) + identidade unificada entre canais

> **Executor instructions**: Plano de DESIGN/SPIKE — entregável é spec, não
> código. São duas iniciativas do pilar conectividade; avalie ambas, veredito
> honesto em cada. Responda às perguntas com `file:line`, pare nos STOP
> conditions. Ao concluir, atualize `plans/README.md`.
>
> **Drift check (run first)**: `git log --oneline -5 -- bauer/gateway_outbox.py bauer/channel_base.py bauer/postiz_client.py`

## Status

- **Priority**: P2
- **Effort**: M para o spike (build: proatividade M, identidade M-L — grosseiro)
- **Risk**: LOW (spike)
- **Depends on**: 014 recomendado antes (compartilham o transporte de
  relatório via outbox; o briefing matinal é irmão do digest do 018)
- **Category**: direction
- **Planned at**: commit `2c9d86f`, 2026-07-07

## Why this matters (a visão 20/10)

Hoje TODOS os canais do Bauer são pergunta→resposta: o humano inicia, o Bauer
responde. Mas as peças de proatividade já existem soltas: outbox durável com
retry (`gateway_outbox`), tool `cronjob` + automation scheduler durável
(status: implemented), tool `channel_send`, e integrações que PRODUZEM eventos
dignos de notificação — a API do Postiz tem endpoints de analytics
(`/public/v1/analytics/...`, vistos no postiz-agent CLI; nosso
`postiz_client.py` ainda não os implementa) e o kanban/dispatcher sabem quando
tasks concluem ou travam. 20/10 perna 1: o Bauer INICIA conversas — briefing
matinal ("agenda do kanban, custo de ontem, teu post alcançou X"), alertas
("task travada", "CI quebrou"). Perna 2: **identidade unificada** — hoje cada
canal é uma sessão isolada (`session_key` prefixa `tg:`/`dc:`/`sl:` por
chat_id — `channel_base.py:111-114`), então "continua aquilo do terminal" não
funciona no Slack. Unificar a identidade (henrique = CLI = Slack = voz) com
continuidade opcional de contexto é o que transforma cinco bots num único
assistente.

## Current state (verificado)

- `bauer/channel_base.py:111-114` — `ChannelMessage.session_key`:
  `{"telegram": "tg", "discord": "dc", "slack": "sl"}` + chat_id → sessões
  segregadas por canal (por design: histórico por chat).
- `bauer/channel_base.py:137+` — `AgentBackend`: UM pipeline compartilhado,
  sessões por chave no `SqliteSessionStore`; overrides de modelo POR sessão
  (`_model_overrides[session_key]`).
- `bauer/gateway_outbox.py` — fila durável (SQLite) com retry; pump no
  `gateway_runtime._outbox_pump` a cada `outbox_drain_interval_s`.
- `bauer/gateway_channels.py` — registry de canais nomeados
  (`bauer gateway-channel-add alerts telegram <chat_id>`) — o endereçamento de
  notificação JÁ existe.
- Scheduler durável: `docs/architecture/automation-scheduler.yaml`
  (implemented) — dispara jobs como tasks kanban; candidato a disparar o
  briefing.
- `bauer/postiz_client.py` — client HTTP do Postiz SEM os métodos de
  analytics (a API pública tem `/public/v1/analytics/{integrationId}` e
  `/analytics/post/{postId}` — fonte: postiz-agent CLI oficial).
- Identidade de usuário por canal: allowlists por canal
  (`telegram.allowed_users` ints; `slack.allowed_users` "U..."), sem mapa
  "pessoa" unificando-os.

## Investigation steps

**Perna 1 — proatividade:**
1. Trace o caminho completo de uma notificação hoje: `channel_send` →
   `GatewayOutbox.enqueue` → pump → bridge. O que falta para um JOB agendado
   (scheduler) compor uma mensagem RICA (que exige rodar o agente/tools para
   coletar dados — kanban, custo, analytics) e enfileirá-la? (Hipótese: o
   scheduler já enfileira task kanban que o dispatcher executa — a task pode
   ser "monte e envie o briefing"; valide.)
2. Inventário de fontes do briefing: kanban (tasks done/blocked — API do
   `kanban_db`), ledger de custo (016 — se não existir, o briefing v1 mostra
   tokens), Postiz analytics (exige adicionar 2 métodos ao client — dimensione),
   git/CI (fora do escopo v1?).
3. Anti-spam: janela de silêncio, dedup de alertas repetidos, config de
   opt-in por tipo de evento.

**Perna 2 — identidade unificada:**
4. Desenhe o mapa de identidade: config `identities:` ligando
   `{cli: true, slack: ["U0BF8RCSQ57"], telegram: [602936016]}` a uma pessoa.
   Que recursos passam a ser por-PESSOA (memória durável, preferências,
   /model override?) e o que segue por-CANAL (histórico da conversa)?
5. Continuidade de contexto explícita vs automática: fundir históricos
   automaticamente quebra o modelo mental (e o session_key). Alternativa de
   menor risco: comando/detector "continua o que discutimos no terminal" →
   `session_search` (já existe!) busca a sessão relevante e injeta um RESUMO
   dela na sessão atual do canal. Valide viabilidade com a API do
   `SqliteSessionStore.search_sessions` (`sqlite_session_store.py:330`).

## Design deliverable

`docs/architecture/proactive-unified-identity.yaml` (status draft) com as duas
pernas: (1) proatividade — tipos de evento (briefing agendado, alerta de task,
alerta de erro), o produtor de cada um, transporte (scheduler→task→agente→
outbox), formato das mensagens, anti-spam, config (`notifications:` — canais
por tipo, horário do briefing, quiet hours); (2) identidade — schema do mapa
pessoa↔canais, escopo do que unifica (memória/preferências) vs não (histórico),
o fluxo "puxar contexto de outra sessão" via session_search + resumo, e
privacidade (multi-usuário num canal: NUNCA vazar memória de outra pessoa).
Vereditos por perna + fatias de build (proatividade v1 = briefing matinal
fixo com kanban+tokens via scheduler existente; identidade v1 = mapa de
config + memória unificada, SEM fusão de histórico). ≥5 open questions.

## Done criteria

- [ ] `docs/architecture/proactive-unified-identity.yaml` existe (draft)
- [ ] 5 perguntas respondidas com `file:line`
- [ ] Veredito por perna + fatias de build v1
- [ ] Anti-spam e privacidade endereçados explicitamente
- [ ] ≥5 open questions
- [ ] Nenhum código de produção alterado
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

- O automation scheduler não conseguir compor mensagens via agente (só
  enfileirar prompts crus) — a proatividade v1 muda de forma; apresente as
  opções (job dedicado no gateway_runtime vs task kanban).
- Unificar memória exigir mudanças no formato de memória durável com migração
  — pare e apresente o trade-off.
- O design de identidade colidir com multi-tenancy de companies
  (`bauer/company*`) — mapeie a interação antes de propor.
