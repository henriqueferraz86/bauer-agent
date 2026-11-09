# BACKLOG — Sprint 65

### 1. Contrato de escopo (`memory_scope.py`)
Status: concluído
- `WorkspaceScope`, id estável, guard de tipo, recusa de `company`/`user`.
- `sanitize_text`: redige antes de cortar e marca o que continua sensível.

### 2. Proveniência no `SqliteSessionStore`
Status: concluído
- Migração idempotente de colunas.
- Gravação do workspace só no INSERT; conflito por troca de workspace.
- Listagem por workspace com fail closed.

### 3. Propagar o workspace em `bauer agent`
Status: concluído
- Dois pontos em `agent_cmd.py` passam `router.workspace`.

### 4. Feedback estruturado
Status: concluído
- `FeedbackStore` com escopo, redação, digest, tools, turno e motivo.
- Handler `/thumbsup|/thumbsdown [motivo]` chama `record_turn_feedback`.
- `FEEDBACK.md` legado redigido.
- `bauer memory feedback list`.

### 5. Testes e gates
Status: concluído
- `tests/test_memory_scope_feedback.py`: escopo, migração, conflito,
  isolamento entre workspaces, redação e loop do agente com cliente fake.
- Gates do plano 065 verdes.
