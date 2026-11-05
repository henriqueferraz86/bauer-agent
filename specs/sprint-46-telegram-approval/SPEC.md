# SPEC — Sprint 46: aprovação exata pelo Telegram (port da sprint 42)

## Origem e decisões do port

Esta sprint é o **port** da sprint 42 (commit `f009cd5`, branch
`codex/sprint-42-telegram-approval`, nunca mergeada) para o `master` atual. O
número novo evita sugerir que esta é a branch antiga. Decisões:

- **Sprint 41 descartada.** `f009cd5` estava sobre `bb9aee1` (atividade
  governada da sprint 41), que nunca foi mergeado; o `master` implementou
  `/atividade` de forma independente na sprint 45 (`bauer/telegram_activity.py`),
  que é a implementação canônica. Nada da infraestrutura de atividade da
  sprint 41 foi ressuscitado (`_activity_http_lock`, `_activity_generation`,
  `_activity_shutdown`, `_activity_buses`, timers de callback,
  `channel_activity_source`).
- **Estado próprio da aprovação.** O `TelegramBridge` ganhou equivalentes
  dedicados: `_approval_http_lock` (serializa envio/edição de cartão com o
  fechamento do client em `stop()`), `_approval_generation` (invalida tokens
  em `stop()`/`reset()`), `_approval_shutdown`, `_approval_lock`,
  `_approval_buses`, `_approval_callbacks` e `_approval_executor`. A
  resolução de projeto+Kernel usada pelo cartão e pelo callback é
  `AgentBackend.channel_approval_source` (vínculo persistente atual, falha
  fechada se o projeto mudou). `TelegramBridge.reset()` passa a existir só
  para invalidar aprovações e reabrir o executor de aprovação; o restante do
  ciclo de vida do bridge segue o `master`.
- **EventBus único por backend.** A única peça de `bb9aee1` trazida é o
  `AgentBackend._configure_channel_router`/`_channel_event_bus`: no `master`
  o router do turno e o Kernel do canal usavam instâncias distintas de
  `EventBus` (assinaturas são por instância), então `approval.requested`
  publicado pelo `ToolRouter` nunca chegaria ao subscriber instalado em
  `kernel.bus`. `tests/test_telegram_approval_e2e.py` falha sem essa
  correção.
- **Executor Agno (plano 062).** Com `runtime.telegram_default_adapter: agno`
  (produção na Beelink desde 2026-11-04), `_turno_agno` passa o próprio
  `ToolRouter` do projeto ao `AgnoRuntimeAdapter`; cada tool Bauer do Agno
  chama `ToolRouter.execute_native_call`, que passa pelo mesmo
  `_evaluate_runtime_policy`, na mesma thread e com as mesmas ContextVars
  (`_POLICY_APPROVAL_WAIT`, `ExecutionControl` ativo). A espera exata
  funciona igual nos dois executores; não há lacuna conhecida. O teste
  ponta a ponta cobre aprovar/negar em `bauer_native` e em `agno` (SDK Agno
  real, modelo fake que emite o tool call).
- **Endurecimentos do port.** O `ToolRouter` só aplica o deadline Telegram
  quando o `ExecutionControl` é de chat privado, e o cartão nunca é
  renderizado para execução não privada (antes, um router configurado
  globalmente em `enforce` poderia levar o cartão a um grupo).
- **Limitação conhecida (inalterada da sprint 42).** Conversa privada sem
  projeto vinculado (`/projetos`) liga a espera, mas não recebe cartão: os
  bindings do callback exigem projeto. O pedido pode ser decidido pela UI web
  ou expira em 600s, falhando fechado.


## Problema

O runtime já persiste pedidos de aprovação e suspende chamadas de ferramenta,
mas o gateway Telegram ainda não oferece uma decisão remota segura. Uma
aprovação transportada como texto, vinculada só ao ID, ou retomada por uma nova
mensagem pode autorizar argumentos diferentes, atravessar projetos ou repetir
trabalho depois de restart.

## Objetivo

