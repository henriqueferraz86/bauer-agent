# BACKLOG — Sprint 68

### 1. Fingerprint e regra de promoção
Status: concluído
- Fingerprint v1 (motivo, ou tools + palavras-chave).
- 3 sinais em 2 sessões, decay de 30 dias, avoid/prefer.

### 2. Store e ciclo de vida
Status: concluído
- `directives.json` confinado e atômico, filtro por workspace.
- `approve` (TTL obrigatório, texto sanitizado), `reject`, `revoke`.
- Histórico preservado; re-proposta só com sinais novos.

### 3. Injeção só depois de aprovar
Status: concluído
- `LocalMemoryProvider.system_prompt_block` lê as diretivas ativas a cada
  montagem.

### 4. CLI
Status: concluído
- `bauer memory directives propose|list|show|approve|reject|revoke`.

### 5. Testes, gates e fechamento do plano
Status: concluído
- `tests/test_memory_directives.py`.
- Plano 065 e YAML de arquitetura atualizados.
- Gates do plano 065 verdes.
