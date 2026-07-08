# Plan 018 (SPIKE): Taint tracking de conteúdo não-confiável + relatório de prestação de contas

> **Executor instructions**: Plano de DESIGN/SPIKE — entregável é spec, não
> código. Responda às perguntas com `file:line`, pare nos STOP conditions.
> Ao concluir, atualize `plans/README.md`.
>
> **Drift check (run first)**: `git log --oneline -5 -- bauer/channel_base.py bauer/tool_router.py bauer/audit_logger.py`

## Status

- **Priority**: P1 (segurança; pré-condição de confiança para o daemon 014)
- **Effort**: M para o spike (build M por fase — grosseiro)
- **Risk**: LOW (spike)
- **Depends on**: none (o build da parte de relatório conversa com 014)
- **Category**: direction / security
- **Planned at**: commit `2c9d86f`, 2026-07-07

## Why this matters (a visão 20/10)

A superfície do Bauer mudou de natureza nas últimas semanas: ele agora LÊ
conteúdo de origens não-confiáveis — mensagens de Slack/Telegram (gateway),
páginas web (`web_fetch`), transcrição de voz — e tem acesso a shell, arquivos
e publicação em redes sociais. Prompt injection deixa de ser teórico: uma
mensagem num canal ou uma página buscada pode instruir o modelo a exfiltrar um
arquivo ou rodar um comando. As defesas atuais (allowlist de comando, G4 LLM
approval, secrets scanner no output) são **cegas à origem**: uma tool call
disparada por conteúdo de terceiros é tratada igual a uma digitada pelo dono.
20/10 tem duas pernas: (1) **taint tracking** — cada mensagem carrega a origem
(owner-CLI / canal-allowlisted / conteúdo-de-web / terceiro) e tools
privilegiadas exigem escalonamento quando o gatilho é tainted; (2) **prestação
de contas** — o `audit_logger` já grava tudo por sessão; transformá-lo num
**relatório diário via Slack** ("o que fiz: N tasks, X comandos aprovados, Y
custo") fecha o ciclo de confiança que autonomia 24/7 exige.

## Current state (verificado)

- `bauer/channel_base.py:100-114` — `ChannelMessage` (envelope inbound
  normalizado: channel, user_id, chat_id, text) — o lugar natural do campo de
  origem; a allowlist de usuário por canal já existe nos bridges
  (`_is_authorized`).
- `bauer/tool_router.py:114-207` — `_TOOL_SECURITY` (permission/risk/approval
  por tool) — o lugar natural da política "tainted → exige aprovação a partir
  de risk X".
- `bauer/tool_router.py:1547-1569` — G4 LLM approval usa `_recent_messages`
  como contexto — hoje sem noção de origem.
- `bauer/llm_approval.py` — julga "a ação bate com a intenção aparente do
  usuário?" — exatamente o julgamento que MELHORA com o rótulo "esta instrução
  veio de conteúdo externo".
- `bauer/audit_logger.py` — audit log por sessão (logs_dir, session_id),
  chamado no `execute()` do tool_router ("SEG-3: audit com medição de tempo",
  `tool_router.py:~1579`).
- `bauer/secrets_scanner.py` — redação de segredos no output de tools (já
  roda em todo execute).
- Saída para relatório: `bauer/gateway_outbox.py` (durável, retry) + tool
  `channel_send`; scheduler durável para o disparo diário
  (`docs/architecture/automation-scheduler.yaml`).
- Contexto de mensagens: `ContextManager.messages` (`bauer/context_manager.py`)
  — mensagens são dicts role/content SEM metadados de origem hoje.

## Investigation steps

1. **Propagação de origem**: trace o caminho gateway→agente
   (`ChannelMessage` → `AgentBackend.process` → `ctx.add_user(text)` em
   `channel_base.py:552`). Onde a origem se perde? O dict de mensagem aceita
   campos extras sem quebrar providers (a API OpenAI ignora chaves
   desconhecidas? ou o metadado deve viver FORA das messages, num side-map do
   ContextManager)?
2. **Tool results como fonte de taint**: um `web_fetch` retorna conteúdo de
   terceiros que entra no contexto como tool result. Marque: quais tools
   produzem conteúdo externo (web_fetch, browser_*, transcribe_audio,
   session_search?) — a lista vira política.
3. **Ponto de decisão**: no `execute()` do tool_router, que informação de
   origem está disponível no momento da checagem de `_TOOL_SECURITY`? O
   `_recent_messages` dá janela suficiente para "o gatilho desta call foi
   tainted"? (Heurística mínima viável: sessão contaminada = qualquer input
   tainted nos últimos N turnos.)
4. **Audit → relatório**: formato atual do audit log (jsonl? por sessão?).
   O que falta para agregá-lo num digest diário (task, tool, custo, resultado,
   aprovações)? Cruze com o design do 014 (mesmo canal de outbox).
5. **Custo/fricção**: qual o falso-positivo aceitável? (Sessões de gateway
   inteiras são "tainted" por definição — dono manda mensagem pelo Slack
   também. A política precisa diferenciar REMETENTE allowlisted de CONTEÚDO
   citado/buscado.)

## Design deliverable

`docs/architecture/taint-tracking-accountability.yaml` (status draft)
definindo: os níveis de origem (ex.: `owner`, `allowlisted-channel`,
`external-content`, `unknown`), onde o rótulo vive (side-map no
ContextManager vs campo no dict), a matriz política origem × risk →
(permite / exige aprovação / nega) aplicada em `_TOOL_SECURITY`, o
enriquecimento do prompt do G4 (`llm_approval`) com a origem, o schema do
digest diário (fonte: audit_logger; transporte: outbox; disparo: scheduler),
e plano de build em 3 fatias (1ª: propagar origem + logar no audit SEM
bloquear nada — modo observação; 2ª: digest diário; 3ª: enforcement da matriz
com config de rigor). ≥5 open questions (o enforcement default é observar ou
bloquear? como o usuário marca exceções? interação com o /loop yolo?).

## Done criteria

- [ ] `docs/architecture/taint-tracking-accountability.yaml` existe (draft)
- [ ] 5 perguntas respondidas com `file:line`
- [ ] Matriz origem × risco desenhada
- [ ] Plano de build em 3 fatias (observação primeiro, enforcement por último)
- [ ] ≥5 open questions
- [ ] Nenhum código de produção alterado
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

- Já existir marcação de origem em mensagens (procure
  `grep -rn "origin\|taint\|source.*channel" bauer/context_manager.py bauer/channel_base.py`)
  — mapeie e reporte.
- O side-map de origem exigir refactor do ContextManager além de aditivo
  (mudança no formato persistido de sessões) — pare e apresente o trade-off
  (migração vs campo embutido).
