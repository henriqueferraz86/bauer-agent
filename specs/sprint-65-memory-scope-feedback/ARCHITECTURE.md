# ARCHITECTURE — Sprint 65

## Componentes

```text
bauer agent (agent_cmd.py)
  └─ SqliteSessionStore(sessions_dir, workspace=router.workspace)
        sessions.workspace_id / workspace_path / workspace_conflict
agent.py  /thumbsup|/thumbsdown [motivo]
  ├─ FEEDBACK.md (legado, agora redigido)
  ├─ memory_feedback.record_turn_feedback(router.workspace, ...)
  │     └─ FeedbackStore(<ws>/memory/consolidation/feedback.sqlite3)
  ├─ DecisionMemory.update_latest_outcome (inalterado)
  └─ skill_stats (inalterado)
memory_scope.py  WorkspaceScope · sanitize_text · validate_scope_kind
```

## Proveniência das sessões

| Gravação anterior | Gravação atual | Resultado |
|---|---|---|
| (nenhuma) | store com workspace W | `workspace_id=W`, conflito 0 |
| (nenhuma) | store sem workspace | `workspace_id=NULL` (inelegível) |
| `NULL` (histórica/migrada) | qualquer | continua `NULL` |
| `W` | store com W | inalterado |
| `W` | store com outro workspace ou sem workspace | `workspace_conflict=1` (inelegível) |

A regra roda no próprio `ON CONFLICT` do upsert, numa única instrução. Não
existe janela entre leitura e escrita. `workspace_id` nunca é reescrito no
UPDATE.

## Fronteira de escopo do feedback

- O banco fica dentro do workspace, então a separação física já é a regra.
- Há também uma segunda barreira: toda consulta filtra
  `workspace_id = scope.workspace_id`, e um banco copiado de outro workspace
  devolve vazio.
- `WorkspaceScope.ensure_state_dir` recusa `memory/` ou `memory/consolidation`
  como symlink, ou resolvido para fora do workspace. O `FeedbackStore` recusa
  o arquivo do banco como symlink.

## Redação

`sanitize_text(value, limit)` faz quatro coisas, nesta ordem:

1. aplica `privacy.redact_text`;
2. passa `secrets_scanner.has_secrets` no texto redigido;
3. se ainda houver algo sensível, devolve `sensitive=True` e texto vazio;
4. senão, corta no limite.

Os digests são do texto **redigido**: um hash de segredo de baixa entropia
permitiria confirmar o segredo.

## Risco e compatibilidade

- Os testes que montam `SqliteSessionStore` sem `workspace` continuam iguais.
  A linha só fica sem proveniência.
- A alteração no `agent.py` se limita ao bloco do feedback, com o parse do
  motivo, a redação do `FEEDBACK.md` e uma chamada best-effort.
