# ARCHITECTURE — Sprint 71

## Kanban no prompt

```text
agent.py
  KANBAN_WORKER_GUIDANCE  → kanban_read(mode="show") · kanban_write(action=complete|block|comment)
  /develop (_kanban_hint) → kanban_read(mode="list") · kanban_write(action="complete")
  _ledger_block           → kanban_write(action="complete"|"block")
tool_router.py            → expõe só kanban_read / kanban_write
tools/kanban.py           → gate por ação; worker: heartbeat|comment|complete|block
```

O teste usa `tokenize` em `bauer/agent.py`, então só os literais de string
contam, não os identificadores Python como `_kanban_create`. Cada nome
`kanban_*` encontrado precisa estar em `ToolRouter._tools`.

## Delete de vetores por identidade

```text
SqliteSessionStore.delete(sid)
  DELETE FROM sessions WHERE session_id = sid
  VectorStore.list_source_ids("session_msg", prefix=f"{sid}:")   # estreita (LIKE escapado)
    para cada source_id:
      _parse_vector_source_id(source_id) = rsplit(":", 2) → (sessão, role) com idx numérico
      sessão == sid (comparação exata, sensível a caixa) → VectorStore.delete(source_id)
```

`role` e `idx` nunca contêm `:`, então o parse pela direita é inequívoco
mesmo com `tg:1:user:0` ou `agent:x:1:assistant:3`. A busca vetorial
(`_vector_search`) usa o mesmo parse.

## Compatibilidade

- `list_source_ids(source_type)` sem `prefix` continua igual.
- `delete_prefix` continua disponível no `VectorStore`, mas o
  `SqliteSessionStore` não o usa mais.
- O formato do `source_id` não mudou e não há migração.
