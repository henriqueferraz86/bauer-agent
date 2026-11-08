# BACKLOG — Sprint 52: Bauer Data e SQL read-only

- [x] `FactoryContext(workspace)` na ponte e no adapter.
- [x] `bauer.data`: describe/query com orçamentos; save_report/save_chart em
      `.bauer/artifacts/data/` sob `filesystem.write`.
- [x] `bauer.sql`: settings estritos (sqlite path; servidores por credencial
      nomeada + atestado read-only), guarda de instrução, read-only no driver,
      timeout com cancelamento, truncamento, fingerprint.
- [x] SDK `pandas`, `csv_toolkit`, `visualization`, `sql`, `postgres`,
      `duckdb`, `redshift`, `neo4j`, `google.bigquery` `incompatible`.
- [x] Testes `tests/test_agno_data_capabilities.py` (SQLite real em tmp,
      tentativa de escrita direta no driver, timeout com CTE recursiva).
- [x] Gates: suíte completa, Ruff bloqueante e de estilo.
