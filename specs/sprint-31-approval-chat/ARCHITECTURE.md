# ARCHITECTURE — Aprovação no chat e retomada da execução

## Fluxo

O ToolRouter converte falta de permissão binária em ask antes da execução,
mesmo em audit. A autorização consumida passa ao ShellRunner por contexto
local da chamada, vinculado aos argumentos completos e instalado na thread
que executa a tool. O contexto é removido em finally; não altera a allowlist
compartilhada nem autoriza outras chamadas concorrentes.

1. `bauer/server.py:/stream` faz a admissão no Kernel.
2. Se a decisão for `ask`, o run fica `waiting_approval` e o stream envia um
   evento SSE `approval` com uma projeção pública do `ApprovalRecord`.
3. O mesmo stream permanece aberto, observando somente o status persistido da
   aprovação. Heartbeats não carregam conteúdo de ferramenta ou payload.
4. A aba `Approvals` ou o cartão inline chama
   `POST /api/approvals/{id}/approve|deny`.
5. Em `approve`, o Kernel mantém a transição formal para `queued`; o stream
   chama `start_run` uma única vez e inicia o worker original. Em `deny`, o
   Kernel finaliza a run como `failed`.
6. O Chat atualiza o cartão a partir do evento SSE e continua consumindo os
   deltas normais da mesma conexão.

Quando a policy é acionada durante uma tool (por exemplo, `run_command`), o
`ToolRouter` marca a run como `waiting_approval` e aguarda o mesmo
`ApprovalManager`. O loop SSE detecta a aprovação pendente por `run_id` e envia
o cartão. Depois de `approve`, a tool retoma e a run volta a `running`; não há
uma segunda mensagem do usuário nem uma repetição cega do turno.
Routers criados pelo adapter Agno recebem o mesmo `event_bus`, `policy_root`,
modo de policy e contexto de espera; sem essa propagação, o adapter criaria
uma solicitação nova após cada aprovação em vez de retomar a chamada original.
O `ShellRunner` desses routers também deriva `safe_mode`, timeout, limite de
saída e comandos extras da seção global `tools`, como o runtime nativo. No
stack Docker, a permissão binária para `docker` é declarada por ambiente porque
o serviço já monta explicitamente o socket; a policy `enforce` continua pedindo
aprovação humana antes de cada operação sensível.

## Segurança e privacidade

- O evento SSE contém apenas campos equivalentes ao cartão de Approvals:
  `id`, `run_id`, `session_id`, `operation`, `tool_name`, `reason`, `risk_level`
  e `status`.
- `payload` não é enviado ao Chat.
- O endpoint REST continua protegido pelo mecanismo de autenticação/CSRF já
  existente.
- Decisão é lida do `ApprovalManager`, não de estado mantido no navegador.

## Concorrência

- O status persistido é a fonte de verdade quando a decisão acontece na aba
  `Approvals`.
- O worker só é iniciado depois de observar `approved` e após uma transição
  para `running`; uma flag local impede inicialização duplicada.
- Se o cliente desconectar enquanto a aprovação ainda estiver pendente, a run
  não será cancelada automaticamente; ela continua disponível na aba
  `Approvals`.

## Compatibilidade

### Revisão de raiz (plano 068)

A decisão aceita `scope=once` por default; `scope=run` exige escolha humana
explícita e aplica-se apenas a shell.execute/run_command na mesma run/sessão.
Deny e guards sempre prevalecem. Decisões repetidas não reiniciam a run;
decisão antiga não altera estado terminal nem ganha escopo maior.
O worker aguarda também a transição de estado antes de executar a chamada.
Cancelamento/negação/expiração não podem se tornar conclusão bem-sucedida.
Os cartões são indexados por ID, sem sobrescrever solicitações distintas.
O comando é validado antes de solicitar aprovação, sem habilitar shell=True.
Compose é verificado na imagem; localhost do container não representa o host.

- Runs que não exigem aprovação mantêm o caminho atual.
- A rota REST não muda.
- O evento novo é ignorado por clientes antigos, que ainda recebem o texto de
  espera e o `done` quando aplicável.
