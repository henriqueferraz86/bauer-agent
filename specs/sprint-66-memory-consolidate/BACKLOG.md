# BACKLOG — Sprint 66

### 1. `CONSOLIDATED.md` na memória Markdown
Status: concluído
- Entrada em `MEMORY_FILES`/`_HEADERS`, alias `consolidated`.
- `entry_expired` + busca ignorando entradas vencidas.

### 2. Settings, ledger e métricas
Status: concluído
- Opt-in por workspace (padrão desligado), budget e limites.
- Ledger `(session_id, scope_id)` e tabela `runs`.

### 3. Consolidador
Status: concluído
- Seleção fail closed, transcrição redigida, budget, LLM injetável.
- `parse_records` com contrato, escrita atômica, ledger depois.
- Lock por workspace.

### 4. Injeção de fatos válidos
Status: concluído
- `LocalMemoryProvider.prefetch` e `UnifiedMemory.prefetch` incluem só
  entradas não vencidas, rotuladas como referência.

### 5. CLI
Status: concluído
- `bauer memory consolidate [--dry-run …]`, `enable`, `disable`, `status`.

### 6. Testes e gates
Status: concluído
- `tests/test_memory_consolidation.py`.
- Gates do plano 065 verdes.
