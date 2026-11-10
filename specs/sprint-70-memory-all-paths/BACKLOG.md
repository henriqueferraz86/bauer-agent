# BACKLOG — Sprint 70

### 1. Bloco de memória dinâmico
Status: concluído
- `bauer/memory_injection.py`: fatos com `scope` do workspace + diretivas
  aprovadas.
- `ContextManager.memory_block`, recalculado a cada `get_payload`, fora de
  `messages`.

### 2. Proveniência por gravação
Status: concluído
- `SqliteSessionStore.save(workspace=...)`.
- `SessionStore.save(workspace=...)` com espelho redigido no `sessions.db`
  (antes do JSONL); `delete` apaga o espelho.

### 3. `serve`/web e `/v1`
Status: concluído
- `_new_context` liga a memória do workspace efetivo.
- `/chat`, `/stream`, `/loop` e `/v1` gravam com workspace.

### 4. Canais
Status: concluído
- `AgentBackend._memory_workspace` (só o projeto vinculado).
- Memória e proveniência a cada turno, nos dois motores (nativo e Agno).

### 5. `bauer agent`
Status: concluído
- As diretivas saem do bloco da abertura e vão para o bloco dinâmico.
- O histórico legado é limpo ao retomar a sessão.

### 6. Testes e gates
Status: concluído
- `tests/test_memory_all_paths.py`, com:
  - web (`/chat` e `/stream`) com dois workspaces e revogação no meio da
    sessão;
  - `/loop`, `/v1` e Telegram (dois projetos, sem vínculo, sessão histórica);
  - Discord e `bauer agent`.
- `tests/test_memory_directives.py` lê o prompt pela fonte nova.
- Gates do plano 066 verdes.

## Pendências

- Feedback (👍/👎) na web, no `/v1` e nos canais não existe. Se for criado,
  deve usar o mesmo `_memory_workspace`/workspace efetivo.
- Discord e grupos do Telegram não têm vínculo de projeto, então continuam sem
  memória de workspace.
