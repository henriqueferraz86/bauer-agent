# Plan 014 (SPIKE): Funcionário 24/7 — fechar o circuito daemon → /loop → aprovação/relatório via gateway

> **Executor instructions**: Este é um plano de DESIGN/SPIKE, não de build.
> O entregável é um documento de arquitetura + gap analysis + protótipo fino
> opcional — NÃO implemente o sistema completo. Siga os passos, responda às
> perguntas de investigação com evidência (`file:line`), e escreva o design.
> Se algo em "STOP conditions" ocorrer, pare e reporte. Ao concluir, atualize
> a linha deste plano em `plans/README.md`.
>
> **Drift check (run first)**: `git log --oneline -5 -- bauer/commands/daemon_cmd.py bauer/task_dispatcher.py bauer/gateway_runtime.py`
> Se esses arquivos mudaram muito desde `2c9d86f`, re-derive os fatos de
> "Current state" antes de desenhar.

## Status

- **Priority**: P1 (é a aposta de direção nº 1 do projeto)
- **Effort**: M para o spike (o build depois é L — estimativa grosseira, é spike)
- **Risk**: LOW (spike não toca código de produção)
- **Depends on**: none (mas o build resultante deve aguardar 007/008 — o daemon
  amplia a superfície de autonomia; segurança primeiro)
- **Category**: direction
- **Planned at**: commit `2c9d86f`, 2026-07-07

## Why this matters (a visão 20/10)

O Bauer tem todos os órgãos de um agente autônomo 24/7, mas eles não formam um
organismo: o kanban tem claim/heartbeat/CAS, o TaskDispatcher tem watchdog e
até `record_daemon_started`, existe um `bauer daemon` (pool de workers), um
automation scheduler (cron → task READY no kanban), o `/loop` roda o agente
sozinho com guardrails de orçamento, e o gateway fala Slack bidirecionalmente.
O que NÃO existe é o circuito fechado: **daemon pega tarefa → executa com
/loop → pede aprovação humana via Slack nos passos de risco → reporta o
resultado no canal → pega a próxima**. Hoje a aprovação é um prompt interativo
no TERMINAL (`_prompt_cmd_decision` em agent.py) — num daemon headless isso
trava ou nega tudo. Fechar esse circuito transforma o Bauer de "algo que você
usa" em "algo que trabalha enquanto você dorme" — o caso de uso exato do
servidor doméstico (Beelink/Ubuntu) planejado pelo mantenedor.

## Current state (órgãos verificados — o spike confirma os detalhes)

| Órgão | Onde | O que já faz |
|---|---|---|
| Daemon de workers | `bauer/commands/daemon_cmd.py` | Typer app "BauerDaemon — pool de workers autonomos que processam tasks do kanban"; start/pid/log em `_daemon_*` helpers |
| Dispatcher | `bauer/task_dispatcher.py:100` | `TaskDispatcher` com `mark_ready`, `heartbeat`, `reclaim_stale`, `detect_crashed_workers`, `watchdog_tick`, `dispatch_once`, `run_claimed_worker`, `record_daemon_started/stopped` |
| Kanban kernel | `bauer/kanban_db.py` | CAS `transition_task` (~:620), `claim_task` (~:649), heartbeat com `claim_lock` (~:684) |
| Swarm | `bauer/kanban_swarm.py` | DAG de papéis (workers → verifier → synthesizer) sobre o kanban; "não executa agentes — isso é do dispatcher" |
| Scheduler | `docs/architecture/automation-scheduler.yaml` (status: implemented) | cron durável → enfileira jobs como tasks READY; store em `.bauer_automation/automations.sqlite3` |
| /loop autônomo | `bauer/agent.py` + `bauer/loop_skills.py` | roda o agente turno-a-turno com guardrails (`--max-minutes --max-tool-calls --max-cost`), loop-skills auto-disparáveis |
| Gateway | `bauer/gateway_runtime.py`, `bauer/slack_bridge.py`, `bauer/gateway_outbox.py` | Slack bidirecional (Socket Mode), outbox durável com retry, `live_bridges` registry, tool `channel_send` |
| Aprovação | `bauer/approval.py` (allowlist que aprende once/session/always), `bauer/llm_approval.py` (G4), `_prompt_cmd_decision`/`_make_cli_approval_callback` em `bauer/agent.py:1244-1296` | TODOS assumem terminal interativo ou julgamento LLM local |
| Serviço de SO | `bauer/gateway_service.py` | install systemd/Task Scheduler (padrão a reusar para o daemon) |

**A lacuna central (hipótese a validar no spike)**: (1) o approval callback é
CLI-only — não há caminho "pergunta no Slack e espera resposta"; (2) o daemon
executa tasks do kanban, mas ninguém liga o RESULTADO ao gateway (relatório);
(3) não há política de confiança progressiva — o daemon ou pergunta tudo ou
nada.

## Investigation steps (responda cada uma com file:line no design doc)

1. **Mapear o daemon existente**: leia `bauer/commands/daemon_cmd.py` inteiro e
   `TaskDispatcher.dispatch_once`/`run_claimed_worker`. Responda: o worker
   executa a task com QUAL runtime (orchestrator? run_one_turn? subprocess)?
   Onde o resultado é gravado? O que acontece quando uma tool exige aprovação
   dentro de um worker headless hoje (nega? trava? bypass)?
