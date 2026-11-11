# SPEC — Sprint 76: conexões SQLite reutilizadas no runtime (plano 068)

## Problema

`SqliteStateStore` (`runtime_state.sqlite3`) e `BudgetLedger`
(`budget_ledger.sqlite3`) abriam e fechavam uma conexão SQLite a cada
leitura, escrita e evento publicado. Cada `EventBus.publish` faz três
escritas (evento, auditoria, trace), e a checagem de governança do canal lê
kill-switch, run e budget a cada evento do stream Agno.

A run 42935274304 (tentativa 2, `Tests (Python 3.12 / windows-latest)`)
falhou em
`test_approval_chat_e2e.py::test_real_agno_chat_approval_runs_each_command_once[text-1]`:
o run ficou em `running`, sem aprovação, e a única thread interessante estava
em `state_store.py:134 _connect`, no `PRAGMA synchronous = NORMAL` de uma
conexão nova, chamada de `EventBus.publish → _record_observability →
traces.record_event → store.append`.

## Causa-raiz (verificação)

Hipótese do plano: rotatividade de conexões. Em WAL, fechar a última conexão
de um arquivo faz checkpoint e apaga `-wal` e `-shm`; a próxima abertura
reconstrói os dois sob lock exclusivo, e outra conexão abrindo nesse
intervalo espera no busy handler.

O que foi verificado:

1. **A rotatividade existe e é total.** Medido no Linux com o código de
   `26554d9`: depois de cada um de 300 `publish` seguidos, o `-wal` não
   existia mais (300/300). Toda operação passava pelo ciclo completo de
   criar `-wal`/`-shm`, recuperar o índice, checkpoint e apagar.
2. **A thread presa estava numa espera de lock, não em CPU.** `PRAGMA
   synchronous` precisa do schema (`PragFlg_NeedSchema` no SQLite), ou seja,
   é o primeiro comando que abre de fato o banco, mapeia o `-shm` e, se o
   `-wal` acabou de ser apagado, roda a recuperação do índice WAL. A espera
   durou o prazo inteiro do teste (30s), que coincide com o `busy_timeout =
   30000` da conexão.
3. **Volume:** um turno governado mínimo (5 eventos) abria 59 conexões; um
   turno Agno simples pelo canal abria 182 (114 no store de runtime e 62 no
   ledger de budget, por causa da checagem de governança por evento); o e2e
   de aprovação que falhou abria cerca de 280 por teste, contando o polling.

Não foi possível reproduzir a espera de 30s localmente: ela depende do
Windows (fsync e remoção de arquivo lentos, antivírus abrindo arquivo
recém-criado, que deixa o `-wal` em "delete pending"). A confirmação final é
o aceite no CI do Windows (BACKLOG).

## Escopo

- `bauer/core/runtime/sqlite_connections.py` (novo): pool de conexões por
  arquivo, por processo.
- `SqliteStateStore` e `JsonlStateStore`: usam o pool e expõem `close()`.
- `BudgetLedger`: mesmo pool e `close()` (a medição mostrou a mesma
  rotatividade: 62 conexões por turno Agno).
- `restore_snapshot`: fecha as conexões do processo antes de substituir os
  arquivos.
- `tests/conftest.py`: fixture autouse que fecha todas as conexões ao fim de
  cada teste.

## Decisão de design: as duas coisas

**Uma conexão por thread por arquivo, mais uma conexão âncora por arquivo.**

- Só conexão por thread não basta: o runtime cria muitas threads curtas
  (thread órfã do `/stream`, workers de canal, thread de cada request). A
  conexão de uma thread que morre é fechada; se fosse a última, o `-wal`
  seria apagado de novo.
- Só a âncora compartilhada não serve: uma conexão única entre threads
  exigiria serializar também as leituras, e um leitor veria a transação
  aberta de outra thread. O contrato é "leitores não são afetados por
  writers".
- A âncora abre o banco, lê o schema (o que mapeia o `-shm`) e fica ociosa.
  Enquanto ela existe, nenhum outro fechamento é o último, então `-wal` e
  `-shm` vivem o processo inteiro. O WAL não cresce sem limite: o
  autocheckpoint (1000 páginas) continua rodando nos commits.

O contrato de escrita é o mesmo: `RLock` por arquivo, `BEGIN IMMEDIATE`,
retry em `locked`, `busy_timeout` de 30s no store e de 5s no ledger.

