# ARCHITECTURE — Sprint 52

## Módulos

| Arquivo | Conteúdo |
|---|---|
| `families/data.py` | `DataSettings` (orçamentos), leitura em streaming CSV/TSV/JSONL, JSON, coerção de tipos, `_Budget`, artefatos atômicos (`.tmp` → `replace`), SVG |
| `families/sql.py` | `SqlSettings`, `guard_statement`, `fingerprint`, `_engine` (read-only por dialeto), `_run` (timeout, rollback, truncamento) |
| `bridge.py` | `FactoryContext(workspace)` passado a toda factory |
| `agno_adapter.py` | passa `self.workspace` à ponte |

## SQL — camadas

```text
modelo: run_query(connection, sql)
  → ToolRouter (data.query) → ponte (sem segredo nos args? n/a: operação local)
  → conexão nomeada? (senão erro com nomes disponíveis)
  → guard_statement: literais/comentários removidos → 1 instrução →
    SELECT/WITH → sem palavras/funções proibidas → allowlist FROM/JOIN
  → _engine: sqlite mode=ro + query_only | pg/mysql READ ONLY + timeout
  → execute → fetchmany(max_rows+1) → rollback → dispose (NullPool)
```

## Data — escopo

`_resolve` resolve o caminho contra o workspace (symlinks incluídos) e exige
`relative_to(workspace)`; extensão em allowlist; tamanho máximo. Artefatos
usam slug (`[a-z0-9_-]`), então `../../x` vira `x`.

## Riscos

- A guarda léxica é conservadora (pode recusar SQL válido com palavras
  reservadas em identificadores sem aspas); falso negativo de escrita é
  coberto pela camada do driver.
- Read-only de PostgreSQL/MySQL depende também do usuário de banco (atestado
  pelo operador); a transação READ ONLY é defesa adicional.
