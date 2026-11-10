# BACKLOG — Sprint 64

## Tarefas

### 1. Levantar o conjunto completo de falhas Windows

Status: concluído

Os logs de 9 runs do `master` estão na tabela do `SPEC.md`. O teste de
permissão POSIX do `test_telegram_loop` já tinha sido resolvido no master
(`a720e50`).

### 2. Remover o custo quadrático do setup do turno

Status: concluído

`TeamRegistry` valida contra um snapshot de agents, e `load_spec_yaml` faz
cache por conteúdo. As regressões estão em
`tests/test_sprint64_runtime_registry_cost.py` e falham sem a correção: 50
parses por listagem e re-parse a cada chamada.

### 3. Boot hermético em relação ao Docker

Status: concluído

`BAUER_DOCKER_DISCOVERY=0` está no `conftest.py`. A regressão falha sem a
correção, porque o `docker` era executado.

### 4. Testes sensíveis a tempo

Status: concluído

- Gateway: o relógio de geração começa na entrada do turno. Com 0,8s de
  setup injetado, o teste antigo falha com a assinatura do CI e o novo passa.
- Polling de aprovação, de run terminal e de `/loop`, e os joins dos e2e:
  anti-travamento de 30s.

### 4b. Corrida admissão × aprovador rápido

Status: concluído

`BauerKernel._waiting_result` vale nos dois pontos de admissão do
`_preflight` (policy e contrato). O ponto de revalidação K08 (sprint 60) tem
o mesmo padrão e não foi alterado aqui. A regressão está em
`tests/test_sprint64_runtime_registry_cost.py` e falha sem a correção.

### 5. Validar no CI da branch

Status: concluído (runs listados abaixo)

Iterações (workflow_dispatch; jobs `Tests (3.11/3.12 / windows-latest)`):

| Run | Commit | Windows | O que revelou |
|---|---|---|---|
| 42467767343 | 0db5acb | vermelho | canal/approval/accounting verdes; sobraram `session_turns[loop-True]` (5s) e a corrida do stream-approval |
| 42483569748 | b65d827 | **verde/verde** | primeiro run todo verde, antes do merge de #164/#165 |
| 42484846415 | 25f1899 | vermelho | diagnóstico: run `queued` com aprovação concedida, o que expôs a corrida admissão × aprovador |
| 42488352273 | 4b2a063 | 3.12 verde, 3.11 vermelho | `navigation_disconnect` (httpx read timeout de 6s) |
| 42519154399 | 9973d58 | **verde/verde** | |
| 42521458809 | 9973d58 | 3.11 verde, 3.12 vermelho | esperas de 3 a 5s nos testes de canal Agno/governado |
| 42525316354 | 08497e4 | 3.11 verde, 3.12 vermelho | `kanban_server` urlopen de 5s |
| 42527595980 | 8d3c962 | 3.11 verde, 3.12 vermelho | `pending()` sem diagnóstico no caminho deny do `/v1` |
| 42544510927 | dc6310d | **verde/verde** | |

Depois do merge de #166 a #172:

| Run | Commit | Windows | O que revelou |
|---|---|---|---|
| 42699016047 | 04b45a8 | 3.12 verde, 3.11 vermelho | registro terminal do run gravado depois do `done` do SSE (teste do budget) |
| 42704475754 | 807ba59 | 3.11 verde, 3.12 vermelho | worker de memória liberando slot alheio (corrigido no produto) |
| 42709717081 | 8d4b118 | **verde/verde** | |
| 42713245840 | 8d4b118 | vermelho | esperas positivas curtas espalhadas pela suíte, e histórico Agno no cwd (44 mil eventos) |
| 42741683859 | 5fc52d7 | **verde/verde** | |
| 42772715239 | ddfee5c | 3.11 verde, 3.12 vermelho | budget de 15s estourado: estado de runtime compartilhado no checkout |
| 42777493025 | caac398 | **verde/verde** | cwd dos testes fora do checkout + guard |
| 42871846639 | caac398 | 3.11 verde, 3.12 vermelho | run parado em `policy_check` por 30s sem aprovação (diagnóstico de stacks adicionado) |
| **42878237260** | **1156d77** | **verde/verde** | |
| **42919782235** | **1156d77** | **verde/verde** | segundo run consecutivo verde no mesmo commit |

### 6. Hermeticidade do checkout

Status: concluído

Cada teste roda com o cwd num diretório temporário do worker, e o fixture
`_repo_root_stays_clean` falha o teste que criar entrada na raiz do repo.
O histórico Agno foi corrigido no produto (item 6 do SPEC).

## Pendências (com evidência)

- **Congelamento em `policy_check` no Windows (run 42871846639,
  `test_cancel_while_waiting_never_executes_or_resurrects`):** o run ficou
  em `policy_check` por 30s, sem pedido de aprovação persistido. Não se
  repetiu nos dois runs seguintes. Quando voltar, `pending()` imprime as
  stacks das threads `bauer`. Suspeita ainda não confirmada: espera de
  lock SQLite (`busy_timeout = 30000` em `SqliteStateStore._connect`) entre
  conexões do mesmo processo.
- **Custo por evento da governança do canal:** `governance_stop_reason`
  abre várias conexões SQLite (kill-switch, run, budget diário/semanal/
  mensal) a cada evento do stream Agno, cerca de 0,75ms no Linux e mais no
  Windows. Para respostas longas, vale limitar a frequência da checagem de
  budget/kill-switch, mantendo o `cancel_event` imediato.
- **Defaults do produto relativos ao cwd** (`memory/runtime` em
  RunManager, ApprovalManager, BudgetManager e PolicyEngine,
  `memory/*.md`): a suíte agora é isolada deles, mas mover os defaults para
  `$BAUER_HOME` exige migração de dados.
- **Testes lentos que não falham** (retry/backoff real de 15 a 47s em
  `test_turn_partial_accounting`, `test_server_openai_turns` e
  `test_channel_base`).

## Notas de entrega

- Branch: `codex/sprint-64-windows-ci-stability`, empilhada na
  `codex/sprint-62-security-network-web`.
- CI: o workflow só roda em push para `master/main/develop` e em PR. Na branch
  ele é disparado com `workflow_dispatch` (`gh workflow run CI --ref <branch>`).
