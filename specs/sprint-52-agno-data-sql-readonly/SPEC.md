# SPEC — Sprint 52: Bauer Data local e SQL somente leitura (plano 057, sprint "20")

Depende das sprints 50 (ponte) e 51 (padrões de allowlist/credencial).

## Problema

Os toolkits de dados do Agno (`PandasTools`, `CsvTools`, `SQLTools`,
`PostgresTools`, `DuckDbTools`, `VisualizationTools`…) executam operações
arbitrárias, leem caminhos sem escopo ou rodam SQL sem modo read-only
verificável nem cancelamento. `pandas`/`duckdb`/`psycopg` nem estão no lock.

## Escopo

1. **`bauer.data`** (stdlib, sem pandas): `describe_file`, `query_file`
   (filtro, agrupamento, agregações count/sum/mean/min/max, ordenação),
   restritos a CSV/TSV/JSON/JSONL **dentro do workspace do agente**, com
   orçamentos configuráveis de tamanho de arquivo, linhas varridas, tempo e
   linhas retornadas (truncamento sinalizado).
2. Artefatos locais: `save_report` (md/csv/json) e `save_chart` (SVG de barras,
   texto escapado) gravam só em `<workspace>/.bauer/artifacts/data/<slug>`;
   arquivo de origem nunca é alterado; sem upload. São ações `write` que
   exigem declaração explícita e passam pela operação `filesystem.write` do
   PolicyEngine (a mesma de `bauer.write_file`).
3. **`bauer.sql`** com conexões nomeadas pelo administrador:
   `run_query`, `list_tables`, `describe_table`. Read-only em duas camadas:
   - guarda de instrução: uma instrução, só `SELECT`/`WITH`, nenhuma
     palavra-chave mutante/de sessão (após remover comentários e literais),
     funções perigosas bloqueadas (`pg_sleep`, `load_extension`, …),
     allowlist de schemas/tabelas em todo `FROM`/`JOIN`/vírgula;
   - guarda do driver: SQLite com `mode=ro` + `PRAGMA query_only`;
     PostgreSQL/MySQL em transação `READ ONLY` com timeout no servidor e
     **usuário read-only declarado** (`credential_is_read_only: true`), URL
     por nome de credencial. Sempre `rollback`.
   - timeout com cancelamento real (SQLite: progress handler; servidores:
     `statement_timeout`/`max_execution_time`), limite de linhas e de
     tamanho de célula; log só com fingerprint da consulta.
4. Toolkits de dados/SQL do SDK `incompatible` com motivo e substituto.
5. Ponte: factories recebem `FactoryContext(workspace)` do adapter (nunca
   do modelo).

## Fora de escopo

- Escrita em banco (nenhuma ação classificada); outros dialetos (Oracle,
  MSSQL, BigQuery…) — recusados por não terem read-only verificável aqui.
- pandas/duckdb como dependência obrigatória.

## Decisões (sem operador — opção mais segura)

- Análise em stdlib: evita dependência pesada e mantém instalação ausente
  irrelevante (capability sempre pronta, mas só concedida por declaração).
- Dialeto sem modo read-only verificável ⇒ não suportado (condição STOP do
  plano), em vez de confiar só no parser.
- Config SQL rejeita `url` em claro (campo inexistente; `extra=forbid`).

## Critérios de aceite

- [x] Consultas de leitura válidas funcionam; resultado grande truncado.
- [x] Escrita/DDL/múltiplas instruções/PRAGMA/ATTACH bloqueados; tabela fora
  da allowlist negada (inclusive via JOIN e vírgula).
- [x] Mesmo contornando o parser, o driver SQLite recusa escrita.
- [x] Consulta acima do timeout é cancelada.
- [x] Arquivos fora do workspace negados; orçamento de linhas sinalizado.
- [x] Artefatos só em `.bauer/artifacts/data/`; origem intacta.
- [x] Suíte e Ruff verdes.