Permitir que um operador autorizado em conversa privada aprove **uma vez a
chamada exata** que já está aguardando no worker vivo do Telegram, ou a negue,
com um cartão seguro e callback opaco. Uma decisão deve resolver uma única
transação compartilhada com web; não deve iniciar o objetivo novamente.

## Escopo

- Apresentar aprovações `pending` de chamadas de ferramenta originadas por um
  `ChannelExecution` Telegram vivo, vinculado ao operador, chat, projeto,
  workspace, sessão e run capturados na admissão.
- Suportar somente `scope="once"` para a chamada cujo `ApprovalRecord` está
  pendente; não oferecer opções de sessão, run ou allowlist permanente.
- Manter o worker original vivo aguardando dentro do caminho de política do
  `ToolRouter`; após a decisão, ele revalida a política e consome o digest exato
  antes de prosseguir com a chamada já suspensa.
- Registrar explicitamente `approval_id` e `payload_digest` no
  `ChannelExecutionControl` do waiter antes de publicar `approval.requested`;
  somente esse registro de espera torna o pedido elegível para cartão.
- Instalar o subscriber do EventBus antes de qualquer `ApprovalManager.request`
  elegível poder publicar; não depender de registro lazy ao receber o evento.
- Exibir resumo seguro de projeto, tarefa, operação, motivo genérico, risco e
  expiração. Usar `approval_summary` para rótulos fixos; não exibir payload,
  argumentos, saída de ferramenta, comandos ou traceback.
- Para pedidos Telegram elegíveis, persistir um deadline absoluto de 600
  segundos no `ApprovalManager`; callbacks privados e decisões web sobre esse
  registro respeitam o mesmo deadline. Callback é vinculado no servidor a
  usuário, chat, mensagem, projeto, workspace, sessão, run, approval e digest.
  A decisão e a expiração comparam esse deadline sob a transação do
  `ApprovalManager`; nenhuma superfície pode estendê-lo. Pedidos web/CLI comuns
  não recebem prazo novo e preservam a política atual.
- Ao sair do waiter por qualquer caminho, expirar o pedido ainda `pending` e
  limpar a associação de aprovação do controle.
- Cancelamento Telegram de worker vivo é cooperativo por
  `ChannelExecutionControl.request_cancel`; não terminalizar a run com
  `Kernel.cancel` antes que o worker saia.
- Suspender somente o timeout de geração do gateway (120s) durante a espera
  humana registrada. O deadline persistido de 600s, orçamento, kill-switch e
  limites absolutos do Kernel permanecem ativos.
- Revalidar todos os vínculos e o estado `pending` imediatamente antes de
  decidir. A aprovação web e Telegram deve usar a transação atômica existente
  no `ApprovalManager`.
- Negação, expiração, cancelamento cooperativo via `ChannelExecutionControl` e
  kill-switch encerram o pedido sem executar a chamada. O worker expira a
  aprovação pendente ao sair, qualquer que seja o motivo. HARDLINE/denylist
  bloqueia antes de criar `ApprovalRecord`.
- Após restart, invalidar tokens em memória e falhar fechado para pedidos sem
  worker vivo; nunca reconstruir callback acionável a partir do banco nem
  repetir objetivo ou ferramenta.
- Isolar indisponibilidade ou erro de envio/edição Telegram do runtime e da
  decisão persistida.

## Fora de escopo

- Aprovação de preflight do Kernel (`operation=runtime.execute`, `tool_name`
  `kernel`) ou de contrato antes de iniciar executor: hoje esses casos retornam
  de `run_governed` sem um worker de ferramenta suspenso que possa continuar.
  O Telegram não os converterá em continuação improvisada; eles permanecem
  bloqueados e sem botão nesta sprint.
- Aprovação permanente, por sessão, por run, autoaprovação ou alteração de
  allowlist.
- Reexecutar o objetivo, recriar uma execução ou retomar worker em processo
  reiniciado.
- Bot real, grupos/tópicos, detalhes de payload sensível, integração com
  `/loop` e mudanças nos endpoints web existentes.

## Contratos auditados

