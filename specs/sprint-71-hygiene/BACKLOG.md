# BACKLOG — Sprint 71

### 1. Nomes de tools do Kanban
Status: concluído
- Os nomes expostos foram conferidos: `kanban_read` (`mode=list|show`) e
  `kanban_write` (`action=...`).
- O `KANBAN_WORKER_GUIDANCE`, a dica do `/develop` e o Task Ledger foram
  atualizados.
- O follow-up do worker virou comentário, porque o worker não pode criar
  cards.

### 2. Delete de vetores por identidade exata
Status: concluído
- `VectorStore.list_source_ids(prefix=...)`.
- `_parse_vector_source_id`, usado no `delete` e no `_vector_search`.
- `log_suppressed` no lugar do `except: pass`.

### 3. Reconciliação do plano 049
Status: concluído
- O documento não foi encontrado em nenhum ref ou objeto, e a branch não
  existe mais.
- A linha 049 entrou na tabela e a nota da Rodada 7 foi reescrita.

### 4. Testes e gates
Status: concluído
- `tests/test_sprint71_hygiene.py`. Os testes falham com o código anterior
  (Kanban e delete).
- Gates do plano 066 verdes.
