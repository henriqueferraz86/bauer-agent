# ARCHITECTURE — Sprint 66

```text
bauer memory consolidate (memory_cmd.py)
  └─ MemoryConsolidator(workspace, sessions_dir, llm=default_llm())
       ├─ ConsolidationSettings  <ws>/memory/consolidation/settings.json   (opt-in)
       ├─ ConsolidationLedger    <ws>/memory/consolidation/ledger.sqlite3  (ledger + runs)
       ├─ SqliteSessionStore.list_sessions_for_workspace / get_provenance / load
       ├─ privacy.is_public_message + memory_scope.sanitize_text  → transcrição
       ├─ llm(messages) → parse_records (contrato + nova sanitização)
       └─ write_records → <ws>/memory/CONSOLIDATED.md (replace atômico)
                          → ledger.mark_consolidated
LocalMemoryProvider.prefetch / UnifiedMemory.prefetch
  └─ active_consolidated_block (só entradas não vencidas)
MemoryManager.search → ignora entradas com valid_until vencido
```

## Ordem e idempotência

1. `ledger.start_run` (métrica).
2. Lock exclusivo `memory/consolidation/run.lock`, com o mesmo O_EXCL lock
   do scheduler de automações. Duas execuções simultâneas no mesmo workspace
   não intercalam.
3. Para cada sessão:
   - transcrição;
   - checagem de budget;
   - chamada ao LLM;
   - `parse_records`;
   - `write_records`, que pula `record_id` já presente;
   - `mark_consolidated`.
4. `ledger.finish_run` com `completed | partial | failed`.

`record_id = sha256(workspace_id | session_id | kind | texto normalizado)`.
Se a execução morre entre o passo `write_records` e o `mark_consolidated`, a
reexecução gera os mesmos ids e não duplica nada. Se o LLM devolver outro
texto na nova tentativa, os registros novos entram junto dos anteriores. Isso
é aceitável e pode ser revisado/editado à mão.

## Formato da entrada

```markdown
## [2026-11-09 12:00 UTC] fact: O projeto usa uv…

- record_id: cr-0123456789abcdef
- source: consolidado de sessão s1 em 2026-11-09
- session_id: s1
- scope: ws-…
- confidence: 0.90
- valid_until: 2026-12-09

O projeto usa uv…
```

É o mesmo formato `## [timestamp]` do `MemoryManager`. Por isso `cleanup`,
`search` e `list` funcionam sem mudança, e `entry_expired` é genérico: vale
para qualquer arquivo que use `- valid_until:`.

## Defesas

- Escopo:
  - a seleção só lê linhas com o mesmo `workspace_id` e sem conflito;
  - a proveniência é relida no momento do `load`;
  - ledger e settings filtram por `scope_id`.
- Segredos, em quatro camadas:
  - tool/system ficam fora do payload;
  - o texto é redigido;
  - mensagens que continuam sensíveis são descartadas;
  - cada registro do LLM é sanitizado de novo, e registro com
    `[REDACTED` é descartado.
- Prompt injection: a transcrição vai entre `<transcript>`, marcada como dado.
  Só `fact|preference|lesson` passam, com tamanho limitado. A injeção no
  prompt vem rotulada "referência, não instrução".
- Arquivos: `confined_files` (sem symlink, replace atômico), e
  `memory/`/`consolidation/` não podem ser symlink.