### Regras do pool

- **Thread safety:** cada conexão é usada só pela thread dona.
  `check_same_thread=False` existe apenas para que `close()` (teardown,
  `atexit`) feche conexões de outras threads; uma conexão emprestada no
  momento do `close()` só é fechada quando a dona a devolve.
- **Uso reentrante na mesma thread** (ler dentro de `transaction()`): abre
  uma conexão temporária, como antes, para não enxergar nem misturar a
  transação aberta.
- **Threads encerradas:** a conexão de uma thread morta é fechada na próxima
  abertura de conexão do mesmo arquivo. Um `ident` de thread reaproveitado
  não herda a conexão de outra thread.
- **Arquivo apagado ou substituído por fora:** cada empréstimo compara
  `(st_dev, st_ino)` do arquivo com o registrado na abertura da âncora. Se
  mudou, o pool fecha tudo e reabre.
- **`fork`:** o filho não usa nem fecha as conexões herdadas (o SQLite
  proíbe levar conexão aberta através do fork); elas ficam referenciadas e o
  filho abre as suas. Os locks de registro e os `RLock` de escrita são
  recriados no filho. Subprocessos com `exec` não herdam os handles (o
  SQLite abre com `O_CLOEXEC` e, no Windows, sem herança).
- **Ciclo de vida:** `store.close()` fecha as conexões do arquivo no processo
  (de todos os stores desse arquivo); o próximo uso reabre. `atexit` fecha
  tudo no fim do processo. A âncora fecha por último, então o checkpoint de
  fechamento roda uma vez só e o `-wal`/`-shm` são removidos.
- **Limite de arquivos abertos:** só os `MAX_OPEN_POOLS` (16) arquivos usados
  mais recentemente mantêm conexões. Usar um 17º fecha o pool ocioso mais
  antigo, que reabre sob demanda. Um pool com conexão emprestada não é
  fechado. Sem esse limite, um processo que toca um banco novo por run (o
  dispatcher do autopilot) acumulava cerca de 5 descritores por caminho.
- **Quem cria um runtime num diretório temporário fecha os pools antes de
  apagá-lo** (`close_all_state_stores()` ou `store.close()`). No Windows,
  arquivo aberto não pode ser apagado.
- **Manutenção:** `assert_runtime_writable` continua sendo checado a cada
  operação. O restore exige o Bauer parado (`--confirm-stopped`); no mesmo
  processo, ele fecha o pool antes do `os.replace`.

## Fora de escopo

- Trocar o SQLite ou mudar o formato do estado persistido.
- `SqliteSessionStore` (`sessions.db`): 6 a 8 conexões por turno, contra 114
  e 62 nos dois bancos do runtime. A medição não justifica mudar.
- Banco do Agno (`SqliteDb`, engine SQLAlchemy com pool próprio): 1 conexão
  por teste.
- `decisions.db` e `vector_store.db`: 2 a 3 conexões por teste.
- Agrupar as três escritas de um `publish` numa transação só.

## Critérios de aceite

1. Um turno governado abre pelo menos 10 vezes menos conexões que
   operações de store (teste hermético que conta `sqlite3.connect`).
2. O `-wal` não é apagado entre operações.
3. Depois de `close()`, nenhum handle fica aberto: `-wal`/`-shm` removidos,
   nenhum fd para os arquivos em `/proc/self/fd` (Linux) e o arquivo pode ser
   renomeado (falharia no Windows com handle aberto).
4. Escritas concorrentes de várias threads sem conexão compartilhada;
   leitores não veem transação aberta de outra thread.
5. Filho de `fork` funciona com conexões próprias.
6. Os testes de aprovação ponta a ponta passam em 5 rodadas seguidas dos dois
   jobs do Windows no mesmo commit.

## Plano de validação

```bash
uv sync --frozen --extra dev
uv run pytest tests/ -q --tb=short
uv run --with ruff==0.17.0 ruff check bauer/ --select E9,F63,F7,F82
uv run --with ruff==0.17.0 ruff check bauer/ --select E,F,W --ignore E501,W291,W293,E302,E303
uv run mypy bauer/
```

Mais o CI do Windows, disparado repetidamente na branch, com os IDs das runs
registrados no BACKLOG.