- `bauer/core/policy/approvals.py`: `ApprovalManager.request(...,
  suspend_run=True)` persiste a aprovação e muda atomicamente a run para
  `waiting_approval`; `resolve_for_run` serializa decisões; `authorize` verifica
  operação, tool, run, sessão e `payload_digest`, e consome `once` uma vez.
  Ainda não persiste um deadline absoluto nem oferece hook para registrar o
  waiter antes do publish; a sprint deve acrescentar ambos e arbitrar expiração
  sob a mesma transação para decisões web e Telegram quando o registro for um
  pedido Telegram elegível com deadline. Requests web/CLI comuns não recebem
  `expires_at` novo e mantêm a semântica atual.
  Aprovações históricas sem `expires_at` não recebem deadline retroativo: sem
  provenance Telegram preservam a semântica web/CLI anterior; com origem
  Telegram comprovada, ficam inacessíveis à interface e falham fechadas.
- `bauer/core/kernel/kernel.py`: `BauerKernel.approve(id, scope="once")`
  resolve aprovação e transição `waiting_approval → queued`; `deny`,
  `expire_approval` e `cancel` encerram a execução, e `cancel` expira aprovações
  pendentes. Não passar `executor`/`continue_with` no fluxo Telegram.
- `bauer/tool_router.py`: o modo de espera de política mantém a chamada de
  ferramenta dentro do worker; depois de `approved`, reavalia a política,
  chama `ApprovalManager.authorize(..., resume_run=True)` e só então retorna à
  execução. `deny` prevalece sobre aprovação. O caminho do gateway deve ligar
  esse modo somente para o worker Telegram coberto pela spec. Hoje o waiter não
  registra aprovação/digest no controle antes da publicação, precisa expirar a
  aprovação pendente em todo caminho de saída e usa `BAUER_APPROVAL_WAIT_SECONDS`
  relativo em vez do deadline persistido compartilhado.
- `bauer/channel_execution.py` e `bauer/channel_base.py`: o registro vivo
  guarda `owner`, canal, chat, projeto, sessão, workspace, `run_id`, fase e
  sinalização de término. Seu `ExecutionControl` oferece cancelamento
  cooperativo e precisa representar approval/digest/deadline e duração da
  espera para provar que há worker aguardando, suspender o timeout de geração
  do gateway e validar o cartão.
- `bauer/channel_base.py`: `BAUER_GATEWAY_TURN_TIMEOUT` defaulta em 120s; hoje
  uma espera humana pode ser confundida com timeout de geração e receber sinal
  de cancelamento. A sprint deve descontar da janela de geração apenas o tempo
  registrado como espera de aprovação; limites absolutos do Kernel não mudam.
- `bauer/server.py`: o stream web já suspende o seu deadline de geração enquanto
  a run está `waiting_approval`; usar esse comportamento como referência para
  o teste equivalente do gateway, mantendo o novo deadline persistido de 600s.
- `bauer/channel_governance.py`: routers de projetos compartilham EventBus e
  runtime root por backend; usar o mesmo bus para receber `approval.requested`.
- `bauer/approval_presentation.py`: `approval_summary` fornece rótulos fixos e
  não inclui argumentos do comando. `bauer/privacy.py:redact_text` é defesa
  adicional, não autorização para imprimir argumentos ou `reason` arbitrários.
- `bauer/server.py`: web já decide usando o mesmo Kernel/ApprovalManager; o
  contrato de concorrência é compartilhamento da store SQLite e decisão
  transacional, sem endpoint web novo.
- Distinção obrigatória: aprovação de **preflight** acontece antes do executor
  e não tem worker de ferramenta vivo; aprovação de **tool** ocorre dentro do
  worker, que fica bloqueado no `ToolRouter`. Só a segunda é elegível nesta
  sprint.

## Requisitos funcionais

1. Só usuários autorizados em chat privado recebem ou acionam controles.
2. O cartão corresponde a um pedido `pending` de ferramenta e a um
   `ChannelExecution` ainda vivo, na fase de execução, cujo owner/canal/chat,
   projeto/workspace, sessão e run coincidam com o pedido e o contexto atual.
   A aprovação só é elegível se o `ChannelExecutionControl` do waiter já
   registrar explicitamente o mesmo `approval_id` e `payload_digest`.
