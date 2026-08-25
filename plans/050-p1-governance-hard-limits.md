# Plan 050: Tornar a governança P1 efetiva nos limites e aprovações

> **Executor instructions**: trabalhe apenas no worktree e na branch indicados
> pelo revisor. Execute cada gate; se uma condição STOP ocorrer, pare e relate.
> Não altere `plans/README.md`.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: HIGH
- **Depends on**: 049 (DONE)
- **Category**: security, correctness, tests
- **Planned at**: commit `c2d3e4e`, 2026-08-24
- **Completed**: merge `af40d4b` on `master`, 2026-08-25

## Objective

Fechar quatro lacunas comprovadas: a PolicyEngine precisa receber o input de
operação; a decisão HTTP de aprovação precisa mudar corretamente o ciclo de
vida da run; `max_tool_calls` não pode ser ultrapassado por batches; e limites
de custo não podem depender de uma estimativa ausente nem de check-then-record
concorrente.

## Current state

- `bauer/core/kernel/kernel.py:715-724` monta o payload da policy com apenas
  `agent_id` e `request.metadata`, descartando `request.input`.
- `bauer/server.py:597-609` e `bauer/desktop_api.py:991-1011` resolvem
  aprovações diretamente no `ApprovalManager`; `BauerKernel.deny()` é quem
  marca uma run `waiting_approval` como failed (`core/kernel/kernel.py:395-404`).
- `bauer/serve_loop.py:197-214` recebe o log após `turn_fn()` e só então
  consome o orçamento. No servidor, `_turn` em `bauer/server.py:2260-2263`
  ainda não passa o budget para o motor de tools.
- `bauer/agent.py:4183-4286` constrói e despacha o batch; a contagem em
  `:4288-4293` ocorre depois da execução. `AutonomousBudget.remaining_tool_calls`
  já existe em `bauer/autonomous_budget.py:241-242`.
- `bauer/core/policy/engine.py:65-71` passa `estimated_cost_usd` (default zero)
  a `BudgetManager.ensure_can_start`; `core/runtime/autonomy.py:160-218` não
  reserva o valor e grava custo apenas após a run. `JsonlStateStore` só oferece
  locks em memória por arquivo, não uma transação entre processos.
- `bauer/kanban_db.py:221-267` já estabelece o padrão SQLite do projeto
  (WAL e `BEGIN IMMEDIATE`); o orçamento deve reutilizar o desenho, mas nunca
  o banco de Kanban ou de sessões.
- Padrões de teste: `tests/test_kernel.py`, `tests/test_policy_engine.py`,
  `tests/test_server_extended.py`, `tests/test_desktop_api.py`,
  `tests/test_serve_loop.py`, `tests/test_autonomous_budget.py`.

## Scope

O executor pode modificar somente os arquivos necessários abaixo e deve listar
qualquer adição antes de fazê-la:

- `bauer/core/kernel/kernel.py`
- `bauer/core/policy/engine.py`
- `bauer/core/runtime/autonomy.py`
- `bauer/core/runtime/budget_ledger.py` (novo ledger SQLite dedicado)
- `bauer/server.py`
- `bauer/desktop_api.py`
- `bauer/serve_loop.py`
- `bauer/agent.py`
- os testes correspondentes sob `tests/`

Fora de escopo: alteração de API de provider, schema/config público sem teste
de compatibilidade, novo serviço externo, mudanças de UI e migração de dados
de produto. A migração interna, transacional e idempotente do histórico
`run_costs.jsonl` para o ledger de orçamento é permitida.

## Required behavior

1. Policy: dados operacionais em `KernelRequest.input` devem chegar à policy;
   `agent_id` do Kernel não pode ser sobrescrito pelo input/metadata. Campos de
   risco declarados no input devem prevalecer sobre metadata conflitante.
2. Approval HTTP: negar uma aprovação de Kernel deve finalizar a run correta.
   Aprovar não pode responder sucesso se não houver executor recuperável: a
   resposta precisa refletir fielmente `queued/pending_resume`, ou retomar pela
   custódia do Kernel quando o executor for recuperável. Nunca deixe a run em
   `waiting_approval` após uma decisão resolvida.
3. Tool cap: um batch não deve despachar mais ferramentas que
   `budget.remaining_tool_calls()`. O caminho HTTP `/loop` deve usar a mesma
   fonte de verdade e não pode contabilizar duas vezes. Testes com 60 ações e
   teto 50 devem provar que a 51ª não é chamada, não só que o estado final diz
   limite.
4. Cost: criar um ledger SQLite dedicado no `runtime_root`, com WAL e
   `BEGIN IMMEDIATE`. A admissão deve reservar de forma atômica quando houver
   estimativa; `run_id` deve ser idempotente; a reserva precisa ser
   reconciliada/liberada em êxito, falha, cancelamento e recuperação. Migrar o
   histórico JSONL uma vez, de modo repetível, preservando JSONL como audit
   trail. Sem estimativa confiável, registrar reserva zero explicitamente e
   aplicar teto executável durante a run; nunca anunciar reserva zero como
   garantia. Não conte custo duas vezes no servidor.

## Verification

- `uv run pytest tests/test_kernel.py tests/test_policy_engine.py tests/test_server_extended.py tests/test_desktop_api.py tests/test_serve_loop.py tests/test_autonomous_budget.py -q --tb=short`
- `uv run ruff check bauer/ --select E9,F63,F7,F82`
- `uv run pytest tests/ -q --tb=short`

## Done criteria

- Testes de regressão afirmam as quatro propriedades acima, sem rede real.
- Nenhum caminho HTTP deixa uma decisão de aprovação de Kernel silenciosamente
  em `waiting_approval`.
- Nenhuma tool acima do limite é executada no caminho de loop real.
- A contabilidade de custo é idempotente e segura sob admissões concorrentes,
  inclusive entre processos distintos no mesmo runtime root.
- Gates passam; `git diff --check` passa; mudanças permanecem no escopo.

## STOP conditions

- Retomar uma aprovação HTTP requer persistir uma closure de request ou criar
  uma nova infraestrutura de executor fora de escopo. Nesse caso, implemente
  somente a transição segura e explícita para `pending_resume` e reporte o
  desenho necessário; não simule uma retomada.
- A implementação exige nova configuração pública, mudança incompatível sem
  rota de migração, ou não permite recuperação idempotente depois de usar o
  ledger SQLite dedicado.
- A correção de cap exige alterar semântica de tool-calling para todos os modos
  fora de `/loop` sem testes caracterizadores.
- A suíte falha duas vezes por regressão causada pelo lote.

## Commit

`fix(governance): enforce P1 approval and budget limits`

## Execution record

- Policy evaluation now receives `KernelRequest.input`, while the Kernel-owned
  `agent_id` remains authoritative and risk facts cannot be downgraded through
  metadata.
- Server and desktop approval decisions use the Kernel lifecycle transition;
  approvals without a recoverable executor become explicitly `queued`.
- Tool-call budget is reserved before dispatch, including the `/loop` path,
  with regression coverage proving a 51st call is not executed under a cap 50.
- Runtime cost admission now uses an idempotent SQLite ledger with WAL and
  `BEGIN IMMEDIATE`; it migrates the JSONL audit history once and releases or
  settles reservations on terminal states.
- Verification: focused regression suite, critical Ruff and `git diff --check`
  passed; the full suite passed before and after merge. The broad Ruff command
  still reports the same 87 pre-existing violations as `master`.
