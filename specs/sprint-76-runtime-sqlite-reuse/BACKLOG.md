# BACKLOG — Sprint 76

## Tarefas

### 1. Verificar a hipótese da rotatividade

Status: concluído

Log da run 42935274304 (tentativa 2, job 130256004351): a única thread do
Bauer estava em `state_store.py:134 _connect` (`PRAGMA synchronous`, o
primeiro comando que abre o banco) durante os 30s do prazo, igual ao
`busy_timeout`. No Linux, com o código de `26554d9`, o `-wal` some depois de
300 de 300 `publish` seguidos. Detalhes no SPEC.

### 2. Medir antes

Status: concluído

| Cenário | Conexões abertas |
|---|---|
| Turno governado mínimo (5 eventos, processo aquecido) | 59 (56 store, 3 ledger) |
| `test_agno_happy_path_completes_simple_turn_through_channel` | 182 (114 store, 62 ledger, 6 sessions.db) |
| `test_real_agno_chat_approval_runs_each_command_once[text-1]` | 279 (255 store, 10 ledger, 8 sessions.db, 6 outros) |

`publish` (3 escritas): 2,35ms por evento no Linux.

### 3. Pool de conexões por arquivo (por thread + âncora)

Status: concluído

`bauer/core/runtime/sqlite_connections.py`, usado por `SqliteStateStore` e
`BudgetLedger`. `close()` nos dois, `close_all_state_stores()`, `atexit`,
`register_at_fork`, fechamento antes do `os.replace` do restore.

### 4. Testes

Status: concluído

`tests/test_sprint76_runtime_sqlite_reuse.py`. Sem a correção, falham as
regressões de contagem por turno (59 conexões para 59 operações), de `-wal`
apagado depois da primeira operação e de `close()`/thread/fork (API
inexistente). A fixture `_close_runtime_sqlite_connections` do `conftest.py`
fecha tudo ao fim de cada teste; `_repo_root_stays_clean` continua valendo.

### 5. Medir depois

Status: concluído

| Cenário | Antes | Depois |
|---|---|---|
| Turno governado mínimo (processo aquecido) | 59 | 0 |
| Turno Agno pelo canal (store + ledger) | 176 | 6 |
| e2e de aprovação `[text-1]` (store + ledger) | 265 | 15 |
| e2e de aprovação `[text-1]` (todos os bancos) | 279 | 29 |

`publish`: de 2,35ms para 1,29ms por evento no Linux (média de 300). O
`-wal` permanece entre operações (0 de 300 ausentes).

### 6. Aceite no CI do Windows

Status: concluído

Meta: 5 runs seguidas com os dois jobs `Tests (Python 3.11/3.12 /
windows-latest)` verdes no mesmo commit.

As 5 runs de `c1acb2f` (42944307246, 42944341042, 42944350746, 42944360287,
42944370021) falharam no Windows, sempre nos mesmos dois testes. Nenhuma
falha foi nos e2e de aprovação. As duas causas são efeito da própria
correção, e estão na tarefa 7.

| Run | Commit | Windows 3.11 | Windows 3.12 | Observação |
|---|---|---|---|---|
| 42947807652 | `125dd3d` | verde | verde | |
| 42947815878 | `125dd3d` | verde | verde | |
| 42947824369 | `125dd3d` | verde | verde | |
| 42947832861 | `125dd3d` | verde | verde | |
| 42947841542 | `125dd3d` | verde | verde | |

O job `E2E smoke (provider real)` falha nessas runs e no master pelo mesmo
motivo: o provider gratuito responde `Model is unavailable`. Ele não faz
parte do aceite.

### 7. Correções depois da revisão e do primeiro CI

Status: concluído

- **Pools sem limite** (`4dd7c41`, revisão): cada caminho de banco diferente
  mantinha cerca de 5 descritores até o fim do processo. O dispatcher do
  autopilot lê um `autopilot_runtime/<run>` novo por run, então chegaria ao
  `EMFILE` depois de uns 200 runs, e no Windows deixaria esses diretórios
  travados. Agora só os `MAX_OPEN_POOLS` (16) arquivos usados mais
  recentemente ficam abertos (LRU). Os pools ociosos mais antigos fecham e
  reabrem sob demanda, e um pool com conexão emprestada nunca é fechado.
  `_find_kernel_gate_attestation` e os helpers de relatório do benchmark
  fecham o store depois do uso.
- **Scorecard no Windows** (`test_tabela_do_plano_esta_em_dia`): a medição
  de aprovação em `evals/harness/medir.py` roda um Kernel dentro de um
  `TemporaryDirectory`. O pool mantinha `runtime_state.sqlite3` aberto, o
  Windows recusava apagar o diretório e a capacidade era contada como
  ausente. A medição agora fecha os pools antes da limpeza.
- **Duração 0.0 no Windows** (`test_executor_exception_fails_run`): com as
  conexões reutilizadas, um run que falha cabe dentro da resolução de
  `time.monotonic()` no Windows, que é de cerca de 15,6ms. O Kernel passou a
  medir a duração com `time.perf_counter()`.
- `test_each_thread_uses_its_own_connection` era instável: o `id()` de uma
  conexão coletada podia ser reaproveitado. O teste agora segura as
  conexões.

## Pendências

- `SqliteSessionStore` (`sessions.db`) continua abrindo uma conexão por
  operação (6 a 8 por turno). Não mexido: a medição não mostrou o mesmo
  peso.
