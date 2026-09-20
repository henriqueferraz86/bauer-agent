# Backlog — Sprint 09

- [x] Criar contrato JSONL versionado e `PluginProcess` com handshake,
  timeout, limite de mensagem e shutdown idempotente.
- [ ] Criar `plugin_worker` mínimo para carregar somente entrypoints validados
  e responder `hello`, `health`, `event` e `shutdown`.
- [ ] Criar `PluginBroker` com política de capability/permissão, estado,
  restart limitado e prevenção de processos duplicados.
- [ ] Integrar hooks gerenciados ao broker, preservando compatibilidade apenas
  com flag explícita.
- [ ] Adicionar testes de falha, timeout, payload inválido, autorização,
  restart e shutdown em `tmp_path`.
- [ ] Atualizar documentação operacional e executar suíte/gates completos.

Status: em andamento na branch `codex/sprint-09-plugin-isolation`.
