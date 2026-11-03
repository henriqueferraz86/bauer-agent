# BACKLOG — Sprint 38: migrar o caminho web

- [x] SPEC/ARCHITECTURE escritos com evidência real (checkout `6b839a1`).
- [x] Helper `start_chat` + teste real de aprovação assíncrona via `/chat`
  com Agno ativo, reaproveitando fixtures de `tests/test_approval_chat_e2e.py`.
  Teste `test_real_agno_post_chat_approval_runs_each_command_once` cobre
  caminho feliz (duas tool calls aprovadas, efeitos no workspace, histórico
  de sessão) e um caso de deny (run falha, sem efeitos).
- [x] Corrigir a seção do sprint 38 em `plans/062-agno-executor-padrao.md`
  para descrever o mecanismo real (`_runtime_mode()` dentro do executor,
  não `executor=None`). Reescrita no mesmo estilo da correção de
  2026-10-29 já presente no arquivo.
- [x] Checar (não implementar se não existir) se `bauer doctor`/painel já
  expõem o executor ativo por canal — achado abaixo, para o sprint 39.
- [x] Gates do `AGENTS.md`: Ruff bloqueante/estilo, suíte completa.

## Bugs reais encontrados e corrigidos nesta sprint

O teste novo (`test_real_agno_post_chat_approval_runs_each_command_once`)
revelou duas divergências reais entre `/chat` e `/stream` — ambas pontuais,
não exigiram reestruturação (STOP não acionado):

1. **Aprovação nunca esperava em `/chat`.** `run_chat_turn`/`chat_via_kernel`
   (e o branch legado sem Kernel) nunca chamavam `set_policy_approval_wait
   (True)`. Sem isso, `_evaluate_runtime_policy` (`bauer/tool_router.py`)
   levantava `ToolError("waiting_approval: ...")` imediatamente em vez de
   bloquear a requisição HTTP até o operador decidir via
   `POST /api/approvals/{id}/approve` — exatamente o que `/stream` já faz
   (`bauer/server.py::_worker` liga o mesmo contextvar). Corrigido em
   `bauer/server_chat.py`, nos dois branches (com e sem Kernel).
2. **Resposta do Agno nunca persistia no histórico da sessão via `/chat`.**
   O branch Agno de `run_chat_turn` nunca chamava `ctx.add_assistant(...)`.
   O caminho nativo não tem esse problema porque `run_one_turn` já grava a
   resposta em `ctx.messages` como parte do laço de tools; o adapter Agno
   mantém contexto próprio e nunca escreve no `ctx` do chamador — sem o
   append explícito, uma sessão `/chat` com `runtime_mode=agno` perdia a
   resposta do assistente no histórico (`/sessions/{id}/messages`), e o
   próximo turno carregaria a conversa incompleta. Corrigido chamando
   `ctx.add_assistant(deps.format_response(response))` no branch Agno,
   igual ao que `server.py::_worker` já faz para `/stream`.

## Achado para o sprint 39 (não implementado aqui, só registrado)

`bauer doctor` (`bauer/cli.py::doctor` + `bauer/preflight.py::run_doctor`)
**não** menciona executor/adapter em nenhum lugar — nenhuma ocorrência de
`runtime_adapter`/`default_adapter`/`runtime_mode` no módulo.

O painel desktop (`desktop/src/screens/Runtime.tsx`) expõe `default_adapter`
(card "adapter ativo") e `runtime_mode` (dropdown "EXECUTOR GLOBAL DO
SERVER") lado a lado, mas o próprio rótulo da UI já avisa a limitação: "A
escolha é global e vale para os próximos turnos do Server" — ou seja, é
um toggle único que afeta só `/stream`/`/chat` (via `_runtime_mode()`), sem
granularidade por canal. Outros canais (Telegram via `channel_base.py`,
OpenAI-compat via `server_openai.py`, CLI via `agent.py`) não aparecem
nesse painel e usam seus próprios mecanismos de seleção de executor
(ver a seção "Evidência e baseline" de `plans/062-agno-executor-padrao.md`
para o mapeamento completo). O item "expor no `bauer doctor` e no painel
qual executor está ativo por canal/config" já está descrito como escopo do
sprint 39 no próprio plano 062 — nada a implementar aqui, só confirmado
que ainda não existe por acidente.
