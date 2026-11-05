# ARCHITECTURE — Sprint 46 (port da sprint 42)

## Visão

O Telegram será uma interface de decisão para a store de aprovações existente,
nunca um executor. O fluxo suportado é a chamada de ferramenta que já chegou ao
`ToolRouter` de um worker Telegram vivo e está aguardando em
`_wait_for_policy_approval`. Uma aprovação de preflight do Kernel não tem esse
worker suspenso e fica fora do contrato desta sprint.

```text
worker Telegram vivo
  → subscriber já está instalado no EventBus
  → ToolRouter avalia política/HARDLINE e prepara chamada exata
  → ApprovalManager grava request Telegram + deadline absoluto de 600s
  → waiter registra approval_id + digest no ChannelExecutionControl
  → ApprovalManager publica approval.requested somente após o registro
  → bridge busca registro + worker/contexto capturado
  → cartão privado com token opaco
  → callback revalida todos os vínculos
  → BauerKernel.approve(scope=once) faz transação atômica
  → worker reavalia política e consome digest exato
  → chamada original continua uma vez
```

## Componentes e responsabilidades

- `AgentBackend`/`ChannelExecutionRegistry`: fonte de verdade para worker vivo e
  vínculo capturado de owner, chat, projeto, workspace, sessão e run. A
  apresentação não pode inferir ownership somente do `ApprovalRecord`.
- `ToolRouter._evaluate_runtime_policy`: cria o pedido exato e, para a
  execução Telegram elegível, permanece bloqueado aguardando decisão. A
  implementação deve ativar `wait_for_policy_approval` somente no contexto
  Telegram coberto e falhar fechado se run/session/EventBus não coincidirem.
- `ApprovalManager`: persiste status, digest e consumo; requests Telegram
  elegíveis também recebem `expires_at` absoluto. Decisão e expiração desse
  registro são arbitradas transacionalmente, inclusive quando o endpoint web
  tenta resolvê-lo no mesmo runtime SQLite. Requests web/CLI comuns mantêm a
  semântica atual sem prazo novo. A criação Telegram oferece um hook de registro
  do waiter antes de publicar o evento síncrono.
- `BauerKernel`: API para `approve(scope="once")`, `deny`, `expire_approval` e
  `cancel`; não fornecer executor nem input para iniciar uma continuação nova.
- `TelegramBridge`: assina o EventBus uma vez por bridge/bus, renderiza cartões
  privados e guarda tokens opacos temporários. Falhas Telegram são locais à
  apresentação.
- `approval_summary` + `privacy.redact_text`: rótulos curados e redação. O
  argumento não é seguro só por passar por `redact_text`; payload nunca é
  renderizado.

## Elegibilidade: tool approval contra preflight

Há dois caminhos no runtime e eles não são intercambiáveis:

1. **Tool approval com worker vivo**: `ToolRouter` está dentro da chamada da
   ferramenta; `ApprovalManager.request(..., suspend_run=True)` marca a run
   `waiting_approval`; o loop de espera do próprio `ToolRouter` continua vivo.
   Depois de aprovado pelo Kernel, o mesmo loop reavalia a política, chama
   `authorize` com o payload original e digest, consome `once`, move a run
   `queued → running` e retorna à chamada bloqueada. Este é o único caminho
   acionável por cartão nesta sprint.
2. **Aprovação de preflight**: `BauerKernel._preflight` produz `tool_name="kernel"`
   e `operation="runtime.execute"` antes de chamar o executor; `run_governed`
   retorna `blocked_before_start` sem worker suspenso. Não há continuação viva
   para retomar com segurança. O bridge não apresenta botão para esse pedido;
   pedidos de canal sem worker elegível são expirados/falham fechados e recebem
   uma orientação segura. Retomar esse caminho exigiria uma sprint própria com
   persistência/continuação explícita e está fora de escopo.

Essa distinção também cobre aprovações de contrato do Kernel: se forem emitidas
antes do executor, não são tool approvals e não entram nesta interface.

## Fluxo detalhado

