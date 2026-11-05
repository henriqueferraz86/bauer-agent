# BACKLOG — Sprint 42

## Tarefas

### 1. Expor o contexto elegível de aprovação no backend do canal

Status: concluído

Critérios:
- Registrar o subscriber antes de habilitar chamadas elegíveis; nenhum registro
  lazy ao primeiro `approval.requested`.
- Resolver ApprovalRecord, RunManager e `ChannelExecution` pelo runtime
  compartilhado, sem lookup global por sessão não autorizada.
- Confirmar run/session/project/workspace/owner/chat e worker vivo antes de
  criar cartão.
- No fluxo de criação, registrar `approval_id`, `payload_digest` e deadline no
  `ChannelExecutionControl` antes do publish síncrono. Evento sem esse registro
  exato não cria cartão; falha no hook impede publish e encerra fail-closed.
- Filtrar approvals de preflight (`tool_name="kernel"`) e qualquer caso sem
  worker retomável; falhar fechado sem botão.

### 2. Manter worker Telegram vivo na aprovação de ferramenta

Status: concluído

Critérios:
- Habilitar espera de política somente para chamadas elegíveis do worker
  Telegram, com run ID, session ID e EventBus correspondentes.
- HARDLINE/denylist precisa ser avaliado antes de `ApprovalManager.request`;
  negação não persiste approval nem publica evento.
- O worker bloqueia no `ToolRouter`, mantém contexto e continua a mesma chamada
  após decisão; nenhum novo objetivo/turno é criado.
- Política é reavaliada antes de `authorize`; deny e HARDLINE prevalecem.
- Ao sair do waiter por decisão, cancelamento, erro ou deadline, o `finally`
  expira o pedido ainda pending e limpa approval/digest do controle.
- Cancelamento do canal sinaliza `ChannelExecutionControl.request_cancel`;
  `Kernel.cancel` não terminaliza a run enquanto o worker vivo ainda está
  encerrando.
- A espera elegível pausa apenas o timeout de geração de 120s do gateway; ao
  retornar, usa o saldo restante. Limites absolutos, budget, kill-switch e
  deadline de approval continuam ativos.

### 3. Criar cartão privado e callback efêmero

Status: concluído

Critérios:
- Um cartão redigido mostra projeto, tarefa curta, resumo fixo da operação,
  motivo genérico, risco mapeado e “uma vez”, com botões Aprovar uma vez/Negar.
- Mensagem abaixo de 4096 chars, sem payload, args, comando, output, path,
  digest, IDs internos ou reason bruto.
- Callback contém token opaco, expira em 10 minutos e vínculo em memória inclui
  user/chat/message/project/workspace/session/run/approval/digest.
- Subscriber é registrado uma vez por EventBus; callback é removido após
  decisão, expiração, falha de envio ou restart.
- Callback expira no `expires_at` persistido do pedido; não inicia relógio novo
  nem estende o prazo em refresh/reconexão.

### 4. Decisão atômica e efeitos terminais

Status: concluído

Critérios:
- Callback revalida allowlist, privacidade, identidade, bindings atuais,
  ApprovalRecord pending/digest, run waiting e worker vivo imediatamente antes
  de chamar o Kernel.
- Aprovação usa `BauerKernel.approve(..., scope="once")` sem executor; o
  ApprovalManager arbitra com o web; `authorize` consome a chamada exata uma
  única vez.
- Negação, TTL, cancelamento e kill-switch encerram o pedido e liberam o
  waiter sem executar a ferramenta.
- HARDLINE/denylist nunca oferece aprovação.
- Somente requests Telegram elegíveis recebem deadline absoluto UTC de 600s;
  web e Telegram arbitram approve, deny e expire desse registro na mesma
  transação. `BAUER_APPROVAL_WAIT_SECONDS` não é autoridade concorrente.
- Requests web/CLI comuns, novos ou legados, não recebem `expires_at` novo e
  mantêm a semântica existente. Endpoint web respeita o prazo se decidir um
  registro Telegram que tenha `expires_at`.
- A migração não reescreve nem invalida pedidos legados sem `expires_at` e sem
  provenance Telegram: aprovações web/CLI mantêm a semântica anterior e não
  ganham botão Telegram. Pedido legado pending com provenance Telegram
  comprovada fica inacessível ao callback e falha fechado.

### 5. Restart fail-closed

Status: concluído

Critérios:
- Tokens antigos deixam de existir e não podem ser reconstruídos do banco.
- Pending de sessão Telegram sem worker vivo é expirado/falha fechada; nenhum
  worker, tool ou objetivo é reexecutado automaticamente.
- Restart mantém web approvals fora deste escopo intactas.
- Nenhuma expiração baseada somente em cache monotônica; deadline persistido é
  fonte de verdade comum entre processos/superfícies.
- Reconciliação de legados não altera web/CLI sem provenance Telegram; somente
  `agent_id=channel.telegram` junto a `input.endpoint=canal:telegram` prova
  origem Telegram. Prefixo de session ID não é prova.

### 6. Testes focados, revisão e gates

Status: concluído

Critérios:
- Adicionar `tests/test_telegram_approval.py` hermético e estender testes
  vizinhos apenas onde necessário.
- Cobrir subscriber instalado antes do pedido e observar `approval_id`/digest
  já registrados durante o publish; evento sem waiter registrado não gera
  cartão.
- Cobrir sucesso uma vez, deny, deadline UTC de 600s, corrida approve/expire e
  web/Telegram concurrentes no mesmo manager/store, cancel cooperativo com
  expiração pending no `finally`, kill-switch, HARDLINE antes de criar record,
  restart, mismatch de todos bindings, digest, redaction e falhas de API.
- Com relógio fake, comprovar que a espera humana não consome timeout de geração
  do gateway (120s), que o tempo restante volta depois e que deadline de 600s e
  limites absolutos do Kernel seguem ativos; comparar com a pausa do stream web.
- Testar migração de um registro web/CLI legado sem `expires_at` e confirmar que
  continua resolvível pela semântica anterior; testar registro legado Telegram
  com provenance positiva e confirmar que fica sem botão/inacessível e falha
  fechado. Provenance desconhecida não deve ser classificada como Telegram.
- Confirmar que request web/CLI novo não recebe `expires_at`; confirmar que
  aprovação via endpoint web de um request Telegram com deadline expira no
  limite de 600s e não pode ser prorrogada.
- Ruff/mypy dos arquivos alterados e revisão de segurança antes dos gates
  completos.

## Validação executada

- `uv sync --frozen --extra dev` passou.
- Suíte completa `uv run pytest tests/ -q --tb=short` passou após corrigir o
  contrato da mensagem de denylist do `execute_code` e validar o endpoint web
  com uma aprovação Telegram expirada.
- Ambos os comandos Ruff definidos no `AGENTS.md` passaram em `bauer/`.
- Mypy nos oito módulos Python alterados passou.
- `git diff --check` passou.

```bash
uv run pytest tests/test_telegram_approval.py tests/test_approval_root_lifecycle.py tests/test_approval_chat_e2e.py tests/test_approval_presentation.py tests/test_policy_engine.py tests/test_policy_serve_modes.py tests/test_execution_control.py tests/test_channel_base.py tests/test_channel_projects.py tests/test_telegram_activity.py -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ tests/test_telegram_approval.py --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ tests/test_telegram_approval.py --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy <módulos Python modificados>
```

Gates finais obrigatórios segundo `AGENTS.md`:

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
```