3. O subscriber do EventBus está registrado antes da publicação do evento. A
   criação do pedido registra approval/digest/deadline no waiter antes de
   publicar; evento sem esse registro correspondente nunca cria cartão.
4. O resumo usa dados curados e limitados. `project_name` e tarefa são
   redigidos, têm controles removidos e comprimento máximo; caminho absoluto,
   `ApprovalRecord.payload`, `reason` bruto, args/resultados e IDs internos de
   callback não são renderizados. Risco e motivo são mapeados para vocabulário
   fixo. Mensagem fica abaixo de 4096 caracteres.
5. O callback contém somente um token opaco aleatório. O registro servidor-side
   inclui bindings completos e `payload_digest`; não colocar approval/run,
   sessão, workspace ou digest no `callback_data`.
6. Antes de aprovar/negar, buscar de novo approval, run, worker e projeto;
   conferir igualdade dos bindings/digest e status `pending`, allowlist atual,
   chat privado e mensagem original. Projeto alterado, removido ou caminho
   inválido falha fechado.
7. Aprovar chama a decisão governada com `scope="once"` e nenhum executor ou
   `continue_with`. A store efetua uma única transição; o worker vivo acorda e
   só executa após autorizar o payload com o mesmo digest. A operação não pode
   ser repetida por segundo clique, callback antigo ou decisão web concorrente.
8. Um único deadline absoluto de 600s é criado e persistido quando
   `ApprovalManager.request` grava um pedido Telegram elegível. O callback, o
   waiter e uma decisão web sobre esse mesmo registro respeitam o mesmo
   `expires_at`; nenhuma atualização do cartão ou reconexão reinicia o relógio.
   `resolve_for_run`/decisão web e Telegram arbitram expiração e decisão sob a
   mesma transação: no/após deadline, estado `expired` vence e nenhuma
   aprovação pode entrar em `queued`. Requests web/CLI comuns não recebem esse
   campo e preservam comportamento anterior.
9. A espera humana registrada não consome os 120s de geração do gateway. Ao
   sair da espera, o worker continua com a janela de geração restante. Budget,
   kill-switch e limites absolutos do Kernel continuam ativos.
10. Negar usa o Kernel para negar. O waiter que atinge deadline chama o caminho
    central de expiração. Cancelamento de worker vivo sinaliza
    `ChannelExecutionControl`; não chama `Kernel.cancel` antes do worker sair.
    O worker expira pedido ainda pendente no `finally` e o Kernel só registra
    terminal conforme a custódia do worker.
11. HARDLINE e denylist são checados antes de criar `ApprovalRecord`; não criar
    botão e não executar independentemente de decisão prévia.
12. Após restart ou perda do estado de callback, tokens antigos não são aceitos.
   Aprovações pendentes sem `ChannelExecution` vivo ficam inacessíveis até
   reconciliação fail-closed; não se cria worker, não se reproduz a chamada e
   não se reenvia o objetivo ao modelo. A origem deve ser identificada pelos
   metadados persistidos `agent_id`, `input.endpoint`, operador, projeto e
   workspace; prefixos de session ID não provam origem Telegram.
13. Falhas de Telegram não mudam status, digest, custo, run ou resultado da
    operação; falhas de decisão não removem a custódia do worker.

## Skills e responsabilidades

- Skill obrigatória para o fluxo documental: `spec-driven-project-setup`.
- Na implementação, seguir também as regras de teste hermético e validação do
  `AGENTS.md`; não carregar config real nem chamar provider ou bot real.
- Papéis recomendados: implementador de backend (wiring Kernel/bridge),
  engenheiro de testes (concorrência e cenários de worker), revisor de segurança
  (bindings/redação/restart/HARDLINE) e revisor de código (contratos e escopo).
  Cada papel revisa sua área; nenhum deles altera o escopo para aprovação
  permanente ou retomada por replay.

## Critérios de aceite

- Aprovar uma chamada no Telegram faz o worker original continuar exatamente
  uma vez, e a segunda chamada não é executada sem uma nova aprovação.
