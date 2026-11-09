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

Os runs de confirmação depois do merge de #166 estão nas notas de entrega.

## Notas de entrega

- Branch: `codex/sprint-64-windows-ci-stability`, empilhada na
  `codex/sprint-62-security-network-web`.
- CI: o workflow só roda em push para `master/main/develop` e em PR. Na branch
  ele é disparado com `workflow_dispatch` (`gh workflow run CI --ref <branch>`).