1. O subscriber é instalado antes de habilitar qualquer espera elegível. O
   worker já tem um `ChannelExecution`; `control.run_id`, run store,
   `session_id` e contexto do projeto devem estar disponíveis antes da chamada
   de ferramenta. O router do projeto usa o mesmo EventBus/runtime root do
   Kernel. O modo de espera habilitado não muda outras interfaces.
2. HARDLINE/denylist roda antes da criação de qualquer `ApprovalRecord`; uma
   negativa bloqueia a chamada e não publica pedido.
3. Para uma chamada Telegram elegível, `ApprovalManager.request` persiste o
   pedido e um `expires_at` UTC absoluto igual a `created_at + 600s`. Requests
   web/CLI comuns não recebem esse campo. Antes do publish síncrono de
   `approval.requested`, registra `approval_id`, `payload_digest` e deadline no
   `ChannelExecutionControl` do waiter. Esse registro é a única prova de que
   há um worker aguardando aquela chamada. Falha de registro impede publish e
   deixa o pedido sem cartão (rollback ou expiração fail-closed).
4. Um evento `approval.requested` carrega somente referência/metadados. O
   subscriber não mostra `event.message` nem confia em `event.data` como decisão:
   consulta `ApprovalManager.get(approval_id)`, run store e worker registry.
   Confirma `pending`, `tool_name != "kernel"`, run `waiting_approval`, worker
   não terminado e bindings iguais; também exige correspondência exata entre o
   approval/digest do evento e o registro ativo no `ChannelExecutionControl`.
5. Re-resolver o vínculo privado atual e validar projeto registrado, caminho,
   router/workspace e `ChannelExecution` capturados. Não redirecionar uma
   aprovação se o operador mudou o projeto. Se faltar dado ou houver corrida,
   não enviar botão; encerrar/expirar de modo fail-closed.
6. Renderizar um cartão abaixo de 4096 caracteres: nome do projeto sem path,
   tarefa curta redigida, rótulo de `approval_summary`, motivo genérico fixo
   (por exemplo, “a política exige confirmação”), risco mapeado para rótulo
   conhecido, escopo “uma vez — esta chamada” e validade de 10 minutos. Nunca
   mostrar `payload`, comando, args, stdout/stderr, `reason` arbitrário,
   traceback, digest, approval ID ou workspace.
7. Criar `secrets.token_urlsafe` e manter em memória um registro com token,
   operador, chat, mensagem, project ID, workspace canônico, session ID, run ID,
   approval ID, digest original, o mesmo `expires_at` persistido e geração do
   bridge. O `callback_data` contém somente o token opaco. Callback não é
   persistido.
8. Callback privado revalida usuário autorizado, chat, mensagem original,
   projeto/workspace atual, worker vivo e pertencimento de run/sessão. Busca o
   ApprovalRecord outra vez e exige status `pending`, digest igual, run em
   `waiting_approval`, aprovação ainda não expirada, política ainda não negada
   e kill-switch inativo. `ApprovalManager` decide expiração e resolução sob a
   mesma transação para Telegram e web; o TTL do callback não renova o deadline.
9. Aprovar usa `BauerKernel.approve(approval_id, scope="once")` sem executor.
   `ApprovalManager.resolve_for_run` disputa atomicamente com web; um único
   estado ganha. O worker original percebe `approved`, reavalia a política e
   `authorize` confere operação, ferramenta, run, sessão, digest e consumo.
   Botão deixa de ser acionável. Um clique repetido não faz nova chamada.
10. Negar usa `BauerKernel.deny`; deadline usa a expiração central do
   `ApprovalManager` (também usada pelo web). Ambos acordam o waiter para sair
   sem executar. `/cancelar` chama `ChannelExecutionControl.request_cancel` e
   sinaliza o worker cooperativamente; não chama `Kernel.cancel` enquanto o
   worker ainda está vivo. O waiter expira qualquer approval ainda `pending` e
   limpa approval/digest no `finally`; só depois de o worker sair o fluxo de
   custódia registra o resultado terminal. Kill-switch impede aprovação; se
   ativar após a decisão, checkpoint/policy antes da execução ainda bloqueia a
   tool.
