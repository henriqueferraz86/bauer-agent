# SPEC — Sprint 45: `/atividade` no Telegram (plano 061, Etapa 3)

Plano 061, Etapa 3 ("Atividade dos agentes"). Hoje o Telegram não tem NENHUM
comando de atividade — confirmado por busca literal em `bauer/telegram_bridge.py`
no `origin/master` (commit `3121bfa`): nenhuma ocorrência de "atividade" ou
"ACTIVITY". É um recurso novo, não um refinamento.

## Problema

A projeção de atividade já existe e é compartilhada com a web:
`bauer/server_activity.py:build_chat_activity(run_manager, event_bus,
session_id, run_id=None)` — pura, sem I/O, já filtra por `session_id`/`run_id`
(nunca cruza sessão), já redige texto (`redact_text`), já limita agentes/
eventos/tamanho de texto, já distingue delegações concorrentes ao mesmo agente
por `delegation_id`, e já trata o estado terminal corretamente (nunca marca um
filho como concluído por silêncio). Isso satisfaz o item 1 da Etapa 3 — NADA
a extrair, já é o módulo compartilhado. O trabalho desta sprint é só a
apresentação no Telegram: comando, formatação, atualização da mesma mensagem
e botões.

## Objetivo e escopo

1. Comando `/atividade` em conversa privada autorizada: resolve a sessão do
   chat, chama `build_chat_activity` via o Kernel do canal, renderiza um
   cartão em texto legível (agente, tarefa, ferramenta, estado, motivo de
   espera, custo conhecido) com botões `Atualizar` e `Cancelar tarefa`.
2. Enquanto a execução mostrada não for terminal, uma thread observadora edita
   a MESMA mensagem a cada ~2,5s (mesmo cadência de `_StreamingDraft`/
   `STREAM_EDIT_INTERVAL_S`) SE o conteúdo renderizado mudou desde a última
   edição — isso é a "coalescência": vários eventos entre dois ticks viram
   uma edição só, e um tick sem mudança não gera chamada à API (menos risco de
   429). Tem um teto de tempo de observação (2h) independente do budget da
   execução — é só UX, não é um mecanismo de governança.
3. Ao chegar a um estado terminal (ou ao bater o teto), a edição final do
   resumo é tentada INCONDICIONALMENTE, mesmo que uma edição anterior tenha
   falhado — nunca herdar o padrão "falhou uma vez, desliga para sempre" do
   `_StreamingDraft` (ver ARCHITECTURE.md). Os botões são removidos nesse
   momento (consistente com `_project_callback`'s `reply_markup:
   {inline_keyboard: []}` ao consumir um menu).
4. Botão `Atualizar`: reusa a mesma função de renderização para reconsultar o
   snapshot atual e editar a mensagem na hora, sem esperar o próximo tick do
   observador.
5. Botão `Cancelar tarefa`: mesma semântica de `/cancelar <run_id>` — tenta
   `cancel_channel_execution`, cai para `telegram_loop.cancel_loop` (sprint
   44) se o run não estiver em `ChannelExecutionRegistry`. Extrair essa lógica
   combinada para um helper único, reusado pelo comando de texto E pelo botão
   (sem duplicar, sem mudar o comportamento já testado de `/cancelar`).
6. Callback opaco com token (`secrets.token_urlsafe`), ligado a
   `chat_id`/`owner`/`message_id`/`run_id`, TTL e limite de tamanho — mesmo
   padrão de `self._project_callbacks` em `telegram_bridge.py`.

Fora de escopo: grupos/tópicos (V1 é conversa privada, igual ao resto do
plano); histórico de execuções passadas (card mostra a execução ativa/mais
recente da sessão, igual à semântica já implementada em
`build_chat_activity`); qualquer mudança em `bauer/server_activity.py`
(módulo já correto, só consumir); aprovação remota (Etapa 4, trabalho
paralelo em andamento por outra sessão nesta mesma janela — não depender de
nada que essa sessão ainda não tenha mergeado em `origin/master`).

## Skills obrigatórias

- `spec-driven-project-setup`
- `test-strategy` (suíte hermética — reusar o padrão real de
  `tests/test_chat_activity.py`: `RunManager`+`EventBus` reais sobre
  `tmp_path`, só a camada HTTP do Telegram e `_resolver_kernel` são fake)

## Sub-agents recomendados

- `activity-rendering-reviewer`: confirmar que o texto nunca expõe JSON cru,
  prompts, argumentos sensíveis ou stack trace (mesma régua de
  `server_activity.py`'s `redact_text`/limites de tamanho).
- `telegram-callback-reviewer`: confirmar que o token do botão é opaco,
  expira, está vinculado a chat/owner/message_id, e que dois chats nunca veem
  o cartão um do outro.

## Requisitos

1. `/atividade` fora de conversa privada autorizada → recusa, mesmo texto
   padrão de `/cancelar`/`/loop`.
2. Sem execução ativa/recente na sessão → mensagem amigável, sem erro, sem
   botões.
3. O cartão mostra, por agente/membro: nome, tarefa, estado em português,
   ferramenta corrente (se houver) e, no topo, custo conhecido do run —
   nunca inventar progresso, percentuais ou custo zero quando desconhecido.
4. Duas delegações concorrentes ao MESMO agente aparecem como linhas
   separadas no cartão (usa `delegation_id` que `build_chat_activity` já
   expõe).
5. Dados de outra sessão/chat NUNCA aparecem no cartão — `build_chat_activity`
   já garante isso filtrando por `session_id`; o comando nunca passa um
   `session_id` que não seja o da própria conversa.
6. A thread observadora edita a mesma `message_id` sempre; nunca manda uma
   segunda mensagem para a mesma execução enquanto a primeira ainda existir.
7. Uma falha de rede/Telegram numa edição intermediária não impede a edição
   final do resumo terminal nem interrompe a tarefa sendo observada (a tarefa
   em si roda no Kernel, independente do Telegram estar no ar).
8. Mensagens longas respeitam o limite de caracteres do Telegram e nunca
   cortam uma tag HTML no meio (reusar `chunk_text`/`md_to_telegram_html`
   existentes ou truncar texto puro de forma segura, igual ao resto do
   bridge).
9. Botão `Cancelar tarefa` tem exatamente o mesmo comportamento observável do
   comando `/cancelar <run_id>` (mesmo helper, sem duplicação de lógica nem
   de texto de resposta).
10. Callback expirado, de outro usuário/chat, ou para uma mensagem diferente
    da registrada é ignorado (mesma régua de `_project_callback`).
11. A thread observadora nunca roda indefinidamente: teto de 2h de observação,
    mesmo se o run nunca chegar a terminal.
12. Suíte hermética: nenhum teste chama rede, Telegram real ou provider LLM
    real; usar `RunManager`/`EventBus` reais sobre `tmp_path` para gerar os
    dados que `build_chat_activity` projeta, igual a `tests/test_chat_activity.py`.

## Critérios de aceite

- Duas delegações ao mesmo agente aparecem separadas no cartão.
- Dados de outra sessão nunca aparecem no cartão de um chat.
- Falha simulada do Telegram numa edição intermediária não impede o resumo
  terminal nem interrompe a tarefa observada.
- Mensagens longas mantêm formatação válida e não inundam o chat (uma
  mensagem por execução, sempre editada, nunca duplicada).
- `uv run pytest tests/ -q` e `uv run --with ruff==0.17.0 ruff check bauer/
  --select E9,F63,F7,F82` passam.
