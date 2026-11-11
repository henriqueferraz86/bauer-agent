# ARCHITECTURE — Sprint 76

## Antes

```text
store.append()
  └─ _connect(): sqlite3.connect → PRAGMA busy_timeout → PRAGMA synchronous
       │          (primeiro comando que abre o banco: cria -shm, recupera o WAL)
       ├─ BEGIN IMMEDIATE … INSERT … COMMIT
       └─ conn.close()  ← última conexão: checkpoint + apaga -wal e -shm
```

Três vezes por `EventBus.publish` e várias vezes por evento do stream Agno
(kill-switch, run, budget diário/semanal/mensal).

## Depois

```text
SqliteStateStore(root) ─┐
BudgetLedger(root)     ─┼─ pool_for(path, configure) ─▶ SqliteConnectionPool (1 por arquivo/processo)
                        │                                ├─ anchor: conexão ociosa (segura -wal/-shm)
                        │                                └─ entries[thread ident] → conexão da thread
store._connect()
  ├─ assert_runtime_writable(root)        (como antes, a cada operação)
  └─ pool.connection()
       ├─ checa identidade do arquivo (st_dev, st_ino) → mudou: fecha tudo
       ├─ conexão da thread livre → empresta (depth = 1)
       ├─ conexão da thread emprestada (reentrância) → conexão temporária
       └─ sem conexão → abre âncora se faltar, abre a da thread,
                        fecha conexões de threads mortas
```

`configure` roda uma vez por conexão nova: no store, `busy_timeout`, o
`journal_mode = WAL` uma vez por processo (commit `eb65424`) e `synchronous
= NORMAL`; no ledger, os três pragmas de antes.

## Arquivos

| Arquivo | Mudança |
|---|---|
| `bauer/core/runtime/sqlite_connections.py` | novo: `SqliteConnectionPool`, `pool_for`, `close_pool`, `close_all_pools`, `atexit` e `register_at_fork` |
| `bauer/core/runtime/state_store.py` | `_connect` empresta do pool; `_configure`; `close()`; `close_all_state_stores()`; `RLock` de escrita resolvido por chave em cache e recriado no filho de fork |
| `bauer/core/runtime/budget_ledger.py` | `_connect` empresta do pool; `close()` |
| `bauer/core/runtime/snapshot.py` | `restore_snapshot` fecha o pool dos arquivos antes do `os.replace` |
| `tests/conftest.py` | fixture autouse `_close_runtime_sqlite_connections` |
| `tests/test_sprint76_runtime_sqlite_reuse.py` | regressões e medição |

## Fechamento

```text
pool.close()
  ├─ generation += 1        (entradas antigas viram obsoletas)
  ├─ conexões ociosas: close()
  ├─ conexões emprestadas: discard = True → a thread dona fecha ao devolver
  └─ âncora: close() por último → checkpoint único, -wal/-shm removidos
```

O pool continua utilizável depois de `close()`: o próximo empréstimo reabre
âncora e conexão. Por isso a fixture do `conftest` pode fechar tudo ao fim de
cada teste mesmo com threads órfãs de um teste anterior ainda rodando.

## Fork

`os.register_at_fork(after_in_child=...)` recria os locks do registro e de
cada pool, move as conexões herdadas para `_INHERITED` (nunca usadas nem
fechadas no filho) e incrementa a geração. Para plataformas sem
`register_at_fork`, o pool também compara `os.getpid()` a cada empréstimo.
`state_store` recria `_SQLITE_LOCK_GUARD` e esvazia `_SQLITE_WRITE_LOCKS` no
filho; o store busca o `RLock` pela chave a cada escrita, então não guarda
um lock copiado do pai.