11. O gateway tem timeout de geração de 120s. Enquanto o controle registra a
   espera humana elegível, esse relógio de geração fica pausado e volta com o
   tempo restante quando a espera termina. O deadline humano de 600s e os
   limites absolutos, budget e kill-switch do Kernel continuam correndo. O
   `server.py` web já pausa seu deadline de geração durante `waiting_approval`;
   o teste do gateway deve provar semântica equivalente sem copiar o timeout
   web de 3600s.
12. Em `stop/reset`, limpar a cache de tokens, invalidar a geração do bridge e
    expirar pedidos pendentes vinculados às execuções que esse backend está
    encerrando. Não reconstruir callbacks a partir de registros persistidos.
    Após cold restart, callbacks antigos não encontram token e são rejeitados;
    pedidos órfãos permanecem inacessíveis até reconciliação fail-closed. Uma
    reconciliação, se implementada, deve identificar a origem pelo conjunto
    persistido `agent_id=channel.telegram`, `input.endpoint=canal:telegram`,
    operador, projeto e workspace capturados — nunca por prefixo de session ID
    (`tg:` não é o formato usado por estes projetos nem prova origem). Não
    expirar execuções que possam pertencer a outro gateway vivo ou ao web.

## Armazenamento, concorrência e falhas

- Persistente: pedidos Telegram elegíveis recebem `expires_at` absoluto UTC,
  junto ao estado da run e digest no runtime SQLite existente. A migração
  adiciona o campo opcional sem deadline retroativo. Requests web/CLI comuns,
  novos ou legados, não recebem o novo prazo e preservam a semântica anterior.
  Registro legado sem `expires_at` e comprovadamente
  originado no Telegram fica inacessível a cartão e falha fechado. A prova de
  origem usa metadados persistidos (`agent_id=channel.telegram` e
  `input.endpoint=canal:telegram`); prefixo de sessão não basta.
- Efêmero: token Telegram e bindings em memória, com vencimento igual ao
  `expires_at` persistido, tamanho limitado e remoção após decisão/expiração.
  Relógio monotônico pode auxiliar limpeza local, mas nunca é a autoridade nem
  renova o prazo. Reinício perde todos os tokens por desenho.
- Web e Telegram instanciam managers separados, mas devem apontar ao mesmo
  runtime root/EventBus. A serialização de `_resolve` arbitra pedidos Telegram
  com deadline, mesmo quando o endpoint web tenta resolvê-los. Um perdedor de
  corrida observa conflito/estado já resolvido e nunca continua a execução;
  requests web/CLI sem `expires_at` seguem a semântica anterior.
- O caso de mesmo resultado aprovado pode ser idempotente na camada Kernel;
  isso não dá segundo consumo porque `authorize` marca `consumed_at` em
  transação e confere digest.
- Falha de envio antes de publicar cartão remove o callback efêmero. Falha de
  edição depois de decisão não reverte estado do runtime. Callback vencido,
  inválido ou sem worker recebe somente resposta genérica e não expõe estado de
  outro projeto.
- Para pedidos Telegram elegíveis, a expiração é central no `ApprovalManager`:
  callbacks Telegram e endpoints web que tentem resolvê-los competem sob a
  mesma transação e deadline absoluto. Requests web/CLI comuns sem
  `expires_at` seguem a semântica anterior. O limite relativo
  `BAUER_APPROVAL_WAIT_SECONDS` não pode ser uma segunda autoridade. O waiter
  observa o deadline persistido e, ao sair por qualquer motivo, expira o que
  continuar pending. Registro legado sem deadline e sem provenance Telegram
  conserva o comportamento anterior das superfícies web/CLI; origem Telegram
  comprovada nunca recebe botão e falha fechada se ainda estiver pending.
- O timeout de geração do gateway (120s) pausa somente durante a espera humana
  explicitamente registrada. Isso não pausa deadline de approval, orçamento,
  kill-switch ou limite absoluto de execução do Kernel.

## Decisões e migração

- Reutilizar `ApprovalManager`, `BauerKernel` e `authorize`; não criar uma
  segunda store de consentimento nem aprovar por texto livre.
- Expor só `once`. As APIs atuais aceitam `run` e `always` em alguns caminhos;
  Telegram não encaminha esses escopos e sempre fornece explicitamente
  `scope="once"`.
- Somente tool approval com worker vivo; preflight fica fora de escopo. Isso
  limita a sprint a um caminho que já pode continuar a chamada exata sem
  reconstruir executor ou objetivo.