2. **Mapear o caminho de aprovação**: leia `bauer/approval.py`,
   `check_all_command_guards` (uso em `tool_router.py:~1486`), e
   `_make_cli_approval_callback` em `agent.py`. Responda: o callback de
   aprovação é injetável por interface? O que o `/loop` faz hoje com aprovações
   (auto-nega? auto-aprova com yolo)?
3. **Mapear o canal de ida-e-volta no gateway**: o Slack bridge processa
   mensagens INBOUND via `AgentBackend.process`. Existe alguma primitiva de
   "pergunta pendente aguardando resposta de humano" (correlação
   request/response por thread ou reaction)? (Hipótese: não — precisa ser
   desenhada; inspiração: o fluxo once/session/always do approval.py.)
4. **Mapear o scheduler**: confirme no código que o automation-scheduler
   enfileira tasks READY (onde? `bauer/automation_*.py` ou similar — ache o
   módulo que implementa o spec YAML) e como o daemon as consome.
5. **Confiança progressiva**: leia a allowlist que aprende
   (`~/.bauer/allowed_commands.yaml`, `bauer/approval.py`). Ela é por-comando;
   desenhe como generalizá-la por (task_lane × risco × histórico de sucesso).

## Design deliverable

Escreva `docs/architecture/autonomous-daemon-v2.yaml` seguindo o formato dos
specs existentes em `docs/architecture/` (leia `automation-scheduler.yaml` como
modelo: `id/version/status/owner/summary/store/...`, status: `draft`). O spec
deve definir:

1. **Topologia**: scheduler → kanban READY → daemon claim → executor (/loop ou
   run_claimed_worker?) → gate de aprovação remota → relatório via outbox.
   Diagrama em ASCII no summary.
2. **RemoteApprovalGate**: interface de aprovação plugável — CLI (atual),
   Slack (nova): mensagem no canal com a ação proposta + task + risco; respostas
   aceitas (`sim/não/sempre` ou reactions); timeout com default DENY; onde o
   estado pendente vive (tabela nova no kanban_db? outbox?); idempotência.
3. **Política de confiança progressiva**: regras de quando o daemon age sem
   perguntar (ex.: tool risk low/medium + lane com N sucessos consecutivos) e
   quando escala para humano (risk high, primeiro uso, falha recente). Formato
   do ledger (arquivo YAML em ~/.bauer? tabela?).
4. **Relatório**: evento de conclusão de task → outbox → canal configurado.
   Formato da mensagem (o que fez, custo, duração, diff/artefatos, próxima da
   fila). Digest diário vs por-task (ambos; config).
5. **Config**: nova seção `daemon:` no config.yaml (enabled, lanes, canal de
   aprovação, canal de relatório, orçamentos por task herdados do /loop,
   trust policy).
6. **Segurança**: interação com 007/008 (o daemon NUNCA roda antes desses
   fixes em produção); todo output do daemon passa pelo secrets_scanner; audit
   log por task.
7. **Plano de build fatiado**: 3-5 fatias PR-sized com ordem (ex.: 1ª fatia =
   RemoteApprovalGate com timeout-deny + relatório simples; confiança
   progressiva por último), cada uma com sua verificação.
8. **Open questions** para o mantenedor (ex.: aprovação por reaction ou texto?
   múltiplos aprovadores? o que fazer com task que expira aprovação?).

## Optional thin prototype (só se o design fechar sem bloqueios)

Um script de demonstração `workspace/` OU teste de integração marcado
`@pytest.mark.skip("spike")` que prove o caminho mais arriscado: enfileirar no
outbox uma "pergunta de aprovação" e correlacionar uma resposta inbound fake à
pergunta pendente (sem Slack real — use o padrão de bridge fake dos testes
`tests/test_gateway_runtime.py`). NÃO integre ao daemon real.

## Done criteria

- [ ] `docs/architecture/autonomous-daemon-v2.yaml` existe, status `draft`,
      seguindo o formato dos specs vizinhos
- [ ] As 5 perguntas de Investigation respondidas no spec com `file:line`
- [ ] Gap analysis explícito: tabela "existe / falta" por órgão
- [ ] Plano de build fatiado (3-5 fatias) com dependências
- [ ] ≥5 open questions objetivas para o mantenedor decidir
- [ ] NENHUM código de produção alterado (`git status`: só docs/ e, se houver
      protótipo, arquivo novo isolado)
- [ ] Status atualizado em `plans/README.md`

## STOP conditions

- `bauer/commands/daemon_cmd.py` já implementar aprovação remota ou relatório
  via gateway (o gap pode ter sido fechado desde o planejamento) — reporte o
  que existe antes de desenhar por cima.
- O executor de tasks do daemon NÃO for reutilizável para /loop (arquiteturas
  incompatíveis) — isso muda a topologia; pare e apresente as opções.
- Descobrir um segundo mecanismo de daemon concorrente (além de daemon_cmd e
  gateway service) — mapeie os dois e reporte antes de escolher.

## Maintenance notes

- Este spike é o guarda-chuva da direção 20/10; os planos 015-020 se conectam
  a ele (o relatório matinal do 018 usa o mesmo canal de outbox; o ledger de
  custo do 016 alimenta o relatório).
- O build resultante deve rodar no Beelink como serviço systemd — reuse o
  padrão de `bauer/gateway_service.py` (install/uninstall/status/logs).