- `ApprovalRecord` com digest/payload diferente, outro user/chat/message,
  projeto/workspace/sessão/run diferente, token adulterado ou callback expirado
  não concede autorização.
- Aprovação web concorrente e clique Telegram concorrente têm no máximo uma
  transição/consumo; o vencedor é o estado persistido e o perdedor não repete
  efeitos.
- Negar, expirar, cancelar e kill-switch impedem a operação e deixam estado
  terminal coerente; HARDLINE não oferece botão.
- O evento de pedido não é perdido: subscriber já existe e o waiter registra
  ID/digest antes do publish; evento pendente sem registro explícito não aparece
  como elegível. Um teste observa o controle já registrado durante o callback
  síncrono do EventBus.
- Deadline UTC absoluto de 600s é persistido somente para pedidos Telegram
  elegíveis; callback e endpoint web que resolverem esse registro respeitam o
  prazo, e no/após deadline só podem resolver como expirado. Requests web/CLI
  comuns, novos ou legados, não recebem prazo e mantêm a semântica anterior;
  aprovação legada sem deadline com provenance Telegram comprovada não ganha
  botão e falha fechada. Testes cobrem esses casos, dois managers e corrida
  aprovar/expirar.
- A espera humana não dispara timeout/cancelamento de geração do gateway em 120s;
  teste com relógio controlado mostra paridade com a pausa de deadline já usada
  pelo stream web, sem pausar o deadline de approval de 600s nem
  budget/kill-switch/limites absolutos do Kernel.
- Cancelar uma execução viva sinaliza `ChannelExecutionControl`, não marca run
  terminal prematuramente, e o waiter expira aprovação pendente ao sair.
- Reiniciar invalida callbacks e não cria nova execução; pedidos órfãos são
  expirados ou mantidos inacessíveis até reconciliação fail-closed explícita.
- Cartão não contém args, payload, output, caminho absoluto, segredo, traceback,
  ID interno ou JSON cru; resumo é legível e menor que 4096 caracteres.
- Testes herméticos usam `tmp_path`, SQLite temporário, EventBus/RunManager reais,
  providers fake, relógio/timers controlados e `httpx.MockTransport`.

## Plano de validação

Testes focados previstos: `tests/test_telegram_approval.py` (novo),
`tests/test_telegram_approval_e2e.py` (novo no port: turno real nos
executores `bauer_native` e `agno`), `tests/test_process_exact_approval.py`,
`tests/test_approval_root_lifecycle.py`, `tests/test_approval_chat_e2e.py`,
`tests/test_approval_presentation.py`, `tests/test_policy_engine.py`,
`tests/test_policy_serve_modes.py`, `tests/test_execution_control.py`,
`tests/test_channel_base.py`, `tests/test_channel_projects.py` e
`tests/test_telegram_activity.py`. Cobrir worker vivo e gatear preflight,
subscriber antes de publish, registration approval/digest antes do evento,
resumo/redação, deadline persistido, callback bindings, troca de
projeto/caminho, concorrência com web em managers separados, digest, HARDLINE
antes da criação do registro, deny, expire, cancelamento cooperativo com
expiração em finally, kill-switch, restart, falha Telegram e pausa/paridade dos
timeouts de geração gateway/web.

Comandos focados após implementação (ajustar aos módulos realmente alterados):

```bash
uv run pytest tests/test_telegram_approval.py tests/test_approval_root_lifecycle.py tests/test_approval_chat_e2e.py tests/test_approval_presentation.py tests/test_policy_engine.py tests/test_policy_serve_modes.py tests/test_execution_control.py tests/test_channel_base.py tests/test_channel_projects.py tests/test_telegram_activity.py -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ tests/test_telegram_approval.py --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ tests/test_telegram_approval.py --select E,F,W --ignore E501,W291,W293,E302,E303
```

Antes de encerrar a sprint, seguir os gates do repositório: `uv sync --frozen
--extra dev`, `uv run pytest tests/ -q --tb=short`, Ruff nos caminhos `bauer/`
conforme `AGENTS.md`, e mypy nos módulos Python modificados. Nenhum teste faz
request de rede real ou executa comando fora de diretório temporário.