- Adicionar migração aditiva de schema para `expires_at` opcional; criar
  deadlines somente para requests Telegram elegíveis, sem reescrever registros
  legados nem ampliar a política web/CLI. Preservar a semântica de aprovações
  web/CLI históricas sem provenance Telegram. Registros legados
  comprovadamente Telegram não recebem botão e pending falha fechado. Não
  adicionar config. Token efêmero implica invalidação de cartões após restart;
  runs órfãs exigem reconciliação explícita e nunca são reemitidas.
- Subscriber único por EventBus, bindings e lifecycle do cartão próprios da
  aprovação (no original, reaproveitados da sprint 41; no port, que descarta a
  sprint 41, o estado é dedicado: `_approval_http_lock`,
  `_approval_generation`, `_approval_shutdown`, `_approval_lock`), com efeitos
  mutáveis estritamente através do Kernel.
- O backend mantém um único `EventBus` por runtime root
  (`AgentBackend._configure_channel_router`), compartilhado pelo router do
  turno e pelo Kernel do canal; sem isso o evento publicado pelo `ToolRouter`
  não alcança o subscriber instalado em `kernel.bus`.
- Executor Agno: `_turno_agno` entrega o `ToolRouter` do projeto ao
  `AgnoRuntimeAdapter`, e as tools Bauer do Agno chamam
  `execute_native_call` na thread do worker — mesma costura de política,
  mesmas ContextVars, mesma espera. Nenhum caminho alternativo de governança.
- Alterar `ApprovalManager.request` de modo que o waiter registre
  `approval_id`/digest/deadline antes do evento síncrono. A inscrição do
  subscriber ocorre na inicialização do bridge/contexto, nunca como reação ao
  primeiro evento.
- Introduzir deadline persistido requer migração compatível: somente pedidos
  Telegram elegíveis recebem o prazo; endpoint web respeita esse prazo se
  resolver o registro. Requests web/CLI comuns não ganham prazo. Registros
  antigos sem prazo não são alterados globalmente. Apenas provenance Telegram
  comprovada permite classificar um pedido legado como inacessível/fail-closed,
  sem botão.

## Testes e observabilidade

- Novo `tests/test_telegram_approval.py`: `httpx.MockTransport`, runtime em
  `tmp_path`, EventBus/ApprovalManager/RunManager reais, worker de tool
  bloqueado com `threading.Event`/barreira, clock/timer fake. Nenhum bot ou
  provider real.
- Com subscriber instalado antes do request, teste o publish síncrono e prove
  que o controle já contém approval ID/digest; pedido sem registro não cria
  cartão e falha de registro não publica evento acionável.
- Provar execução única do efeito com payload original, worker vivo antes e
  depois da decisão, e ausência de repetição do objetivo.
- Concorrer dois `ApprovalManager` em roots iguais (Telegram e web) para provar
  uma decisão; disputar aprovar/negação e callback duplicado.
- Cobrir restart token perdido e run sem worker, deadline absoluto de 600s
  somente para pedido Telegram, expiração atômica contra endpoint web,
  cancelamento cooperativo com
  expiração em `finally`, kill-switch, HARDLINE antes do request, digest
  alterado, projeto/path alterado, user/chat/message errados, mensagem segura
  e Telegram API falhando.
- Usar relógio fake para provar que espera humana não gasta os 120s de geração
  do gateway, mas os 600s de approval e limites absolutos do Kernel seguem
  correndo; comparar com a pausa existente no stream web.
- Cobrir migração legada: aprovação web/CLI sem `expires_at` continua intacta;
  approval pendente sem prazo com provenance Telegram comprovada não recebe
  cartão e falha fechada. Provenance ausente ou ambígua nunca deve ser inferida
  como Telegram por prefixo de sessão.
- Confirmar que requests web/CLI novos não recebem `expires_at` e mantêm o
  comportamento existente; o endpoint web deve aplicar o prazo somente ao
  resolver um pedido Telegram que tenha esse campo.
- Logs trazem somente tipo de evento, status e IDs internos em log protegido;
  não registram callback token, payload, args, digest ou resposta de tool.
